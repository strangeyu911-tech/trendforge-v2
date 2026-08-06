"""KB 真实化：把真实公开源（Dev.to + GDELT best-effort）抓取的文章 ingest 进 demo_snapshot.db，
使虚构 example.com 条目占比降到 30% 以下。

为什么只抓 Dev.to/GDELT 不抓 HN：HN Algolia 对高频请求会限流（返回 400），而 Dev.to 稳定且真实。
本脚本直接调用源的底层 fetcher，绕过 fetch_market_signals 的缓存与 HN 重试，避免被限流拖慢。

用法：python tools/refresh_kb_real.py
幂等：ingest_document 按 url 去重，重复运行不会翻倍（但会补缺失的）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import DATA_DIR
from app.models import Document
from app.rag.store import ingest_document
from app.sources.devto import fetch_devto
from app.sources.gdelt import fetch_gdelt

SNAPSHOT = DATA_DIR / "demo_snapshot.db"
MARKETS = ["US", "JP", "KR", "BR", "CN"]


async def main() -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{SNAPSHOT}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    # 旧快照缺 migrate_db 后来加的列，先补齐（运行时 seed_all 也会补，但本脚本需先能写入）
    async with engine.begin() as conn:
        res = await conn.execute(text("PRAGMA table_info(documents)"))
        cols = {r[1] for r in res}
        for col, typ, dflt in [
            ("last_verified_at", "VARCHAR(16)", "''"),
            ("freshness_ttl", "INTEGER", "90"),
            ("retired", "INTEGER", "0"),
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {typ} DEFAULT {dflt}"))
    async with Session() as s:
        added = 0
        for m in MARKETS:
            # Dev.to：每个市场多 tag，尽量拉开与别的市场的文章差异
            sigs = await fetch_devto(m, limit_per_tag=25)
            for sig in sigs:
                if not sig.url:
                    continue
                added += await ingest_document(
                    s, title=sig.title, source=sig.source, url=sig.url,
                    category=sig.category, country=sig.country, language=sig.language,
                    published_at=sig.published_at, body=sig.snippet or sig.title,
                    credibility=2, ttl=30)
            # GDELT：best-effort，补本地化新闻（datacenter 常被限流，失败即跳过）
            for sig in await fetch_gdelt(m):
                if not sig.url:
                    continue
                added += await ingest_document(
                    s, title=sig.title, source=sig.source, url=sig.url,
                    category=sig.category, country=sig.country, language=sig.language,
                    published_at=sig.published_at, body=sig.snippet or sig.title,
                    credibility=2, ttl=30)
            await s.commit()
            print(f"  {m}: 本市场尝试入库 +{added if added else 0}")

        total = (await s.scalar(select(func.count(Document.id)))) or 0
        ex = (await s.scalar(select(func.count(Document.id)).where(Document.url.like("%example.com%")))) or 0
        real = total - ex
        print(f"\n入库新增(去重后): {added}")
        print(f"documents 总数: {total} | 真实: {real} | example.com: {ex}")
        print(f"虚构占比: {100*ex/max(total,1):.1f}%  {'✅ 达标(<30%)' if ex/total < 0.3 else '❌ 未达标，需增大抓取量'}")


if __name__ == "__main__":
    asyncio.run(main())
