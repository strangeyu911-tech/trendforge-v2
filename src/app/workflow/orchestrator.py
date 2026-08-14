"""流水线编排器：固定拓扑 10 步主链路（Sense→Produce→Amplify），Editor 回退 ≤2 轮

设计原则（与 README 呼应）：拓扑固定、行为可配。不做可拖拽 DAG。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.angle_editor import AngleEditorAgent
from app.agents.audience_insight import AudienceInsightAgent
from app.agents.base import RunContext, clean_ev
from app.agents.distributor import DistributorAgent
from app.agents.editor import EditorAgent
from app.agents.fact_checker import FactCheckerAgent
from app.agents.format_adapter import FormatAdapterAgent
from app.agents.researcher import ResearcherAgent
from app.agents.signal_scout import SignalScoutAgent
from app.agents.topic_guard import TopicGuardAgent
from app.agents.trend_analyst import TrendAnalystAgent
from app.agents.writer import WriterAgent
from app.config import settings
from app.llm import extract_json, get_llm
from app.models import BadCase, Content, Market, SessionLocal, Task


class PipelineRejected(Exception):
    pass


async def run_revise_rounds(ctx: RunContext, data: dict) -> tuple[dict, int]:
    """重跑 Produce 段子链做修订（Writer→FactChecker→Editor），沿用生成时回退逻辑。

    data 需含：brief / evidences / article / fact_check / review。
    返回 (更新后的 data, 实际执行的修订轮数)。
    """
    rounds = 0
    while data.get("review", {}).get("verdict") == "revise" and ctx.review_rounds < settings.max_review_rounds:
        ctx.review_rounds += 1
        rounds += 1
        rewrite_inputs = dict(data)
        rewrite_inputs["editor_feedback"] = data["review"].get("revision_advice", "")
        data.update(await WriterAgent()._exec(ctx, rewrite_inputs))
        data.update(await TopicGuardAgent()._exec(ctx, data))
        data.update(await FactCheckerAgent()._exec(ctx, data))
        data.update(await EditorAgent()._exec(ctx, data))
    return data, rounds


_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_HIRA_RE = re.compile(r'[\u3040-\u309f]')   # 平假名
_KATA_RE = re.compile(r'[\u30a0-\u30ff]')   # 片假名
_HANG_RE = re.compile(r'[\uac00-\ud7a3]')   # 谚文


def _is_cjk_lang(language: str) -> bool:
    """中文市场（zh/cn/tw）：CJK 字符属正常正文，不应判为错配。"""
    return (language or '').lower().startswith(('zh', 'cn', 'tw'))


def _title_lang_mismatch(title: str, language: str) -> bool:
    """fallback 链路可能不遵守 {{language}} 约束，标题混入中文。

    任何 non-CJK 市场标题含中文字符即判错配（第三条问题）。
    同样需用假名/谚文区分『真日文/真韩文标题(含汉字)』与『误入的中文标题』，
    否则会误伤 JP/KO 市场正确的日文/韩文标题。
    """
    if _is_cjk_lang(language):
        return False
    t = title or ''
    cjk = len(_CJK_RE.findall(t))
    if cjk == 0:
        return False
    lang = (language or '').lower()
    if lang.startswith('ja'):
        kana = len(_HIRA_RE.findall(t)) + len(_KATA_RE.findall(t))
        return kana == 0                      # 有汉字无假名 → 实为中文标题
    if lang.startswith('ko'):
        return len(_HANG_RE.findall(t)) == 0  # 有汉字无谚文 → 实为中文标题
    return True                               # en/pt 等：标题不应出现中文


def _body_to_text(body) -> str:
    """把结构化正文（{sections:[{heading,text}]} 或等价 JSON 串）摊平成待检测文本。"""
    if isinstance(body, dict):
        return " ".join(
            f"{s.get('heading', '')} {s.get('text', '')}"
            for s in body.get('sections', []) if isinstance(s, dict)
        )
    if isinstance(body, str):
        try:
            return _body_to_text(json.loads(body))
        except Exception:
            return body
    return str(body)


def _body_lang_mismatch(body, language: str) -> bool:
    """non-CJK 市场正文若混入中文（且缺目标语言应有的假名/谚文），判语言错配。

    关键边界：日文正文本身含汉字(CJK)、韩文含汉字，需用假名/谚文区分
    『真日文/真韩文』与『误入的中文』，否则会误伤 JP/KO 市场正确内容。
    """
    if _is_cjk_lang(language):
        return False  # 中文市场，CJK 正常
    text = _body_to_text(body)
    cjk = len(_CJK_RE.findall(text))
    if cjk == 0:
        return False
    lang = (language or '').lower()
    if lang.startswith('ja'):
        kana = len(_HIRA_RE.findall(text)) + len(_KATA_RE.findall(text))
        return kana == 0 and cjk >= 4          # 有汉字无假名 → 实为中文
    if lang.startswith('ko'):
        return len(_HANG_RE.findall(text)) == 0 and cjk >= 4   # 有汉字无谚文 → 实为中文
    return cjk >= 4                             # en/pt 等：正文不应出现中文


async def _translate_body(ctx: RunContext, body, language: str) -> dict | None:
    """fallback 正文语言错配时，用 LLM 把结构化正文翻译到目标市场语言（best-effort）。

    输入 body 为 {sections:[{heading,text}]} 或其 JSON 串；输出保持同结构。
    翻译失败/结构不合法时返回 None，由调用方决定保留原稿或标记。
    """
    src = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
    system = (
        f"You are a professional translator. Translate the following news article into {language}. "
        "The input is a JSON object with a top-level 'sections' array; each element has 'heading' and 'text' keys. "
        "Translate the values of 'heading' and 'text' into the target language. "
        "Preserve the exact JSON structure and keys; keep any inline [ev_N] citation markers unchanged. "
        "Output ONLY valid JSON (no markdown fence, no commentary)."
    )
    try:
        resp = await ctx.llm.chat(system, src, temperature=0.3, max_tokens=8000)
        translated = extract_json(resp.text)
    except Exception:
        return None
    if not isinstance(translated, dict) or not isinstance(translated.get("sections"), list):
        return None
    sections = [
        {"heading": str(s.get("heading", "")), "text": str(s.get("text", ""))}
        for s in translated["sections"]
        if isinstance(s, dict) and (s.get("heading") or s.get("text"))
    ]
    if not sections:
        return None
    return {"sections": sections}


async def run_pipeline(market_code: str) -> dict:
    """端到端跑一次供给流水线，返回 {task_id, content_id}"""
    async with SessionLocal() as session:
        market = await session.get(Market, market_code)
        if not market:
            raise ValueError(f"未知市场: {market_code}")
        task = Task(id=str(uuid.uuid4()), kind="pipeline", market=market_code,
                    status="running", input={"market": market_code})
        session.add(task)
        await session.commit()

        ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=market)
        data: dict = {"_market": market_code, "rejected_topics": []}
        try:
            # 主编 reject 自愈：换题重试一次（选题判断是概率事件，重试是系统设计而非碰运气）
            for attempt in range(2):
                try:
                    # ---- SENSE ----
                    data.update(await SignalScoutAgent()._exec(ctx, data))
                    data.update(await TrendAnalystAgent()._exec(ctx, data))
                    data.update(await AudienceInsightAgent()._exec(ctx, data))
                    data.update(await AngleEditorAgent()._exec(ctx, data))
                    # ---- PRODUCE ----
                    data.update(await ResearcherAgent()._exec(ctx, data))
                    if not data.get("evidences"):
                        raise PipelineRejected("无证据支撑，终止供给（拒绝无米之炊）")
                    data.update(await WriterAgent()._exec(ctx, data))
                    # 主题一致性硬闸：在 FactChecker/Editor 烧钱之前拦截漂移，定点重写
                    data.update(await TopicGuardAgent()._exec(ctx, data))
                    data.update(await FactCheckerAgent()._exec(ctx, data))
                    data.update(await EditorAgent()._exec(ctx, data))
                    # Editor 回退循环
                    data, _ = await run_revise_rounds(ctx, data)
                    if data["review"]["verdict"] == "reject":
                        raise PipelineRejected(f"总编 reject：{data['review'].get('comments', '')[:80]}")
                    break  # pass，跳出重试循环
                except PipelineRejected:
                    if attempt == 1:
                        raise
                    data["rejected_topics"].append(data.get("brief", {}).get("topic", ""))
                    ctx.log_decision("orchestrator", "选题/成稿被否决，换题重试一次",
                                     rejected=data["rejected_topics"])
            # 被否决的尝试记入 BadCase Center（质量治理资产）
            if data["rejected_topics"]:
                session.add(BadCase(
                    content_id="", category="Q",
                    title=data["rejected_topics"][0],
                    root_cause="总编 reject（首次尝试），已自动换题重试",
                    fix_action="AngleEditor 避开已否决选题，Researcher 启用类目一致性过滤",
                    status="auto_recovered",
                ))
            # ---- AMPLIFY ----
            data.update(await FormatAdapterAgent()._exec(ctx, data))
            data.update(await DistributorAgent()._exec(ctx, data))

            # ---- 落库 ----
            article = data["article"]
            spans_fallback = any(s.status == "degraded" for s in ctx.spans)
            title = article["title"]
            body = article["body"]
            # fallback 内容语言兜底：fallback 链路（含 Writer L3 规则兜底稿）可能不遵守
            # {{language}} 约束，产出错配语言（典型：non-CJK 市场正文/标题混入中文）。
            # —— 标题：non-CJK 市场标题含中文 → 用选题派生英文标题（第三条问题）
            # —— 正文：non-CJK 市场正文含显著中文且缺目标语言假名/谚文 → LLM 翻译兜底至目标语言；
            #          翻译失败则保留原稿并打 language_guard 标记（供分析中心/校准可见）。
            language_guard: dict = {"body_checked": spans_fallback}
            if spans_fallback and _title_lang_mismatch(title, market.language):
                topic = (data.get("brief") or {}).get("topic", "")
                title = f"{topic}: Key Developments" if topic else "AI Content Update"
                language_guard["title_fixed"] = True
            if spans_fallback and _body_lang_mismatch(body, market.language):
                translated = await _translate_body(ctx, body, market.language)
                if translated and not _body_lang_mismatch(translated, market.language):
                    body = translated
                    language_guard["body_fixed"] = True
                    language_guard["note"] = "fallback 正文语言错配，已翻译至目标市场语言"
                    ctx.log_decision("orchestrator", "fallback 正文语言错配，已翻译至目标语言",
                                     market=market.code, language=market.language)
                else:
                    language_guard["body_mismatch"] = True
                    language_guard["note"] = "fallback 正文语言错配，翻译兜底失败，保留原稿并标记"
                    ctx.log_decision("orchestrator", "fallback 正文语言错配且翻译兜底失败",
                                     market=market.code)
            content = Content(
                id=str(uuid.uuid4()), task_id=task.id, market=market.code,
                language=market.language, status="published",
                brief=data["brief"], title=title, summary=article.get("summary", ""),
                body=clean_ev(body), evidences=data["evidences"],
                formats=data.get("formats", {}), distribution=data.get("distribution", {}),
                quality={"fact_check": data.get("fact_check", {}), **data.get("review", {}),
                         "topic_guard": data.get("topic_guard", {}),
                         "evidence_guard": data.get("evidence_guard", {}),
                         "language_guard": language_guard},
                decision_log=ctx.decision_log, prompt_versions=ctx.prompt_versions,
                signals=data.get("signals", []),
                is_fallback=spans_fallback,
            )
            session.add(content)
            task.status = "done"
            task.progress = "done"
            task.output = {"content_id": content.id, "title": content.title,
                           "verdict": data["review"]["verdict"],
                           "quality_avg": data["review"].get("avg", 0)}
            task.finished_at = datetime.utcnow()
            await ctx.persist()
            await session.commit()
            return {"task_id": task.id, "content_id": content.id}
        except PipelineRejected as pr:
            # 被总编/无证据否决：保留成稿用于审计与漂移评分（不发布）
            try:
                article = data.get("article", {})
                content = Content(
                    id=str(uuid.uuid4()), task_id=task.id, market=market.code,
                    language=market.language, status="rejected",
                    brief=data.get("brief"), title=article.get("title", "(无标题)"),
                    summary=article.get("summary", ""),
                    body=clean_ev(article.get("body")), evidences=data.get("evidences"),
                    quality={"fact_check": data.get("fact_check", {}), **data.get("review", {}),
                             "topic_guard": data.get("topic_guard", {}),
                             "evidence_guard": data.get("evidence_guard", {})},
                    decision_log=ctx.decision_log, prompt_versions=ctx.prompt_versions,
                    signals=data.get("signals", []),
                    is_fallback=any(s.status == "degraded" for s in ctx.spans),
                )
                session.add(content)
            except Exception:
                pass
            task.status = "rejected"
            task.progress = "rejected"
            task.error = str(pr)[:500]
            task.finished_at = datetime.utcnow()
            await ctx.persist()
            await session.commit()
            raise
        except Exception as e:
            task.status = "failed"
            task.progress = "failed"
            task.error = str(e)[:500]
            task.finished_at = datetime.utcnow()
            await ctx.persist()
            await session.commit()
            raise


async def get_trace(session: AsyncSession, task_id: str) -> dict:
    from app.models import TaskSpan
    task = await session.get(Task, task_id)
    if not task:
        return {}
    spans = (await session.execute(
        select(TaskSpan).where(TaskSpan.task_id == task_id)
        .order_by(TaskSpan.id))).scalars().all()
    return {
        "task": {
            "id": task.id, "market": task.market, "status": task.status,
            "total_duration_ms": task.total_duration_ms,
            "total_cost_cny": task.total_cost_cny,
            "review_rounds": task.review_rounds, "error": task.error,
            "created_at": task.created_at.isoformat() if task.created_at else "",
        },
        "spans": [{
            "agent": s.agent, "status": s.status, "model": s.model,
            "tokens_in": s.tokens_in, "tokens_out": s.tokens_out,
            "cost_cny": s.cost_cny, "duration_ms": s.duration_ms,
            "warnings": s.warnings, "decision_reason": s.decision_reason,
        } for s in spans],
        "decision_log": task.decision_log or {},
        "prompt_versions": task.prompt_versions or {},
    }
