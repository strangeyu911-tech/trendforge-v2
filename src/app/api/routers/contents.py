"""内容：列表 / 详情 / Trace"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.models import Content, SessionLocal
from app.services.zh_mirror import ensure_zh_mirror
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
            # 中文回译镜像（非中文市场才有；缺失时前端按需触发生成）
            "translation": c.translation or {},
            "needs_zh": not (c.language or "").lower().startswith("zh"),
        }


@router.post("/contents/{content_id}/zh")
async def content_zh_mirror(content_id: str, refresh: bool = False):
    """生成/获取中文对照（供中文运营审核非中文市场产出）。

    按需生成 + 缓存：不塞进供给链路，避免每条内容都白白消耗一次翻译额度。
    """
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        return await ensure_zh_mirror(session, c, refresh=refresh)


@router.get("/contents/{content_id}/trace")
async def content_trace(content_id: str):
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        return await get_trace(session, c.task_id)
