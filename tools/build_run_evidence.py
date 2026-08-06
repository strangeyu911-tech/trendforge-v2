"""把 harvest 下来的真实运行数据固化成仓库内证据文件 + 打印精确统计。

用法:
    python tools/build_run_evidence.py D:/tmp/tf_harvest.json docs/data/RUN_EVIDENCE_v1.json

背景: Render 免费实例磁盘易失（重启即从 demo_snapshot.db 重建），
线上 tasks/contents 会丢。真实运行证据必须落盘进仓库才可复现、可溯源。
"""
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]


def slim_signal(s):
    return {
        "title": s.get("title"),
        "source": s.get("source"),
        "url": s.get("url"),
        "published_at": s.get("published_at"),
        "engagement": s.get("engagement"),
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "D:/tmp/tf_harvest.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "docs/data/RUN_EVIDENCE_v1.json"

    d = json.load(open(src, encoding="utf-8"))
    tasks, contents, details = d["tasks"], d["contents"], d["details"]

    # ---------- 固化 ----------
    ev = {
        "_note": "TrendForge V2 真实运行证据（部署实例 trendforge-v2-api.onrender.com，DeepSeek deepseek-v4-flash）",
        "_captured_at": "2026-08-06T19:59+08:00",
        "_env": "Render free tier（磁盘易失，线上原始记录已随重启丢失，本文件为唯一留存副本）",
        "tasks": [
            {
                "id": t["id"],
                "market": t["market"],
                "status": t["status"],
                "progress": t.get("progress"),
                "duration_ms": t.get("total_duration_ms"),
                "cost_cny": t.get("total_cost_cny"),
                "error": t.get("error"),
                "content_id": (t.get("output") or {}).get("content_id"),
                "created_at": t.get("created_at"),
            }
            for t in tasks
        ],
        "contents": [],
    }
    for c in contents:
        det = details.get(c["id"], {})
        ev["contents"].append(
            {
                "id": c["id"],
                "market": c["market"],
                "language": c.get("language"),
                "title": c.get("title"),
                "topic": c.get("topic"),
                "angle": c.get("angle"),
                "status": c.get("status"),
                "verdict": c.get("verdict"),
                "quality_avg": c.get("quality_avg"),
                "scores": det.get("scores"),
                "formats": list(c.get("formats") or []),
                "signal_count": len(det.get("signals") or []),
                "signals": [slim_signal(s) for s in (det.get("signals") or [])[:5]],
                "fact_check": det.get("fact_check"),
            }
        )

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=2)
    print(f"[written] {out}  ({round(os.path.getsize(out)/1024,1)} KB)")

    # ---------- 精确统计 ----------
    print("\n" + "=" * 56 + "\n精确统计（用于校对 RUN_REPORT 数字）\n" + "=" * 56)
    st = Counter(t["status"] for t in tasks)
    print("task status:", dict(st))
    done = [t for t in tasks if t["status"] == "done"]
    fail = [t for t in tasks if t["status"] == "failed"]
    settled = len(done) + len(fail)
    print(f"已结算运行 N = {settled}（done {len(done)} + failed {len(fail)}），另有 running {st.get('running',0)} 条未结算/已丢失")
    print(f"产出率 = {len(done)}/{settled} = {len(done)/settled*100:.1f}%")

    dur = [(t.get("total_duration_ms") or 0) / 1000 for t in done]
    cost = [t.get("total_cost_cny") or 0 for t in done]
    print(f"done 耗时: mean {statistics.mean(dur):.0f}s  median {statistics.median(dur):.0f}s  min {min(dur):.0f}s  max {max(dur):.0f}s")
    print(f"done 成本: mean ¥{statistics.mean(cost):.3f}  min ¥{min(cost):.3f}  max ¥{max(cost):.3f}  合计 ¥{sum(cost):.2f}")

    fdur = [(t.get("total_duration_ms") or 0) / 1000 for t in fail]
    fcost = [t.get("total_cost_cny") or 0 for t in fail]
    if fdur:
        print(f"failed 耗时 mean {statistics.mean(fdur):.0f}s；failed 成本 mean ¥{statistics.mean(fcost):.3f}  合计 ¥{sum(fcost):.2f}（废稿成本）")
    print(f"全批 LLM 总成本 ¥{sum(cost)+sum(fcost):.2f}")

    print("\n-- verdict 分布（12 条产出）--")
    print(dict(Counter(c.get("verdict") for c in contents)))

    print("\n-- Rubric 五维（真实评分）--")
    agg = defaultdict(list)
    for cid, det in details.items():
        for k in DIMS:
            v = (det.get("scores") or {}).get(k)
            if v:
                agg[k].append(v)
    for k in DIMS:
        if agg[k]:
            print(f"  {k:<12} mean {statistics.mean(agg[k]):.2f}  n={len(agg[k])}")

    print("\n-- 分市场 --")
    md = defaultdict(list)
    for c in contents:
        if c.get("quality_avg"):
            md[c["market"]].append(c["quality_avg"])
    for m, v in sorted(md.items(), key=lambda x: -statistics.mean(x[1])):
        print(f"  {m}  mean {statistics.mean(v):.2f}  n={len(v)}")

    print("\n-- 信号可溯源性 --")
    has = sum(1 for det in details.values() if det.get("signals"))
    print(f"  带真实信号的产出: {has}/{len(details)}；无信号(降级链路): {len(details)-has}/{len(details)}")

    print("\n-- failed 归因原文 --")
    for t in fail:
        print(f"  [{t['market']}] {(t.get('error') or '')[:100]}")


if __name__ == "__main__":
    main()
