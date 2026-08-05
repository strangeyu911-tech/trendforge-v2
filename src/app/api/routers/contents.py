"""内容：列表 / 详情 / Trace / 修订"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.agents.base import RunContext
from app.agents.editor import EditorAgent
from app.agents.fact_checker import FactCheckerAgent
from app.agents.format_adapter import FormatAdapterAgent
from app.agents.writer import WriterAgent
from app.config import settings
from app.llm import get_llm
from app.models import Content, Market, SessionLocal, Task
from app.services.zh_mirror import ensure_zh_mirror
from app.workflow.orchestrator import get_trace, run_revise_rounds

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


@router.post("/contents/{content_id}/revise")
async def revise_content(content_id: str):
    """对已裁决为「需修改」的内容，按总编修改意见就地重写。

    复用生成时的 Produce 段子链（Writer→FactChecker→Editor），并刷新多形态派生；
    中文镜像置空失效，下次查看按需重建。返回更新后的完整内容（与详情同结构）。
    """
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        if (c.quality or {}).get("verdict") != "revise":
            raise HTTPException(400, "仅裁决为「需修改」的内容可发起修订")

        # 并发护栏：置为 revising，防止重复触发。
        # 容错：若上次修订因实例重启/客户端断开而残留 revising 状态，超过阈值则视为失效、允许重入。
        if c.status == "revising":
            since = (c.decision_log or {}).get("_revising_since")
            recent = False
            if since:
                try:
                    recent = (datetime.utcnow() - datetime.fromisoformat(since)).total_seconds() < 600
                except (ValueError, TypeError):
                    recent = False
            if recent:
                raise HTTPException(409, "修订进行中，请稍后再试")
        c.status = "revising"
        c.decision_log = {**(c.decision_log or {}), "_revising_since": datetime.utcnow().isoformat()}
        await session.commit()

        market = await session.get(Market, c.market)
        if not market:
            c.status = "published"
            await session.commit()
            raise HTTPException(400, "未知市场，无法修订")

        task = Task(id=str(uuid.uuid4()), kind="revise", market=c.market,
                    status="running", input={"content_id": content_id})
        session.add(task)
        await session.commit()
        ctx = RunContext(task_id=task.id, session=session, llm=get_llm(), task=task, market=market)

        try:
            quality = c.quality or {}
            data = {
                "brief": c.brief or {},
                "evidences": c.evidences or [],
                "article": {"title": c.title, "summary": c.summary, "body": c.body},
                "fact_check": quality.get("fact_check", {}),
                "review": {k: v for k, v in quality.items() if k != "fact_check"},
            }
            # 重跑 Produce 段子链：Writer 按修改意见重写 → FactChecker → Editor
            data, rounds = await run_revise_rounds(ctx, data)
            # 刷新多形态（正文已变，派生需同步；不重跑 Distributor，分发计划属策略层）
            data.update(await FormatAdapterAgent()._exec(ctx, data))

            c.title = data["article"]["title"]
            c.summary = data["article"]["summary"]
            c.body = data["article"]["body"]
            c.formats = data.get("formats", c.formats)
            c.quality = {"fact_check": data.get("fact_check", {}), **data.get("review", {})}
            c.quality["_revise"] = {"rounds": rounds, "at": datetime.utcnow().isoformat()}
            c.translation = {}  # 失效中文镜像，下次查看按需重建
            c.status = "published"
            task.status = "done"
            task.progress = "done"
            task.finished_at = datetime.utcnow()
            await ctx.persist()
            await session.commit()
        except BaseException as e:
            # BaseException 含 CancelledError：客户端断开/实例重启时不留残留 revising 状态
            c.status = "published"
            task.status = "failed"
            task.error = str(e)[:500]
            task.finished_at = datetime.utcnow()
            try:
                await session.commit()
            except Exception:
                pass
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(500, f"修订失败：{str(e)[:200]}")

        return {
            **_content_summary(c),
            "brief": c.brief, "body": c.body, "evidences": c.evidences,
            "formats": c.formats, "distribution": c.distribution,
            "quality": c.quality, "decision_log": c.decision_log,
            "prompt_versions": c.prompt_versions, "task_id": c.task_id,
            "translation": c.translation or {},
            "needs_zh": not (c.language or "").lower().startswith("zh"),
        }


@router.get("/contents/{content_id}/trace")
async def content_trace(content_id: str):
    async with SessionLocal() as session:
        c = await session.get(Content, content_id)
        if not c:
            raise HTTPException(404, "内容不存在")
        return await get_trace(session, c.task_id)
