"""流水线编排器：固定拓扑 10 步主链路（Sense→Produce→Amplify），Editor 回退 ≤2 轮

设计原则（与 README 呼应）：拓扑固定、行为可配。不做可拖拽 DAG。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.angle_editor import AngleEditorAgent
from app.agents.audience_insight import AudienceInsightAgent
from app.agents.base import RunContext
from app.agents.distributor import DistributorAgent
from app.agents.editor import EditorAgent
from app.agents.fact_checker import FactCheckerAgent
from app.agents.format_adapter import FormatAdapterAgent
from app.agents.researcher import ResearcherAgent
from app.agents.signal_scout import SignalScoutAgent
from app.agents.trend_analyst import TrendAnalystAgent
from app.agents.writer import WriterAgent
from app.config import settings
from app.llm import get_llm
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
        data.update(await FactCheckerAgent()._exec(ctx, data))
        data.update(await EditorAgent()._exec(ctx, data))
    return data, rounds


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
            content = Content(
                id=str(uuid.uuid4()), task_id=task.id, market=market.code,
                language=market.language, status="published",
                brief=data["brief"], title=article["title"], summary=article.get("summary", ""),
                body=article["body"], evidences=data["evidences"],
                formats=data.get("formats", {}), distribution=data.get("distribution", {}),
                quality={"fact_check": data.get("fact_check", {}), **data.get("review", {})},
                decision_log=ctx.decision_log, prompt_versions=ctx.prompt_versions,
                signals=data.get("signals", []),
                is_fallback=any(s.status == "degraded" for s in ctx.spans),
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
