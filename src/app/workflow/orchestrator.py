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

# 目标语言→人类可读名（提升 LLM 翻译任务清晰度，避免 "into ja" 这类歧义）
_LANG_NAME = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean",
              "pt": "Portuguese", "en": "English"}
# 葡语特征：重音字符（英文极少出现，强信号）
_PT_DIAC_RE = re.compile(r'[áàâãéèêíóòôõúçÁÀÂÃÉÈÊÍÓÒÔÕÚÇ]')
# 葡语高频词（仅收录英文里不会作独立词出现的，避免与英文误判；命中 1+ 即视为葡语）
_PT_STOP = {"que", "para", "com", "não", "dos", "das", "uma", "por", "mais",
            "seu", "sua", "foi", "tem", "sobre", "entre", "quando", "da", "de", "em",
            "ao", "pelas", "nesta", "isso", "aq"}


def _is_cjk_lang(language: str) -> bool:
    """中文市场（zh/cn/tw）：CJK 字符属正常正文，不应判为错配。"""
    return (language or '').lower().startswith(('zh', 'cn', 'tw'))


def _target_lang_present(text: str, language: str) -> bool:
    """判断文本是否『命中目标市场语言特征』。

    v2.17 只抓『non-CJK 正文混入中文』，但 JA/KO/BR 的 fallback 产出是英文（无中文字符），
    因此根本没被判错配、没翻译——这就是『对应语言缺失』的真因。
    本函数改为按目标语言的可判定特征识别：
      - zh : 含 CJK 字符
      - ja : 含假名（真日文必含假名，纯汉字串更可能是中文）
      - ko : 含谚文
      - pt : 含葡语重音字符，或命中 2+ 葡语高频虚词
      - en/未知 : 含足够拉丁字母即视为合法（英文兜底稿本就是 en 市场的目标语言）
    """
    text = text or ''
    if not text.strip():
        return False
    lang = (language or 'en').lower()
    if lang.startswith(('zh', 'cn', 'tw')):
        return len(_CJK_RE.findall(text)) >= 1
    if lang.startswith('ja'):
        return (len(_HIRA_RE.findall(text)) + len(_KATA_RE.findall(text))) >= 1
    if lang.startswith('ko'):
        return len(_HANG_RE.findall(text)) >= 1
    if lang.startswith('pt'):
        if _PT_DIAC_RE.search(text):
            return True
        words = set(re.findall(r'[a-zà-ÿ]{2,}', text.lower()))
        return len(words & _PT_STOP) >= 1
    # en / 未知：拉丁字母即视为命中
    return len(re.findall(r'[A-Za-z]', text)) >= 8


def _title_lang_mismatch(title: str, language: str) -> bool:
    """fallback 链路可能不遵守 {{language}} 约束，标题错配目标语言。

    判错配 = 标题非目标语言：非 CJK 市场标题含中文（污染），或标题未命中目标语言特征
    （如 JA/KO/BR 市场拿到英文标题）。CJK 市场用 _target_lang_present 识别（英文标题会被判错配）。
    """
    if not (title or '').strip():
        return False
    lang = (language or 'en').lower()
    # 反向污染：en/pt 等『绝不出现汉字』的市场，标题混入中文 → 错配。
    # 注意排除 ja/ko：日文/韩文标题天然含汉字(kanji/hanja，同处 CJK 码位)，
    # 不能因含汉字就判为中文污染，否则会误杀正确的日文/韩文标题。
    if not _is_cjk_lang(lang) and not lang.startswith(('ja', 'ko')):
        if len(_CJK_RE.findall(title)) >= 1:
            return True
    return not _target_lang_present(title, language)


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
    """判断结构化正文是否错配目标语言（广义：非目标语言即错配）。

    - 保留中文污染防护：non-CJK 正文混显著中文(cjk>=4) → 错配；
    - 其余情况交给 _target_lang_present：JA/KO/BR 市场拿到英文正文（无假名/谚文/葡语特征）
      → 未命中目标语言 → 错配 → 触发全文翻译。
    """
    text = _body_to_text(body)
    if not text.strip():
        return False
    lang = (language or 'en').lower()
    # 反向污染：en/pt 等『绝不出现汉字』的市场，正文混显著中文 → 错配（保留 v2.17 防护）。
    # 同样排除 ja/ko：其正文天然含汉字，不能因含汉字判为中文污染。
    if not _is_cjk_lang(lang) and not lang.startswith(('ja', 'ko')):
        cjk = len(_CJK_RE.findall(text))
        if cjk >= 4:                      # 非 CJK 正文混显著中文 → 错配
            return True
    return not _target_lang_present(text, language)


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


async def _translate_title(ctx: RunContext, title: str, language: str) -> str | None:
    """fallback 标题错配时，LLM 把标题翻译到目标市场语言（best-effort）。"""
    name = _LANG_NAME.get((language or 'en').lower(), language or 'English')
    system = (f"You are a professional translator. Translate the news headline into {name}. "
              "Output ONLY the translated headline text: no quotes, no markdown fence, no commentary.")
    try:
        resp = await ctx.llm.chat(system, title or '', temperature=0.3, max_tokens=200)
        t = (resp.text or '').strip().strip('"').strip("'").strip()
        return t or None
    except Exception:
        return None


async def _translate_text(ctx: RunContext, text: str, language: str) -> str | None:
    """fallback 摘要错配时，LLM 把摘要翻译到目标市场语言（best-effort）。"""
    name = _LANG_NAME.get((language or 'en').lower(), language or 'English')
    system = (f"You are a professional translator. Translate the following text into {name}. "
              "Output ONLY the translated text, no markdown fence, no commentary.")
    try:
        resp = await ctx.llm.chat(system, text or '', temperature=0.3, max_tokens=800)
        t = (resp.text or '').strip()
        return t or None
    except Exception:
        return None


def _localized_topic_title(topic: str, language: str) -> str:
    """标题翻译兜底失败时的本地化派生标题（避免 JA/KO/BR 退化成英文标题）。"""
    t = topic or "AI Content Update"
    lang = (language or 'en').lower()
    if lang.startswith(('zh', 'cn', 'tw')):
        return f"{t}：关键进展"
    if lang.startswith('ja'):
        return f"{t}：最新の動き"
    if lang.startswith('ko'):
        return f"{t}：주요 전개"
    if lang.startswith('pt'):
        return f"{t}: Principais Desenvolvimentos"
    return f"{t}: Key Developments"


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
            summary = article.get("summary", "")
            # fallback 全文语言兜底：fallback 链路（含 Writer L3 规则兜底稿）可能不遵守 {{language}}
            # 约束，产出错配语言。错配判定从 v2.17「只抓 non-CJK 混中文」升级为「是否命中目标语言特征」
            # （ja=假名 / ko=谚文 / pt=葡语重音或虚词 / en=拉丁 / zh=CJK），因此 JA/KO/BR 拿到英文
            # 兜底稿也会被识别并全文翻译至目标市场语言。
            # —— 标题：错配→LLM 翻译至目标语言；失败→本地化派生标题（JA/KO/BR 不退化为英文）。
            # —— 正文：错配→_translate_body 全文（含小标题）翻译至目标语言。
            # —— 摘要：错配→LLM 翻译至目标语言。
            # 任何翻译失败均保留原稿并打 language_guard 标记（供分析中心/校准可见）。
            language_guard: dict = {"body_checked": spans_fallback,
                                    "target_language": market.language}
            if spans_fallback:
                # 标题
                if _title_lang_mismatch(title, market.language):
                    tr = await _translate_title(ctx, title, market.language)
                    if tr:
                        title = tr
                        language_guard["title_fixed"] = True
                    else:
                        topic = (data.get("brief") or {}).get("topic", "")
                        title = _localized_topic_title(topic, market.language)
                        language_guard["title_fixed"] = True
                        language_guard["title_derived"] = True
                # 正文（含小标题）全文翻译
                if _body_lang_mismatch(body, market.language):
                    translated = await _translate_body(ctx, body, market.language)
                    if translated and not _body_lang_mismatch(translated, market.language):
                        body = translated
                        language_guard["body_fixed"] = True
                        language_guard["note"] = f"fallback 正文语言错配，已全文翻译至 {market.language}"
                        ctx.log_decision("orchestrator", "fallback 正文语言错配，已全文翻译至目标语言",
                                         market=market.code, language=market.language)
                    else:
                        language_guard["body_mismatch"] = True
                        language_guard["note"] = f"fallback 正文语言错配，全文翻译兜底失败，保留原稿并标记"
                        ctx.log_decision("orchestrator", "fallback 正文语言错配且全文翻译兜底失败",
                                         market=market.code)
                # 摘要
                if summary and _body_lang_mismatch({"sections": [{"heading": "", "text": summary}]},
                                                   market.language):
                    ts = await _translate_text(ctx, summary, market.language)
                    if ts:
                        summary = ts
                        language_guard["summary_fixed"] = True
            content = Content(
                id=str(uuid.uuid4()), task_id=task.id, market=market.code,
                language=market.language, status="published",
                brief=data["brief"], title=title, summary=summary,
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
