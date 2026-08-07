"""M3 闭环实跑：用已采纳的 writer@v3 端到端产出一条新内容，
并演示「revise → 人审打回 → 多轮重写」把漂移率降到极低的全过程。

设计要点：
- 漂移率 = len(drift_sections) / total_sections（由 rag/tcs.score_article 零成本计算）
- 单变量控制：Sense+Research 只跑一次拿真实 brief/证据，多轮 produce 复用，
  唯一变化是 Writer 吃到的 editor_feedback（上一轮 revision_advice + 反漂移硬指令）
- 每轮记录：守门前漂移(作家自身) / 守门后漂移(TopicGuard 修复后) / verdict / 质量 / 人审打回决策
- writer@v3 通过 refresh_overrides 从 DB 加载为覆盖层，全程 Writer 自动用 v3
- 产物落本地 DB（非 Render）+ docs/data/produced（进 git）
- 断点续跑：Phase B 完成后落 D:/tmp/newcontent_checkpoint.json；若 Phase C 崩，设
  RESUME=1 重跑只重做 Phase C，复用同一份 Phase A/B 数据（避免重花 13 分钟 + token）
- 核心文件产物（稿件 MD + 证据 JSON 不含 formats 版）在 Phase C 之前落盘，绝不因 Phase C 崩溃丢失
"""
from __future__ import annotations
import asyncio, json, copy, uuid, datetime, os, traceback

from sqlalchemy import select
from app.models import SessionLocal, Market, Content, Task
from app.agents.base import RunContext
from app.agents.signal_scout import SignalScoutAgent
from app.agents.trend_analyst import TrendAnalystAgent
from app.agents.audience_insight import AudienceInsightAgent
from app.agents.angle_editor import AngleEditorAgent
from app.agents.researcher import ResearcherAgent
from app.agents.writer import WriterAgent
from app.agents.topic_guard import TopicGuardAgent
from app.agents.fact_checker import FactCheckerAgent
from app.agents.editor import EditorAgent
from app.agents.format_adapter import FormatAdapterAgent
from app.agents.distributor import DistributorAgent
from app.llm import get_llm
from app.rag.tcs import score_article
from app.prompts.manager import get_pm
from app.services.prompt_versions import refresh_overrides

OUT_DIR = "D:/workbuddy/Data/2026-07-21-19-08-17/ai-news-system/v2_trendforge/docs/data/produced"
CHECKPOINT = "D:/tmp/newcontent_checkpoint.json"
os.makedirs(OUT_DIR, exist_ok=True)
LOG_PATH = "D:/tmp/newcontent_run.log"
LOG = open(LOG_PATH, "a", encoding="utf-8")
RESUME = os.environ.get("RESUME") == "1"

def log(*a):
    t = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{t}] " + " ".join(str(x) for x in a)
    print(line, flush=True); LOG.write(line + "\n"); LOG.flush()

def drift_rate(report):
    n = len(report.get("sections") or [])
    return round(len(report.get("drift_sections") or []) / n, 3) if n else 0.0

async def safe_exec(agent, ctx, data, retries=2):
    """跑一个 Agent；若末段 span 为 degraded/failed 则就地重试（吞掉的偶发抖动）。"""
    for attempt in range(retries + 1):
        before = len(ctx.spans)
        out = await agent._exec(ctx, data)
        span = ctx.spans[-1] if len(ctx.spans) > before else None
        if span is None or span.status == "ok":
            return out
        if attempt < retries:
            log(f"  ! {agent.name} span={span.status} retry {attempt+1}")
            continue
        return out
    return out

def main_ev_ids(evidences):
    ids = [e["ev_id"] for e in evidences if e.get("is_main") and e.get("ev_id")]
    if ids or not evidences:
        return ids or ["（未标注）"]
    return [e["ev_id"] for e in evidences[:2]]

def build_feedback(review, post_report, evidences):
    adv = (review or {}).get("revision_advice", "")
    drift_idx = post_report.get("drift_sections") or []
    secs = post_report.get("sections") or []
    main_ids = main_ev_ids(evidences)
    parts = []
    if adv:
        parts.append("【上轮总编修改意见】" + adv)
    if drift_idx:
        names = []
        for i in drift_idx:
            if 0 <= i < len(secs):
                names.append(f"第{secs[i]['idx']+1}节《{secs[i].get('heading','')}》")
        parts.append(
            f"【硬性反漂移要求】上轮 TCS={post_report['tcs']}，检出 {', '.join(names)} 脱离主线。"
            f"本稿必须：每节优先引用主干证据 {main_ids}，不得把无关背景文档拼成独立小节；"
            f"若某节无主干证据支撑，宁可删去或合并，绝不留拼盘节。")
    else:
        parts.append("【反漂移】保持每节紧扣选题与角度，持续引用主干证据，避免拼盘。")
    return "\n".join(parts)

def save_md(article, brief, market, last_review, last_post):
    slug = (brief.get("topic") or "newcontent")[:24].replace(" ", "_").replace("/", "_")
    md_path = os.path.join(OUT_DIR, f"ARTICLE_FINAL_{slug}.md")
    secs_md = (article.get("body") or {}).get("sections") or []
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {article.get('title','')}\n\n")
        f.write(f"> 市场 {market.code} · 采用 Prompt writer@{get_pm().active_version('writer')} · "
                f"最终漂移率 {drift_rate(last_post)} · verdict {(last_review or {}).get('verdict')}\n\n")
        f.write(f"**摘要**：{article.get('summary','')}\n\n")
        for s in secs_md:
            f.write(f"## {s.get('heading','')}\n\n{s.get('text','')}\n\n")
    log(f"== 最终稿件 MD：{md_path}")
    return md_path

def save_evidence(evidence, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    log(f"== 证据已落盘：{path}")

async def phase_c(ctx, session, task, market, checkpoint_data, brief, evidences,
                 final_article, last_review, last_post, prev_feedback, total_cost, total_dur,
                 decision_log, prompt_versions, is_fallback, meta_extra):
    """Amplify（多形态 + 分发）→ 落盘（含 formats）→ 本地 DB 持久化。"""
    log("== PHASE C: Amplify ==")
    final = {"brief": brief, "evidences": evidences, "article": final_article,
             "fact_check": checkpoint_data.get("fact_check"), "review": last_review,
             "topic_guard": last_post, "editor_feedback": prev_feedback}
    final.update(await safe_exec(FormatAdapterAgent(), ctx, final))
    final.update(await safe_exec(DistributorAgent(), ctx, final))

    # 在 DB 持久化之前就写出完整证据（含 formats），确保 Phase C 任何崩溃都不丢产物
    evidence = meta_extra["evidence"]
    evidence["final_article"]["formats"] = final.get("formats")
    evidence["final_article"]["distribution"] = final.get("distribution")
    save_evidence(evidence, meta_extra["ev_path"])

    # 本地 DB 持久化（非 Render）
    content = Content(
        id=str(uuid.uuid4()), task_id=task.id, market=market.code,
        language=market.language, status="published",
        brief=brief, title=final_article.get("title", "(无标题)"),
        summary=final_article.get("summary", ""), body=final_article.get("body", {}),
        evidences=evidences,
        formats=final.get("formats", {}), distribution=final.get("distribution", {}),
        quality={"fact_check": final.get("fact_check", {}), **(last_review or {}),
                 "topic_guard": last_post, "evidence_guard": final.get("evidence_guard", {})},
        decision_log=decision_log, prompt_versions=prompt_versions,
        signals=final.get("signals", []),
        is_fallback=is_fallback,
    )
    session.add(content)
    task.status = "done"; task.progress = "done"
    task.output = {"content_id": content.id, "title": content.title,
                   "verdict": (last_review or {}).get("verdict"),
                   "final_drift": drift_rate(last_post),
                   "rounds": meta_extra["rounds_count"]}
    task.finished_at = datetime.datetime.now(datetime.timezone.utc)
    await ctx.persist()
    # 用 Phase A/B 真实总账回填（ctx 仅含 Phase C span，否则成本被低估）
    task.total_cost_cny = total_cost
    task.total_duration_ms = total_dur
    await session.commit()
    log(f"== 已落本地 DB：content_id={content.id} 最终漂移率={drift_rate(last_post)} ==")
    return content, final

async def main():
    market_code = "US"
    MAX_ROUNDS = 4
    async with SessionLocal() as session:
        n = await refresh_overrides(session)
        log(f"refresh_overrides: {n} 个覆盖层生效；writer active_version = {get_pm().active_version('writer')}")
        market = await session.get(Market, market_code)

        # ---- 断点续跑：复用 Phase A/B checkpoint，只跑 Phase C ----
        if RESUME and os.path.exists(CHECKPOINT):
            log("== RESUME 模式：加载 Phase A/B checkpoint，跳过 Sense/Research/多轮 produce ==")
            with open(CHECKPOINT, "r", encoding="utf-8") as f:
                ck = json.load(f)
            task = Task(id=ck["task_id"] or str(uuid.uuid4()), kind="pipeline",
                        market=market_code, status="running",
                        input={"market": market_code, "demo": "m3-closed-loop-live", "resume": True})
            session.add(task); await session.commit()
            ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=market)
            ctx.decision_log = ck["decision_log"]; ctx.prompt_versions = ck["prompt_versions"]
            ctx.review_rounds = ck["rounds_count"]
            content, final = await phase_c(
                ctx, session, task, market, ck, ck["brief"], ck["evidences"],
                ck["article"], ck["last_review"], ck["last_post"], ck["prev_feedback"],
                ck["total_cost_cny"], ck["total_duration_ms"], ck["decision_log"],
                ck["prompt_versions"], ck["is_fallback"],
                {"evidence": ck["evidence"], "ev_path": ck["ev_path"], "rounds_count": ck["rounds_count"]})
            final_article = ck["article"]
            log("DONE OK (resume)")
            return content

        # ---- 全新运行 ----
        task = Task(id=str(uuid.uuid4()), kind="pipeline", market=market_code,
                    status="running", input={"market": market_code, "demo": "m3-closed-loop-live"})
        session.add(task); await session.commit()
        ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=market)

        # ---------- PHASE A: Sense + Research（一次性，拿真实 brief/证据）----------
        log("== PHASE A: Sense → Research ==")
        data = {"_market": market_code, "rejected_topics": []}
        seed_topic = os.environ.get("SEED_TOPIC")
        seed_path = os.environ.get("SEED_FILE", "D:/tmp/seed_topic.json")
        if not seed_topic and os.path.exists(seed_path):
            try:
                import json as _json
                with open(seed_path, encoding="utf-8") as _f:
                    sd = _json.load(_f)
                seed_topic = sd.get("topic")
                os.environ.setdefault("SEED_ANGLE", sd.get("angle", ""))
                os.environ.setdefault("SEED_HOOK", sd.get("hook", ""))
                os.environ.setdefault("SEED_AUDIENCE", sd.get("audience", ""))
                log(f"  (SEED_FILE 读取) {seed_path}")
            except Exception as _e:
                log(f"  SEED_FILE 读取失败: {_e}")
        for A in (SignalScoutAgent(), TrendAnalystAgent(), AudienceInsightAgent(),
                  AngleEditorAgent()):
            data.update(await safe_exec(A, ctx, data))
        if seed_topic:
            # 种子选题：复用已知会出现漂移轨迹的选题，证据仍由 Researcher 实时检索
            log(f"  (SEED_TOPIC 覆盖) topic={seed_topic!r}")
            data["brief"] = {
                "topic": seed_topic,
                "angle": os.environ.get("SEED_ANGLE", data.get("brief", {}).get("angle", "")),
                "hook": os.environ.get("SEED_HOOK", ""),
                "audience": os.environ.get("SEED_AUDIENCE", ""),
            }
        # Researcher 最后跑，基于（可能已被种子覆盖的）brief 检索真实证据
        data.update(await safe_exec(ResearcherAgent(), ctx, data))
        brief = data.get("brief") or {}
        evidences = data.get("evidences") or []
        log(f"topic={brief.get('topic','')!r} angle={brief.get('angle','')!r} evidences={len(evidences)}")
        if not brief.get("topic") or not evidences:
            raise RuntimeError("Sense/Research 未产出可用 brief/证据，终止")

        # ---------- PHASE B: 多轮 produce（单变量：editor_feedback）----------
        log("== PHASE B: 多轮 produce（闭环降漂移）==")
        rounds = []
        prev_feedback = ""
        last_review = None
        last_post = None
        for i in range(1, MAX_ROUNDS + 1):
            span0 = len(ctx.spans)
            rd = {"brief": brief, "evidences": evidences, "editor_feedback": prev_feedback}
            rd.update(await safe_exec(WriterAgent(), ctx, rd))
            article_after_writer = copy.deepcopy(rd.get("article", {}))
            pre = score_article(article_after_writer, brief, evidences)
            rd.update(await safe_exec(TopicGuardAgent(), ctx, rd))
            article_after_guard = rd.get("article", {})
            post = score_article(article_after_guard, brief, evidences)
            rd.update(await safe_exec(FactCheckerAgent(), ctx, rd))
            rd.update(await safe_exec(EditorAgent(), ctx, rd))
            review = rd.get("review", {})
            last_review, last_post = review, post

            pre_rate, post_rate = drift_rate(pre), drift_rate(post)
            rcost = round(sum(s.cost_cny for s in ctx.spans[span0:]), 4)
            rdur = sum(s.duration_ms for s in ctx.spans[span0:])
            secs = (article_after_guard.get("body") or {}).get("sections") or []
            sec_view = [{"idx": k, "heading": s.get("heading", ""),
                         "drift": (k in (post.get("drift_sections") or [])),
                         "text": (s.get("text", "")[:600])} for k, s in enumerate(secs)]
            human = ("人审：漂移率已为 0 且守门达标 → 通过，进入发布"
                     if post_rate == 0 and pre_rate == 0
                     else f"人审：守门后漂移率 {post_rate}，守门前 {pre_rate} → 打回，要求重写降漂移")
            rounds.append({
                "round": i, "pre_guard_drift": pre_rate, "post_guard_drift": post_rate,
                "pre_tcs": pre.get("tcs"), "post_tcs": post.get("tcs"),
                "pre_drift_sections": pre.get("drift_sections"),
                "post_drift_sections": post.get("drift_sections"),
                "post_reason": post.get("reason"),
                "verdict": review.get("verdict"), "quality_avg": review.get("avg"),
                "scores": review.get("scores"), "revision_advice": review.get("revision_advice"),
                "comments": review.get("comments"),
                "human_decision": human,
                "cost_cny": rcost, "duration_ms": rdur,
                "title": (article_after_guard.get("title") or ""),
                "summary": (article_after_guard.get("summary") or ""),
                "sections": sec_view,
            })
            log(f"  Round {i}: 守门前漂移={pre_rate} 守门后漂移={post_rate} "
                f"verdict={review.get('verdict')} 质量={review.get('avg')} ¥{rcost}")
            prev_feedback = build_feedback(review, post, evidences)
            if i >= 2 and post_rate == 0 and pre_rate == 0:
                log(f"  → 漂移率已达极低（0），第 {i} 轮收敛，停止")
                break

        final_article = rd.get("article", {})
        # 先写稿件 MD（Phase C 不改主稿正文）
        save_md(final_article, brief, market, last_review, last_post)

        # 证据骨架（formats 待 Phase C 填补）
        evidences_view = [{"ev_id": e.get("ev_id"), "doc_title": e.get("doc_title"),
                           "is_main": e.get("is_main"), "text": (e.get("text") or "")[:300]}
                          for e in evidences]
        ev_path = os.path.join(OUT_DIR, "RUN_EVIDENCE_newcontent_2026-08-07.json")
        evidence = {
            "meta": {
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "market": market.code, "writer_version": get_pm().active_version("writer"),
                "total_rounds": len(rounds), "final_drift": drift_rate(last_post),
                "final_verdict": (last_review or {}).get("verdict"),
                "total_cost_cny": ctx.total_cost_cny, "total_duration_ms": ctx.total_duration_ms,
                "task_id": task.id, "content_id": None,
                "resume_safe": True,
            },
            "sense": {
                "topic": brief.get("topic"), "angle": brief.get("angle"),
                "hook": brief.get("hook"), "audience": brief.get("audience"),
                "evidences": evidences_view,
            },
            "rounds": rounds,
            "drift_series": {
                "pre_guard": [r["pre_guard_drift"] for r in rounds],
                "post_guard": [r["post_guard_drift"] for r in rounds],
            },
            "final_article": {
                "title": final_article.get("title"), "summary": final_article.get("summary"),
                "sections": [(final_article.get("body") or {}).get("sections") or []],
                "formats": None, "distribution": None,
            },
            "prompt_versions": ctx.prompt_versions,
            "is_fallback": any(s.status == "degraded" for s in ctx.spans),
        }
        # Phase B 后立即落盘（不含 formats 的版本），保证核心产物不丢
        save_evidence(evidence, ev_path)

        # 写 checkpoint（含 Phase C 所需全部数据），供断点续跑
        ck = {
            "task_id": task.id, "brief": brief, "evidences": evidences,
            "article": final_article, "fact_check": rd.get("fact_check"),
            "last_review": last_review, "last_post": last_post,
            "prev_feedback": prev_feedback,
            "decision_log": ctx.decision_log, "prompt_versions": ctx.prompt_versions,
            "rounds_count": len(rounds),
            "total_cost_cny": ctx.total_cost_cny, "total_duration_ms": ctx.total_duration_ms,
            "is_fallback": any(s.status == "degraded" for s in ctx.spans),
            "evidence": evidence, "ev_path": ev_path,
        }
        with open(CHECKPOINT, "w", encoding="utf-8") as f:
            json.dump(ck, f, ensure_ascii=False, indent=2)
        log(f"== CHECKPOINT 已落盘（Phase B 完成，可断点续跑）：{CHECKPOINT}")

        # ---------- PHASE C: Amplify + 落库 ----------
        content, final = await phase_c(
            ctx, session, task, market, ck, brief, evidences, final_article,
            last_review, last_post, prev_feedback, ctx.total_cost_cny, ctx.total_duration_ms,
            ctx.decision_log, ctx.prompt_versions,
            any(s.status == "degraded" for s in ctx.spans),
            {"evidence": evidence, "ev_path": ev_path, "rounds_count": len(rounds)})
        # 回填 content_id 到证据 meta
        evidence["meta"]["content_id"] = content.id
        save_evidence(evidence, ev_path)
        log("DONE OK")
        return content

if __name__ == "__main__":
    try:
        asyncio.run(main())
        log("DONE OK")
    except Exception as e:
        log("FATAL", traceback.format_exc())
        raise
