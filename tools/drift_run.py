"""TrendForge V2 — 漂移对比批量跑数（本地，需 DEEPSEEK_API_KEY）

分别用「当前代码（含 TopicGuard）」与「pre-M5 代码（无漂移防护）」各跑一批，
把每条内容的母稿/简报/证据/质量落盘，后续用统一 TCS 打分器算漂移率。

用法:
  python tools/drift_run.py <src_dir> <markets_csv> <per_market> <out_json>
  python tools/drift_run.py D:/.../v2_trendforge/src US,JP,KR,BR,CN 3 D:/tmp/drift_after.json

- src_dir 必须是某仓库的 src/ 目录（决定 import 哪套代码 + 用哪个本地 DB）
- 串行执行（max_workers=1）：避免免费/共享模型下并发争用导致输出污染
- 每条即时写盘，中途失败不丢已完成数据
- 不依赖 API server；直接 seed_all() + run_pipeline()
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


def _done_keys(out: str) -> set:
    """已落盘完成的 (market,idx) 集合，用于断点续跑。"""
    p = Path(out)
    if not p.exists():
        return set()
    keys = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            keys.add((r.get("market"), r.get("idx")))
        except Exception:
            continue
    return keys


async def _run_market(market: str, idx: int, out: str, force: bool) -> None:
    from app.workflow.orchestrator import run_pipeline
    from app.models import SessionLocal, Content
    from sqlalchemy import select, desc

    if not force and (market, idx) in _done_keys(out):
        print(f"[{market}#{idx}] SKIP (already done)", flush=True)
        return

    rec = {"market": market, "idx": idx, "t": time.strftime("%H:%M:%S")}
    try:
        res = await run_pipeline(market)
        rec["content_id"] = (res or {}).get("content_id")
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:400]}"
    # 回读该市场最新一条（无论发布/被拒都已落库，含 topic_guard）
    try:
        async with SessionLocal() as s:
            row = (await s.execute(
                select(Content).where(Content.market == market)
                .order_by(desc(Content.created_at)).limit(1)
            )).scalar_one_or_none()
            if row:
                rec["content_id"] = rec.get("content_id") or row.id
                rec["title"] = row.title
                rec["body"] = row.body
                rec["brief"] = row.brief
                rec["evidences"] = row.evidences
                rec["quality"] = row.quality
                rec["topic_guard"] = (row.quality or {}).get("topic_guard")
                rec["status"] = row.status
                rec["is_fallback"] = row.is_fallback
                rec["ok"] = row.status == "published"
            else:
                rec["ok"] = False
    except Exception as e2:
        rec.setdefault("error", f"readback:{str(e2)[:200]}")
    # 逐行追加（append），中途进程被回收也不丢已完成样本
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tag = "OK" if rec.get("ok") else f"ERR:{rec.get('error','?')[:120]}"
    print(f"[{rec['market']}#{idx}] {tag}", flush=True)


async def main():
    src_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    markets = (sys.argv[2] if len(sys.argv) > 2 else "US,JP,KR,BR,CN").split(",")
    per = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    out = sys.argv[4] if len(sys.argv) > 4 else "drift_out.json"
    force = "--force" in sys.argv

    os.chdir(src_dir)
    sys.path.insert(0, src_dir)
    print(f"[drift_run] src={src_dir} markets={markets} per={per} -> {out}", flush=True)

    from app.seed import seed_all
    print("[drift_run] seeding...", flush=True)
    try:
        await seed_all()
        print("[drift_run] seeded.", flush=True)
    except Exception as se:
        print(f"[drift_run] seed skipped: {type(se).__name__}: {str(se)[:200]}", flush=True)

    done0 = len(_done_keys(out))
    for m in markets:
        for i in range(per):
            await _run_market(m, i, out, force)
    done1 = len(_done_keys(out))
    print(f"[drift_run] DONE: {done1} records on disk "
          f"({done1 - done0} newly added this run)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
