"""M3 可执行闭环：Prompt 版本治理 + 迭代建议人审闸门 + A/B 对比

设计哲学（与 KBCurator 同构）：AI 只提议、不自动改系统。
FeedbackAnalyst 产出结构化「迭代建议」（含完整新版 prompt）→ 人审「采纳」才生成新版本
并覆盖生效 → 可 diff / 可回滚；同一选题用两版 Prompt 各跑一次即 A/B 验证。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.models import Content, PromptRecord, PromptSuggestion, SessionLocal
from app.prompts.manager import TPL_DIR, get_pm
from app.services.prompt_versions import (
    adopt_version, create_version, diff_versions, list_versions,
    persist_structured_suggestions,
)
from app.workflow.ab import run_ab
from app.agents.base import RunContext
from app.agents.feedback_analyst import FeedbackAnalystAgent
from app.llm import get_llm
from app.models import Market, Task

router = APIRouter()


# ---------- 模板与版本 ----------
@router.get("/prompts/templates")
async def list_templates():
    names = sorted(p.stem for p in TPL_DIR.glob("*.md"))
    return {"templates": names}


@router.get("/prompts/versions")
async def versions(template: str = ""):
    async with SessionLocal() as session:
        rows = await list_versions(session, template or None)
    return {"versions": rows}


class VersionCreate(BaseModel):
    template: str
    content: str
    source: str = "human"
    parent_version: str = ""
    adopted: bool = False


@router.post("/prompts/versions")
async def create_version_endpoint(req: VersionCreate):
    async with SessionLocal() as session:
        rec = await create_version(
            session, req.template, req.content,
            source=req.source, parent_version=req.parent_version, adopted=req.adopted)
        await session.commit()
        return {"ok": True, "id": rec.id, "name": rec.name, "version": rec.version,
                "adopted": rec.adopted}


@router.post("/prompts/versions/{version_id}/adopt")
async def adopt_version_endpoint(version_id: int):
    async with SessionLocal() as session:
        res = await adopt_version(session, version_id)
        await session.commit()
        return {"ok": True, **res}


@router.get("/prompts/versions/{a}/diff/{b}")
async def diff_endpoint(a: int, b: int):
    async with SessionLocal() as session:
        return await diff_versions(session, a, b)


# ---------- 迭代建议（AI 提议 → 人审闸门） ----------
@router.get("/prompts/suggestions")
async def list_suggestions(status: str = "pending"):
    async with SessionLocal() as session:
        q = select(PromptSuggestion).order_by(PromptSuggestion.created_at.desc()).limit(100)
        if status and status != "all":
            q = q.where(PromptSuggestion.status == status)
        rows = (await session.execute(q)).scalars().all()
        return {"suggestions": [{
            "id": s.id, "target_template": s.target_template, "section": s.section,
            "proposed_change": s.proposed_change, "rationale": s.rationale,
            "expected_metric": s.expected_metric, "new_prompt": s.new_prompt,
            "source": s.source, "status": s.status, "market": s.market,
            "created_at": s.created_at.isoformat() if s.created_at else "",
        } for s in rows]}


@router.post("/prompts/suggestions/{sid}/adopt")
async def adopt_suggestion(sid: str):
    """人审闸门：采纳一条 AI 建议 → 生成 adopted 新版本并覆盖生效"""
    async with SessionLocal() as session:
        s = await session.get(PromptSuggestion, sid)
        if not s:
            return {"ok": False, "error": "建议不存在"}
        if s.status != "pending":
            return {"ok": False, "error": f"建议已 {s.status}"}
        rec = await create_version(
            session, s.target_template, s.new_prompt,
            source="ai_suggested", parent_version="", adopted=True)
        s.status = "adopted"
        await session.commit()
        return {"ok": True, "version_id": rec.id, "name": rec.name,
                "version": rec.version, "overrides_active": True}


@router.post("/prompts/suggestions/{sid}/reject")
async def reject_suggestion(sid: str):
    async with SessionLocal() as session:
        s = await session.get(PromptSuggestion, sid)
        if not s:
            return {"ok": False, "error": "建议不存在"}
        s.status = "rejected"
        await session.commit()
        return {"ok": True, "status": "rejected"}


# ---------- 采纳效果回收（闭环第四段） ----------
def _group_stats(rows: list[dict]) -> dict:
    """把一组「用同一版 Prompt 跑出来的任务」聚合成供对比的指标。"""
    if not rows:
        return {"n": 0, "quality_avg": None, "cost_avg": None,
                "duration_avg": None, "pass": 0, "revise": 0, "reject": 0}
    qs = [r["quality_avg"] for r in rows if r["quality_avg"] is not None]
    costs = [r["cost"] for r in rows]
    durs = [r["duration"] for r in rows]
    vd = {}
    for r in rows:
        v = r["verdict"] or "unknown"
        vd[v] = vd.get(v, 0) + 1
    return {
        "n": len(rows),
        "quality_avg": round(sum(qs) / len(qs), 2) if qs else None,
        "cost_avg": round(sum(costs) / len(costs), 4) if costs else None,
        "duration_avg": round(sum(durs) / len(durs), 0) if durs else None,
        "pass": vd.get("pass", 0), "revise": vd.get("revise", 0), "reject": vd.get("reject", 0),
    }


@router.get("/prompts/adoption-impact")
async def adoption_impact():
    """闭环第四段「效果回收」：用运行时真实数据回答「采纳之后到底变好了没有」。

    口径（全部来自已落库的 tasks.prompt_versions + contents.quality，无任何补写）：
      · 取当前每条生效版本（prompts.adopted=1），用任务记录的 prompt_versions[name] 判定归属；
      · 「采纳前」= 同一模板用的是其它版本的任务；「采纳后」= 用上这一版之后的任务；
      · 对比质量均分 / 单条成本 / 平均耗时 / 裁决分布，并如实带上样本数 n——
        n 很小的时候它就是 anecdote 而不是结论，前端会据此降调提示。
    """
    async with SessionLocal() as session:
        adopted_rows = (await session.execute(
            select(PromptRecord).where(PromptRecord.adopted == 1)  # noqa: E712
        )).scalars().all()
        tasks = (await session.execute(select(Task))).scalars().all()
        contents = (await session.execute(select(Content))).scalars().all()

    cmap = {c.id: c for c in contents}
    out = []
    for rec in adopted_rows:
        target = f"{rec.name}@{rec.version}"
        after, before = [], []
        for t in tasks:
            used = (t.prompt_versions or {}).get(rec.name)
            if not used:
                continue
            cid = (t.output or {}).get("content_id")
            c = cmap.get(cid) if cid else None
            quality = (c.quality or {}).get("avg") if c else None
            row = {
                "task_id": t.id, "kind": t.kind, "market": t.market,
                "quality_avg": quality,
                "verdict": (c.quality or {}).get("verdict") if c else None,
                "cost": float(t.total_cost_cny or 0),
                "duration": int(t.total_duration_ms or 0),
                "status": t.status,
            }
            (after if used == target else before).append(row)
        b, a = _group_stats(before), _group_stats(after)

        def _delta(key):
            if a[key] is None or b[key] is None:
                return None
            return round(a[key] - b[key], 2)

        out.append({
            "template": rec.name, "version": rec.version,
            "adopted_at": rec.adopted_at.isoformat() if rec.adopted_at else "",
            "source": rec.source, "parent_version": rec.parent_version or "",
            "before": b, "after": a,
            "delta": {"quality_avg": _delta("quality_avg"), "cost_avg": _delta("cost_avg")},
        })
    out.sort(key=lambda x: (x["after"]["n"] + x["before"]["n"]), reverse=True)
    return {"adoptions": out, "note": "真实运行数据聚合，样本量见 n"}


# ---------- A/B ----------
# 后台任务：与 revise 同构（内存字典 + 前端轮询）。
# 已知取舍：免费实例重启会丢内存 job，届时轮询接口返回 404，前端提示「请重新运行」而不是假装还在跑。
AB_JOBS: dict[str, dict] = {}


class ABRequest(BaseModel):
    market: str = "US"
    template: str = "writer"
    v1_id: int
    v2_id: int
    angle: str = ""
    topic: str = ""
    brief: dict | None = None
    per_content: int = 300


@router.post("/prompts/ab/run")
async def ab_run(req: ABRequest):
    """发起 A/B：后台跑两次 Produce 链路，立即返回 job_id，前端轮询状态。

    为什么必须异步：一次完整 Produce 链路在真机上要数分钟（含 revise rounds），A/B = 两次，
    同步挂在一个 HTTP 请求里必然被网关/浏览器判定超时——这也是之前「点了半天没结果」的根因。
    """
    async with SessionLocal() as session:
        v1 = await session.get(PromptRecord, req.v1_id)
        v2 = await session.get(PromptRecord, req.v2_id)
        if not v1 or not v2:
            raise HTTPException(404, "Prompt 版本不存在（可能已被回滚或清理）")
        if v1.id == v2.id:
            raise HTTPException(400, "两版选的是同一个版本，无法对比")
        if v1.name != req.template or v2.name != req.template:
            raise HTTPException(400, f"所选版本不属于模板 {req.template}，请重新选择")

    # 轻量 GC：只留最近 50 条，避免长期运行的实例上 job 表无界增长
    if len(AB_JOBS) > 50:
        for stale in sorted(AB_JOBS.items(), key=lambda kv: kv[1].get("started_at", ""))[:len(AB_JOBS) - 50]:
            AB_JOBS.pop(stale[0], None)

    job_id = str(uuid.uuid4())
    AB_JOBS[job_id] = {
        "status": "running", "progress": "已排队", "error": None, "result": None,
        "started_at": datetime.utcnow().isoformat(),
        "meta": {"market": req.market, "template": req.template,
                 "v1_id": req.v1_id, "v2_id": req.v2_id, "angle": req.angle or req.topic},
    }
    asyncio.create_task(_ab_work(job_id, req))
    return {"job_id": job_id, "status": "running"}


async def _ab_work(job_id: str, req: "ABRequest") -> None:
    """后台执行 A/B：跑两次链路 → 仿真 → 指标对比，结果挂在 job 上供轮询。"""
    async def tick(msg: str) -> None:
        if job_id in AB_JOBS:
            AB_JOBS[job_id]["progress"] = msg

    try:
        result = await run_ab(
            market_code=req.market, template_name=req.template,
            v1_id=req.v1_id, v2_id=req.v2_id,
            brief=req.brief, angle=req.angle, topic=req.topic,
            per_content=req.per_content, on_progress=tick)
        AB_JOBS[job_id] = {**AB_JOBS[job_id], "status": "done",
                           "progress": "完成", "result": result}
    except BaseException as e:  # 含 CancelledError：后台任务不受客户端断开影响
        AB_JOBS[job_id] = {**AB_JOBS[job_id], "status": "failed",
                           "progress": "失败", "error": str(e)[:300]}


@router.get("/prompts/ab/jobs/{job_id}")
async def ab_job(job_id: str):
    """轮询 A/B 后台任务：running 带 stage 文案，done 带完整对比结果，failed 带原因。"""
    job = AB_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（实例可能已重启，请重新运行 A/B）")
    return {"job_id": job_id, **job}


# ---------- 触发 FeedbackAnalyst（产出结构化建议） ----------
@router.post("/prompts/feedback")
async def run_feedback(market: str = "US"):
    """运行 FeedbackAnalyst → 结构化迭代建议落库为 pending（待人审采纳）"""
    async with SessionLocal() as session:
        m = await session.get(Market, market)
        if not m:
            return {"ok": False, "error": f"未知市场 {market}"}
        task = Task(id=uuid.uuid4().hex, kind="feedback",
                    market=market, status="running")
        session.add(task)
        await session.commit()
        ctx = RunContext(task_id=task.id, session=session, llm=get_llm(),
                        task=task, market=m, spans=[], decision_log={}, prompt_versions={})
        result = await FeedbackAnalystAgent()._exec(ctx, {})
        report = result.get("eval_report", {})
        suggestion_ids = await persist_structured_suggestions(
            session, report.get("structured_suggestions", []), market=market)
        task.status = "done"
        await ctx.persist()
        await session.commit()
        return {"ok": True, "suggestion_ids": suggestion_ids,
                "findings": report.get("findings", []),
                "suggestions": report.get("suggestions", [])}
