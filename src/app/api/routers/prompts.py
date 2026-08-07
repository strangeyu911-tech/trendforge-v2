"""M3 可执行闭环：Prompt 版本治理 + 迭代建议人审闸门 + A/B 对比

设计哲学（与 KBCurator 同构）：AI 只提议、不自动改系统。
FeedbackAnalyst 产出结构化「迭代建议」（含完整新版 prompt）→ 人审「采纳」才生成新版本
并覆盖生效 → 可 diff / 可回滚；同一选题用两版 Prompt 各跑一次即 A/B 验证。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.models import PromptRecord, PromptSuggestion, SessionLocal
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


# ---------- A/B ----------
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
    result = await run_ab(
        market_code=req.market, template_name=req.template,
        v1_id=req.v1_id, v2_id=req.v2_id,
        brief=req.brief, angle=req.angle, topic=req.topic,
        per_content=req.per_content)
    return result


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
