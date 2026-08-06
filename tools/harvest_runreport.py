"""TrendForge V2 — 收割部署实例上的真实运行数据，供 RUN_REPORT 汇总

- GET /api/contents?limit=300  → 列表（含 quality_avg/verdict/market/is_fallback/created_at/formats）
- 对每个 done 内容的详情 GET /api/contents/{id} → 取 signals(lead time) 与 quality.scores(五维)
- GET /api/pipeline/tasks?limit=100 → tasks(status/duration/cost/error/market/progress)
- 落盘 /tmp/tf_harvest.json
"""
from __future__ import annotations

import json
import sys
import urllib.request

API = "https://trendforge-v2-api.onrender.com"


def _get(path, timeout=90):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tf_harvest.json"
    contents = _get("/api/contents?limit=300").get("contents", [])
    tasks = _get("/api/pipeline/tasks?limit=100").get("tasks", [])

    # 详情：只对 list 里出现的 content 取五维 + signals
    details = {}
    for c in contents:
        cid = c.get("id")
        if not cid:
            continue
        try:
            d = _get(f"/api/contents/{cid}")
            details[cid] = {
                "signals": d.get("signals", []),
                "scores": (d.get("quality") or {}).get("scores", {}),
                "fact_check": (d.get("quality") or {}).get("fact_check", {}),
                "verdict": (d.get("quality") or {}).get("verdict", ""),
                "avg": (d.get("quality") or {}).get("avg", 0),
            }
        except Exception as e:
            details[cid] = {"error": str(e)[:120]}

    payload = {"contents": contents, "tasks": tasks, "details": details}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n_done = sum(1 for t in tasks if t.get("status") == "done")
    n_fail = sum(1 for t in tasks if t.get("status") == "failed")
    print(f"[harvest] contents={len(contents)} tasks={len(tasks)} "
          f"task_done={n_done} task_failed={n_fail} details={len(details)} -> {out}", flush=True)


if __name__ == "__main__":
    main()
