"""知识库治理：新鲜度概览 / KBCurator 策展 / 补丁审核闸门"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select

from app.agents.kb_curator import KBCuratorAgent
from app.llm import get_llm
from app.models import KBPatch, Market, SessionLocal, Task
from app.rag.store import apply_kb_patch, collect_kb_state

router = APIRouter()


@router.get("/kb/freshness")
async def freshness():
    """知识库新鲜度概览（治理面板用）"""
    async with SessionLocal() as session:
        return await collect_kb_state(session)


@router.post("/kb/curate")
async def curate():
    """运行 KBCurator：扫描覆盖度/过期 → 生成一份 pending 待审补丁（只提议不改）"""
    llm = get_llm()
    async with SessionLocal() as session:
        mkt = (await session.execute(select(Market).limit(1))).scalars().first()
        task = Task(id=str(uuid.uuid4()), kind="kb_curate",
                    market=mkt.code if mkt else "US", status="running")
        from app.agents.base import RunContext
        ctx = RunContext(task_id=task.id, session=session, llm=llm, task=task,
                         market=mkt, spans=[], decision_log={}, prompt_versions={})
        result = await KBCuratorAgent()._exec(ctx, {})
        patch = KBPatch(
            id=str(uuid.uuid4()), status="pending",
            market="", rationale=result.get("rationale", ""),
            items=result.get("items", []),
        )
        session.add(patch)
        await session.commit()
        return {"patch_id": patch.id, "status": "pending",
                "rationale": patch.rationale, "items": patch.items}


@router.get("/kb/patches")
async def list_patches():
    """历史补丁列表"""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(KBPatch).order_by(KBPatch.created_at.desc()).limit(50))).scalars().all()
        return {"patches": [{
            "id": p.id, "status": p.status, "rationale": p.rationale,
            "items": p.items, "created_at": p.created_at.isoformat() if p.created_at else "",
            "decided_at": p.decided_at.isoformat() if p.decided_at else "",
        } for p in rows]}


@router.post("/kb/patches/{patch_id}/approve")
async def approve_patch(patch_id: str):
    """人审闸门：approve 才真正入库/退役"""
    async with SessionLocal() as session:
        p = await session.get(KBPatch, patch_id)
        if not p:
            return {"ok": False, "error": "补丁不存在"}
        if p.status != "pending":
            return {"ok": False, "error": f"补丁已 {p.status}"}
        applied = await apply_kb_patch(session, {"items": p.items})
        p.status = "approved"
        p.decided_at = datetime.utcnow()
        await session.commit()
        return {"ok": True, "status": "approved", **applied}


@router.post("/kb/patches/{patch_id}/reject")
async def reject_patch(patch_id: str):
    """人审闸门：拒绝补丁（不改动知识库）"""
    async with SessionLocal() as session:
        p = await session.get(KBPatch, patch_id)
        if not p:
            return {"ok": False, "error": "补丁不存在"}
        p.status = "rejected"
        p.decided_at = datetime.utcnow()
        await session.commit()
        return {"ok": True, "status": "rejected"}
