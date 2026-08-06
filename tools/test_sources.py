"""本地验证：对 5 个市场各跑一次真实信号抓取，确认数据源可达、字段正确。
运行：PYTHONPATH=src 或本脚本已自动把 src 加入 path。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from app.sources import fetch_market_signals


async def main():
    for m in ["US", "JP", "KR", "BR", "CN"]:
        sigs, diag = await fetch_market_signals(m)
        print(f"\n=== {m} ===")
        print("diag:", diag)
        print("count:", len(sigs))
        for s in sigs[:3]:
            print("  -", s.source, "|", s.published_at, "|", s.engagement,
                  "|", s.title[:55], "|", (s.url or "")[:45])


if __name__ == "__main__":
    asyncio.run(main())
