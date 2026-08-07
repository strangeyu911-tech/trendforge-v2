"""评估中心：事件模拟 / 指标总览 / FeedbackAnalyst 报告"""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import RunContext
from app.agents.feedback_analyst import FeedbackAnalystAgent, collect_metrics
from app.analytics import build_dashboard
from app.llm import get_llm
from app.models import EvalReport, Market, SessionLocal, Task
from app.services.prompt_versions import persist_structured_suggestions
from app.simulator import compute_calibration, simulate_events

router = APIRouter()


class SimulateRequest(BaseModel):
    content_id: str | None = None
    per_content: int = 300


@router.post("/events/simulate")
async def simulate(req: SimulateRequest):
    # 返回含 calibrated_from 元信息：仿真锚定在多少真实信号上
    return await simulate_events(content_id=req.content_id, per_content=req.per_content)


@router.get("/center")
async def center():
    """M2 分析中心：返回所有图表描述符（含真实执行的 SQL 原文 + 数据）"""
    async with SessionLocal() as session:
        specs = await build_dashboard(session)
    return {"charts": specs, "generated_at": _now_iso()}


@router.get("/calibration")
async def calibration():
    """当前仿真器校准状态：是否已用 M1 真实信号分布拟合"""
    async with SessionLocal() as session:
        cal = await compute_calibration(session)
    return {"calibrated": bool(cal), "calibration": cal}


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


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
        # M3：把结构化迭代建议落库为 pending 的 PromptSuggestion（待人审闸门采纳）
        suggestion_ids = await persist_structured_suggestions(
            session, report_data.get("structured_suggestions", []), market=market)
        task.status = "done"
        await ctx.persist()
        await session.commit()
        return {"ok": True, "report_id": report.id,
                "suggestion_ids": suggestion_ids, **report_data}


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
