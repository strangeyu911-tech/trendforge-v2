"""杂项：健康 / 市场档案 / 知识库 / Prompt / 失败案例库（含处置入口）"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.routers.pipeline import JOBS
from app.config import settings
from app.labels_cn import badcase_status_cn, failure_kind_cn
from app.llm import get_llm
from app.models import BadCase, Market, PromptRecord, SessionLocal
from app.rag.store import kb_stats, retrieve
from app.workflow.orchestrator import run_pipeline, settle_bad_case_after_job

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
    """失败案例列表 + 按失败类型的聚合。

    字段口径：status_label / failure_kind_label 由后端给中文，前端不再自己翻译枚举；
    resolved_at 为空即「尚未闭环」，前端据此算挂起天数，而不是把空字段摊给读者看。
    """
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(BadCase).order_by(BadCase.created_at.desc()).limit(50))).scalars().all()
        now = datetime.utcnow()
        items, groups = [], {}
        for b in rows:
            kind = b.failure_kind or ""
            age = int((now - b.created_at).total_seconds() // 86400) if b.created_at else 0
            if kind:
                g = groups.setdefault(kind, {"failure_kind": kind, "label": failure_kind_cn(kind),
                                             "count": 0, "pending": 0})
                g["count"] += 1
                if b.status in ("open", "retrying"):
                    g["pending"] += 1
            items.append({
                "id": b.id, "category": b.category, "title": b.title,
                "root_cause": b.root_cause, "fix_action": b.fix_action, "status": b.status,
                "status_label": badcase_status_cn(b.status),
                "failure_kind": kind,
                "failure_kind_label": failure_kind_cn(kind) if kind else "",
                "market": b.market or "", "age_days": age,
                "created_at": b.created_at.isoformat() if b.created_at else "",
                "resolved_at": b.resolved_at.isoformat() if b.resolved_at else "",
            })
        return {
            "bad_cases": items,
            "groups": sorted(groups.values(), key=lambda x: -x["count"]),
            "summary": {
                "total": len(items),
                "pending": sum(1 for i in items if i["status"] in ("open", "retrying")),
                "recovered": sum(1 for i in items if i["status"] == "auto_recovered"),
                "archived": sum(1 for i in items if i["status"] == "archived"),
            },
        }


@router.post("/bad-cases/{case_id}/archive")
async def archive_bad_case(case_id: int):
    """人工结案：接受该失败不再跟进。归档是终态，让「未处置」有出口。"""
    async with SessionLocal() as session:
        bc = await session.get(BadCase, case_id)
        if not bc:
            raise HTTPException(status_code=404, detail="案例不存在")
        bc.status = "archived"
        bc.resolved_at = datetime.utcnow()
        await session.commit()
        return {"ok": True, "status": bc.status, "status_label": badcase_status_cn(bc.status)}


@router.post("/bad-cases/{case_id}/rerun")
async def rerun_bad_case(case_id: int, market: str = ""):
    """按案例所属市场重跑一次完整链路，用真实结果回写案例状态。

    真跑而非只打标记：成功后 run_pipeline 会把案例置为「已自愈」并引用产出稿；
    失败则退回「待人工处置」并换成本次真实根因（见 orchestrator._settle_bad_case）。
    市场取案例记录，历史案例未记录市场时须由调用方显式指定，不猜。
    """
    async with SessionLocal() as session:
        bc = await session.get(BadCase, case_id)
        if not bc:
            raise HTTPException(status_code=404, detail="案例不存在")
        mk = (market or bc.market or "").strip()
        if not mk:
            raise HTTPException(status_code=400,
                                detail="该案例未记录所属市场，请在请求中指定 market")
        if bc.status == "retrying":
            return {"ok": False, "error": "该案例正在重跑中", "market": mk}
        if bc.status == "archived":
            raise HTTPException(status_code=400,
                                detail="该案例已归档结案，如需重跑请先确认归档决定不再适用")
        bc.status = "retrying"
        await session.commit()

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "running", "market": mk, "result": None,
                    "error": None, "cancelled": False, "bad_case_id": case_id}

    async def _work():
        try:
            result = await run_pipeline(mk, bad_case_id=case_id)
            JOBS[job_id] = {"status": "done", "market": mk, "result": result,
                            "error": None, "bad_case_id": case_id}
        except Exception as e:
            JOBS[job_id] = {"status": "failed", "market": mk, "result": None,
                            "error": str(e)[:300], "bad_case_id": case_id}
            # 兜底回写：run_pipeline 在进入内部 try 之前失败时内部回写执行不到，
            # 不兜住案例会永远停在「重跑中」（这是实际踩到过的 bug）
            try:
                await settle_bad_case_after_job(case_id, str(e)[:200])
            except Exception:
                pass

    asyncio.create_task(_work())
    return {"ok": True, "job_id": job_id, "market": mk, "status": "retrying"}
