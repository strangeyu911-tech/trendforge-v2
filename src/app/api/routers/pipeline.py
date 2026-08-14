"""供给流水线：发起 run（异步 job + 结果缓存）、状态轮询、运行历史"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.models import Content, PipelineCache, SessionLocal, Task
from app.workflow.orchestrator import run_pipeline

router = APIRouter()

# job_id → {status, result, error}
JOBS: dict[str, dict] = {}


class RunRequest(BaseModel):
    market: str = "US"
    force: bool = False  # 忽略缓存强制重跑


def _cache_key(market: str) -> str:
    return hashlib.sha256(f"pipeline:v2:{market}".encode()).hexdigest()


@router.post("/run")
async def run(req: RunRequest):
    key = _cache_key(req.market)
    if not req.force:
        async with SessionLocal() as session:
            cached = await session.get(PipelineCache, key)
            if cached:
                return {"job_id": None, "cached": True, **cached.response}
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "running", "result": None, "error": None}

    async def _work():
        try:
            result = await run_pipeline(req.market)
            JOBS[job_id] = {"status": "done", "result": result, "error": None}
            async with SessionLocal() as session:
                cache = await session.get(PipelineCache, key) or PipelineCache(key=key)
                cache.response = result
                session.add(cache)
                await session.commit()
        except Exception as e:
            JOBS[job_id] = {"status": "failed", "result": None, "error": str(e)[:300]}

    asyncio.create_task(_work())
    return {"job_id": job_id, "cached": False}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"status": "unknown"}
    out = dict(job)
    # 运行中：附带 DB 任务的实时进度（当前 agent）
    if job["status"] == "running":
        async with SessionLocal() as session:
            t = (await session.execute(
                select(Task).where(Task.status == "running")
                .order_by(Task.created_at.desc()).limit(1))).scalars().first()
            if t:
                out["progress"] = t.progress
                out["task_id"] = t.id
    if job.get("result") and job["result"].get("task_id"):
        out["task_id"] = job["result"]["task_id"]
    return out


@router.get("/tasks")
async def list_tasks(limit: int = 20):
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Task).order_by(Task.created_at.desc()).limit(limit))).scalars().all()
        # 反查真实内容标题，兜底填补 output.title 缺失（种子库旧任务/异常落库可能为空）→
        # 避免前端“运行历史”渲染出空白标题链接。
        need_ids = {t.output.get("content_id") for t in rows
                    if t.output and t.output.get("content_id") and not (t.output.get("title") or "").strip()}
        title_map = {}
        if need_ids:
            crows = (await session.execute(
                select(Content.id, Content.title).where(Content.id.in_(need_ids)))).all()
            title_map = {cid: ctitle for cid, ctitle in crows}
        out = []
        for t in rows:
            task_out = dict(t.output) if isinstance(t.output, dict) else (t.output or {})
            cid = task_out.get("content_id")
            if cid and not (task_out.get("title") or "").strip() and cid in title_map:
                task_out = {**task_out, "title": title_map[cid] or ""}
            out.append({
                "id": t.id, "market": t.market, "status": t.status, "progress": t.progress,
                "output": task_out, "error": t.error,
                "total_duration_ms": t.total_duration_ms, "total_cost_cny": t.total_cost_cny,
                "created_at": t.created_at.isoformat() if t.created_at else "",
            })
        return {"tasks": out}
