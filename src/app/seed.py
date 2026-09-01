"""启动初始化：建表 → 市场档案 → KB 灌库 → Prompt 模板登记（幂等）"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.config import DATA_DIR
from app.models import Market, PromptRecord, SessionLocal, init_db
from app.prompts.manager import TPL_DIR
from app.rag.store import ingest_kb
from app.services.prompt_versions import refresh_overrides


async def seed_all() -> dict:
    await init_db()  # 含 migrate_db：兼容旧快照补列 + TTL 回填
    async with SessionLocal() as session:
        # 1. 市场档案
        markets = json.loads((DATA_DIR / "markets.json").read_text(encoding="utf-8"))
        added_m = 0
        for m in markets:
            row = await session.get(Market, m["code"])
            if not row:
                session.add(Market(**m))
                added_m += 1
            elif not (getattr(row, "insight_sources", None) or []) and m.get("insight_sources"):
                # 存量市场回填新增的洞察依据字段
                row.insight_sources = m["insight_sources"]
        await session.commit()
        # 2. KB
        added_kb = await ingest_kb(session)
        # 3. Prompt 模板登记
        added_p = 0
        for tpl in sorted(TPL_DIR.glob("*.md")):
            name = tpl.stem
            exists = await session.scalar(
                select(PromptRecord.id).where(PromptRecord.name == name, PromptRecord.version == "v1"))
            if not exists:
                session.add(PromptRecord(name=name, version="v1",
                                         content=tpl.read_text(encoding="utf-8")))
                added_p += 1
        await session.commit()
        # M3：把 DB 中已采纳的 Prompt 版本同步为运行时覆盖层（部署后无需重启即生效）
        overrides = await refresh_overrides(session)
    return {"markets_added": added_m, "kb_docs_added": added_kb,
            "prompts_added": added_p, "prompt_overrides_active": overrides}
