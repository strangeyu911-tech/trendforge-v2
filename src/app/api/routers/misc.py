"""杂项：健康 / 市场档案 / 知识库 / Prompt / BadCase"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.config import settings
from app.llm import get_llm
from app.models import BadCase, Market, PromptRecord, SessionLocal
from app.rag.store import kb_stats, retrieve

router = APIRouter()


@router.get("/health")
async def health():
    llm = get_llm()
    async with SessionLocal() as session:
        stats = await kb_stats(session)
    return {
        "ok": True, "version": "2.0.0",
        "llm": {"model": settings.llm_model, "configured": llm.available},
        "kb": stats,
    }


@router.get("/markets")
async def list_markets():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Market))).scalars().all()
        return {"markets": [{
            "code": m.code, "name": m.name, "language": m.language,
            "tone": m.tone, "default_style": m.default_style,
            "media_landscape": m.media_landscape, "culture_notes": m.culture_notes,
            "insight_sources": m.insight_sources or [],
            "interests": m.interests, "platforms": m.platforms,
        } for m in rows]}


@router.get("/kb/stats")
async def kb():
    async with SessionLocal() as session:
        return await kb_stats(session)


@router.get("/kb/search")
async def kb_search(q: str, top_k: int = 5):
    async with SessionLocal() as session:
        return {"query": q, "results": await retrieve(session, q, top_k=top_k, days=60)}


@router.get("/prompts")
async def list_prompts():
    async with SessionLocal() as session:
        rows = (await session.execute(select(PromptRecord))).scalars().all()
        return {"prompts": [{
            "name": p.name, "version": p.version, "status": p.status,
            "preview": p.content[:400],
        } for p in rows]}


@router.get("/bad-cases")
async def list_bad_cases():
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(BadCase).order_by(BadCase.created_at.desc()).limit(50))).scalars().all()
        return {"bad_cases": [{
            "id": b.id, "category": b.category, "title": b.title,
            "root_cause": b.root_cause, "fix_action": b.fix_action, "status": b.status,
            "created_at": b.created_at.isoformat() if b.created_at else "",
        } for b in rows]}
