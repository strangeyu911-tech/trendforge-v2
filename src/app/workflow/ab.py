"""M3 A/B：同一选题用两个 Prompt 版本各跑一次 produce 段 → 仿真 → 指标对比

设计：复用现有 Produce 段 Agent（Researcher→Writer→TopicGuard→FactChecker→Editor），
通过 PromptManager 覆盖层把目标模板临时切到指定版本，两次运行共享同一 brief/证据，
从而隔离「Prompt 版本」这一单一变量。每次运行落库 Content + Task（含 prompt_versions 审计）。

A/B 不只交付「多版本改写」能力，更把「人审采纳新 Prompt 之前，先小规模对比验证」变成可执行动作。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.agents.base import RunContext
from app.agents.editor import EditorAgent
from app.agents.fact_checker import FactCheckerAgent
from app.agents.researcher import ResearcherAgent
from app.agents.topic_guard import TopicGuardAgent
from app.agents.writer import WriterAgent
from app.llm import get_llm
from app.models import Content, ContentEvent, Market, PromptRecord, SessionLocal, Task
from app.prompts.manager import get_pm
from app.simulator import simulate_events
from app.workflow.orchestrator import run_revise_rounds


def _default_brief(angle: str, topic: str = "") -> dict:
    t = topic or angle
    return {"topic": t, "angle": angle, "hook": "", "audience": "",
            "style": "deep_dive", "avoid": [], "keywords": []}


async def _produce_once(session, market_code: str, brief: dict,
                        override_name: str, override_content: str,
                        override_version: str, label: str) -> dict:
    market = await session.get(Market, market_code)
    if not market:
        raise ValueError(f"未知市场: {market_code}")
    task = Task(id=str(uuid.uuid4()), kind="ab", market=market_code, status="running",
                input={"ab": label, "market": market_code, "template": override_name})
    session.add(task)
    await session.commit()
    ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=market)
    data: dict = {"_market": market_code, "rejected_topics": [], "brief": brief}
    prev = get_pm()._overrides.get(override_name)  # 暂存，运行后还原
    get_pm().set_override(override_name, override_content, override_version)
    try:
        data.update(await ResearcherAgent()._exec(ctx, data))
        data.update(await WriterAgent()._exec(ctx, data))
        data.update(await TopicGuardAgent()._exec(ctx, data))
        data.update(await FactCheckerAgent()._exec(ctx, data))
        data.update(await EditorAgent()._exec(ctx, data))
        data, _ = await run_revise_rounds(ctx, data)
        verdict = (data.get("review") or {}).get("verdict", "pass")
        article = data.get("article", {})
        content = Content(
            id=str(uuid.uuid4()), task_id=task.id, market=market.code,
            language=market.language,
            status="published" if verdict != "reject" else "rejected",
            brief=brief, title=article.get("title", "(无标题)"),
            summary=article.get("summary", ""), body=article.get("body", {}),
            evidences=data.get("evidences", []),
            quality={"fact_check": data.get("fact_check", {}), **(data.get("review") or {}),
                     "topic_guard": data.get("topic_guard", {}),
                     "evidence_guard": data.get("evidence_guard", {})},
            decision_log=ctx.decision_log, prompt_versions=ctx.prompt_versions,
            signals=data.get("signals", []),
            is_fallback=any(s.status == "degraded" for s in ctx.spans),
        )
        session.add(content)
        task.status = "done"
        task.progress = "done"
        task.output = {"content_id": content.id, "verdict": verdict,
                       "quality_avg": (data.get("review") or {}).get("avg", 0)}
        task.finished_at = datetime.utcnow()
        await ctx.persist()
        await session.commit()
        return {"content_id": content.id, "task_id": task.id, "verdict": verdict,
                "quality": content.quality, "prompt_versions": ctx.prompt_versions}
    finally:
        if prev is None:
            get_pm().clear_override(override_name)
        else:
            get_pm()._overrides[override_name] = prev


async def _metrics(session, content_id: str) -> dict:
    c = await session.get(Content, content_id)
    rows = (await session.execute(
        select(ContentEvent.event_type, func.count())
        .where(ContentEvent.content_id == content_id)
        .group_by(ContentEvent.event_type))).all()
    m = {t: n for t, n in rows}
    exposed = m.get("exposed", 0)
    clicked = m.get("clicked", 0)
    task = (await session.execute(
        select(Task).where(Task.id == c.task_id))).scalars().first() if c else None
    return {
        "quality_avg": (c.quality or {}).get("avg", 0) if c else 0,
        "exposed": exposed, "clicked": clicked,
        "ctr": round(clicked / exposed, 3) if exposed else 0.0,
        "cost_cny": round(task.total_cost_cny, 4) if task else 0.0,
    }


async def run_ab(market_code: str, template_name: str, v1_id: int, v2_id: int,
                 brief: dict | None = None, angle: str = "", topic: str = "",
                 per_content: int = 300) -> dict:
    """对两个 Prompt 版本各跑一次 produce 段并对比。返回 {v1, v2, delta}。"""
    async with SessionLocal() as session:
        v1 = await session.get(PromptRecord, v1_id)
        v2 = await session.get(PromptRecord, v2_id)
        if not v1 or not v2:
            raise ValueError("版本不存在")
        if not brief:
            brief = _default_brief(angle or topic, topic)
        r1 = await _produce_once(session, market_code, brief, template_name,
                                 v1.content, v1.version, "v1")
        r2 = await _produce_once(session, market_code, brief, template_name,
                                 v2.content, v2.version, "v2")
        # 仿真（校准参数来自 M1 真实信号；种子库无信号则走基线）
        await simulate_events(content_id=r1["content_id"], per_content=per_content)
        await simulate_events(content_id=r2["content_id"], per_content=per_content)
        m1 = await _metrics(session, r1["content_id"])
        m2 = await _metrics(session, r2["content_id"])
        # 每张表独立 session 提交，重查确保指标落库
        async with SessionLocal() as s2:
            m1 = await _metrics(s2, r1["content_id"])
            m2 = await _metrics(s2, r2["content_id"])
        delta = {
            "quality_avg": round(m2["quality_avg"] - m1["quality_avg"], 3),
            "ctr": round(m2["ctr"] - m1["ctr"], 3),
            "cost_cny": round(m2["cost_cny"] - m1["cost_cny"], 4),
        }
        return {
            "template": template_name,
            "v1": {**r1, **m1, "version": v1.version},
            "v2": {**r2, **m2, "version": v2.version},
            "delta": delta,
        }
