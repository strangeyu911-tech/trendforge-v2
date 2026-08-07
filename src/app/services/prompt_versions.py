"""Prompt 版本治理服务（M3 可执行闭环核心）

职责：
- 把 DB 中 adopted=True 的 PromptRecord 同步为 PromptManager 的运行时覆盖层，
  使「人审采纳新版本 → 下一轮运行即生效」无需重启。
- 版本创建 / 采纳 / 回滚 / diff。
- 供 FeedbackAnalyst 读取当前生效 prompt 文本，产出可采纳的新版 prompt。
"""
from __future__ import annotations

import re
from difflib import unified_diff

from sqlalchemy import select
from uuid import uuid4

from app.models import PromptRecord, PromptSuggestion
from app.prompts.manager import get_pm


async def refresh_overrides(session) -> int:
    """用 DB 中已采纳的版本刷新 PromptManager 覆盖层。返回生效覆盖数。

    每次采纳 / 回滚后调用；应用启动(seed_all)时也调用，保证部署后覆盖层与 DB 一致。
    """
    pm = get_pm()
    pm.clear_overrides()
    rows = (await session.execute(
        select(PromptRecord).where(PromptRecord.adopted.is_(True)))).scalars().all()
    for r in rows:
        pm.set_override(r.name, r.content, r.version)
    return len(rows)


def _version_rank(v: str) -> int:
    m = re.search(r"\d+", v or "")
    return int(m.group(0)) if m else 0


async def next_version(session, template: str) -> str:
    rows = (await session.execute(
        select(PromptRecord.version).where(PromptRecord.name == template))).scalars().all()
    n = max([_version_rank(v) for v in rows], default=0)
    return f"v{n + 1}"


async def list_versions(session, template: str | None = None) -> list[dict]:
    q = select(PromptRecord).order_by(PromptRecord.created_at.desc())
    if template:
        q = q.where(PromptRecord.name == template)
    rows = (await session.execute(q)).scalars().all()
    return [{
        "id": r.id, "name": r.name, "version": r.version, "status": r.status,
        "source": r.source, "adopted": r.adopted, "parent_version": r.parent_version,
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "adopted_at": r.adopted_at.isoformat() if r.adopted_at else "",
        "preview": (r.content or "")[:300],
        "is_adopted": r.adopted,
    } for r in rows]


async def create_version(session, template: str, content: str, *,
                        source: str = "human", parent_version: str = "",
                        adopted: bool = False) -> PromptRecord:
    version = await next_version(session, template)
    rec = PromptRecord(
        name=template, version=version, content=content, status="production",
        source=source, adopted=False, parent_version=parent_version,
    )
    session.add(rec)
    await session.flush()
    if adopted:
        await adopt_version(session, rec.id)
    return rec


async def adopt_version(session, version_id: str) -> dict:
    """采纳某版本：该版本置 adopted=True，同模板其余版本置 False，刷新覆盖层。"""
    rec = await session.get(PromptRecord, version_id)
    if not rec:
        raise ValueError("版本不存在")
    from datetime import datetime
    # 取消同模板其它版本的采纳
    siblings = (await session.execute(
        select(PromptRecord).where(PromptRecord.name == rec.name,
                                   PromptRecord.id != rec.id))).scalars().all()
    for s in siblings:
        s.adopted = False
        s.adopted_at = None
    rec.adopted = True
    rec.adopted_at = datetime.utcnow()
    await session.flush()
    n = await refresh_overrides(session)
    return {"id": rec.id, "name": rec.name, "version": rec.version,
            "adopted": True, "overrides_active": n}


async def diff_versions(session, a_id: str, b_id: str) -> dict:
    a = await session.get(PromptRecord, a_id)
    b = await session.get(PromptRecord, b_id)
    if not a or not b:
        raise ValueError("版本不存在")
    a_lines = (a.content or "").splitlines(keepends=True)
    b_lines = (b.content or "").splitlines(keepends=True)
    diff = "".join(unified_diff(
        a_lines, b_lines, fromfile=f"{a.name}@{a.version}",
        tofile=f"{b.name}@{b.version}", lineterm=""))
    return {"a": {"name": a.name, "version": a.version, "source": a.source},
            "b": {"name": b.name, "version": b.version, "source": b.source},
            "diff": diff}


async def current_template_content(session, name: str) -> tuple[str, str]:
    """返回某模板当前生效的完整 prompt 文本与版本号（adopted 优先，否则文件基线）。"""
    rec = (await session.execute(
        select(PromptRecord).where(PromptRecord.name == name,
                                   PromptRecord.adopted.is_(True)).limit(1))).scalars().first()
    if rec:
        return rec.content, rec.version
    # 文件基线：经 PromptManager
    sys_, usr = get_pm().load(name)
    return f"---system---\n{sys_}\n---user---\n{usr}", "v1(file)"


async def persist_structured_suggestions(session, structured: list[dict], market: str = "") -> list[str]:
    """把 FeedbackAnalyst 产出的结构化建议落库为 pending 的 PromptSuggestion（待人审闸门）。"""
    ids: list[str] = []
    for s in (structured or []):
        new_prompt = (s or {}).get("new_prompt")
        if not new_prompt or "---system---" not in new_prompt:
            continue
        sug = PromptSuggestion(
            id=str(uuid4()),
            target_template=str(s.get("target_template") or ""),
            section=str(s.get("section") or ""),
            proposed_change=str(s.get("proposed_change") or ""),
            rationale=str(s.get("rationale") or ""),
            expected_metric=str(s.get("expected_metric") or ""),
            new_prompt=new_prompt,
            source="ai_generated", status="pending", market=market,
        )
        session.add(sug)
        ids.append(sug.id)
    await session.flush()
    return ids
