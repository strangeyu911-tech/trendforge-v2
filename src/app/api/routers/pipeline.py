"""供给流水线：发起 run（异步 job + 结果缓存）、状态轮询、运行历史"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
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


def _cache_fresh(cached: PipelineCache | None) -> bool:
    """缓存是否仍在 TTL 内（settings.cache_ttl_hours，默认 72h）。过期视为未命中。"""
    if cached is None or cached.created_at is None:
        return False
    created = cached.created_at
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    age_h = (datetime.utcnow() - created).total_seconds() / 3600
    return age_h <= settings.cache_ttl_hours


@router.post("/run")
async def run(req: RunRequest):
    key = _cache_key(req.market)
    if not req.force:
        async with SessionLocal() as session:
            cached = await session.get(PipelineCache, key)
            if _cache_fresh(cached):
                return {"job_id": None, "cached": True, **cached.response}
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "running", "market": req.market, "result": None, "error": None}

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
    # 运行中：附带 DB 任务的实时进度（当前 agent）。按本 job 的市场过滤，
    # 避免多市场并发运行时进度互相串台（同市场并发仍可能重叠，属已知单进程限制）
    if job["status"] == "running":
        async with SessionLocal() as session:
            t = (await session.execute(
                select(Task).where(Task.status == "running",
                                   Task.market == job.get("market", ""))
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
        # 反查真实内容标题 + 选题(brief.topic)：既兜底缺失标题，又用于同选题重跑去重
        need_ids = {t.output.get("content_id") for t in rows
                    if t.output and t.output.get("content_id")}
        title_map, topic_map = {}, {}
        if need_ids:
            crows = (await session.execute(
                select(Content.id, Content.title, Content.brief).where(Content.id.in_(need_ids)))).all()
            for cid, ctitle, cbrief in crows:
                title_map[cid] = ctitle or ""
                if isinstance(cbrief, dict):
                    topic_map[cid] = cbrief.get("topic", "")
                elif isinstance(cbrief, str) and cbrief:
                    try:
                        topic_map[cid] = json.loads(cbrief).get("topic", "")
                    except Exception:
                        topic_map[cid] = ""
                else:
                    topic_map[cid] = ""
        # 按 (市场, 归一化选题) 分组：同选题多次供给只突出最新，旧运行标「↻旧版」（第四条问题）
        def _norm(market, topic):
            return (market, re.sub(r'[^a-z0-9 ]', '', (topic or '').lower()).strip())
        groups = {}
        dup_meta = {}
        for t in rows:
            cid = (t.output or {}).get("content_id")
            if not cid:
                # 无内容产出（失败/中断/驳回）：不参与选题去重，每条独立显示，避免误标「重跑」
                dup_meta[t.id] = (1, True, "")
                continue
            key = _norm(t.market, topic_map.get(cid, ''))
            groups.setdefault(key, []).append(t)
        for grp in groups.values():
            grp_sorted = sorted(grp, key=lambda x: x.created_at or '', reverse=True)
            for i, t in enumerate(grp_sorted):
                dup_meta[t.id] = (len(grp_sorted), i == 0,
                                  grp_sorted[-1].created_at.isoformat() if grp_sorted[-1].created_at else "")
        out = []
        for t in rows:
            task_out = dict(t.output) if isinstance(t.output, dict) else (t.output or {})
            cid = task_out.get("content_id")
            if cid and not (task_out.get("title") or "").strip() and cid in title_map:
                task_out = {**task_out, "title": title_map[cid] or ""}
            dc, is_latest, earliest = dup_meta.get(t.id, (1, True, ""))
            out.append({
                "id": t.id, "market": t.market, "status": t.status, "progress": t.progress,
                "output": task_out, "error": t.error,
                "total_duration_ms": t.total_duration_ms, "total_cost_cny": t.total_cost_cny,
                "created_at": t.created_at.isoformat() if t.created_at else "",
                "_dup_count": dc, "_is_latest": is_latest, "_dup_earliest": earliest,
            })
        return {"tasks": out}


async def reset_orphan_runs() -> int:
    """部署/重启后，DB 中仍标记 running 的后台任务（协程已被实例重启/休眠杀掉）复位为 failed。

    进程重启后内存 JOBS 表必然丢失，这些 running 是永远不出结果的僵尸，会让运行历史
    出现"running 却空白"的不可信记录。每次启动扫描一次即可根治（幂等）。
    """
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Task).where(Task.status == "running"))).scalars().all()
        for t in rows:
            t.status = "failed"
            t.error = "实例重启/休眠时后台任务中断"
            t.progress = "interrupted"
        if rows:
            await session.commit()
        return len(rows)
