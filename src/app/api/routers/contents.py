"""内容：列表 / 详情 / Trace"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.models import Content, SessionLocal
from app.workflow.orchestrator import get_trace

router = APIRouter()


def _content_summary(c: Content) -> dict:
    return {
        "id": c.id, "market": c.market, "language": c.language,
        "title": c.title, "summary": c.summary, "status": c.status,
        "topic": (c.brief or {}).get("topic", ""),
        "angle": (c.brief or {}).get("angle", ""),
        "quality_avg": (c.quality or {}).get("avg", 0),
        "verdict": (c.quality or {}).get("verdict", ""),
        "formats": list((c.formats or {}).keys()),
        "is_fallback": c.is_fallback,
        "created_at": c.created_at.isoformat() if c.created_at else "",
    }


@router.get("/contents")
async def list_contents(market: str = "", limit: int = 50):
    async with SessionLocal() as session:
        q = select(Content).order_by(Content.created_at.desc()).limit(limit)
        if market:
            q = q.where(Content.market == market)
        rows = (await session.execute(q)).scalars().all()
        return {"contents": [_content_summary(c) for c in rows]}


@router.get("/contents/{content_id}")
async def content_detail(content_id: str):
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        return {
            **_content_summary(c),
            "brief": c.brief, "body": c.body, "evidences": c.evidences,
            "formats": c.formats, "distribution": c.distribution,
            "quality": c.quality, "decision_log": c.decision_log,
            "prompt_versions": c.prompt_versions, "task_id": c.task_id,
        }


@router.get("/contents/{content_id}/trace")
async def content_trace(content_id: str):
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        return await get_trace(session, c.task_id)
