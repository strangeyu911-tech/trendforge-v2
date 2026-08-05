"""评估中心：事件模拟 / 指标总览 / FeedbackAnalyst 报告"""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import RunContext
from app.agents.feedback_analyst import FeedbackAnalystAgent, collect_metrics
from app.llm import get_llm
from app.models import EvalReport, Market, SessionLocal, Task
from app.simulator import simulate_events

router = APIRouter()


class SimulateRequest(BaseModel):
    content_id: str | None = None
    per_content: int = 300


@router.post("/events/simulate")
async def simulate(req: SimulateRequest):
    return await simulate_events(content_id=req.content_id, per_content=req.per_content)


@router.get("/overview")
async def overview(market: str = ""):
    async with SessionLocal() as session:
        return await collect_metrics(session, market=market or None)


@router.post("/run-feedback")
async def run_feedback(market: str = "US"):
    """触发 FeedbackAnalyst：消费数据 → 评估报告"""
    async with SessionLocal() as session:
        m = await session.get(Market, market)
        if not m:
            return {"ok": False, "error": f"未知市场 {market}"}
        task = Task(id=str(uuid.uuid4()), kind="feedback", market=market, status="running")
        session.add(task)
        await session.commit()
        ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=m)
        result = await FeedbackAnalystAgent()._exec(ctx, {})
        report_data = result.get("eval_report", {})
        report = EvalReport(
            quality_avg=report_data.get("quality_avg", 0),
            metrics=report_data.get("metrics", {}),
            findings=report_data.get("findings", []),
            suggestions=report_data.get("suggestions", []),
        )
        session.add(report)
        task.status = "done"
        await ctx.persist()
        await session.commit()
        return {"ok": True, "report_id": report.id, **report_data}


@router.get("/reports")
async def list_reports(limit: int = 10):
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(EvalReport).order_by(EvalReport.created_at.desc()).limit(limit))).scalars().all()
        return {"reports": [{
            "id": r.id, "quality_avg": r.quality_avg, "metrics": r.metrics,
            "findings": r.findings, "suggestions": r.suggestions,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows]}
