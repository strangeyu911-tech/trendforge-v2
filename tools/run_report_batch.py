"""TrendForge V2 — RUN_REPORT 真实批量跑数脚本

用部署实例（自带 DeepSeek key）跑真实供给流水线，拉取每条内容的
真实质量 Rubric / 信号溯源 / Trace 成本耗时，落盘供 RUN_REPORT 汇总。

用法:
  python tools/run_report_batch.py [markets_csv] [per_market] [out_json]
  python tools/run_report_batch.py US,JP,KR,BR,CN 6 /tmp/tf_runreport.json

- 每个市场跑 per_market 次（force=True 绕过市场级缓存，保证不同选题）
- 并发上限 3，避免免费实例被打爆
- 每条完成后即时写盘（results 列表），中途失败不丢已完成数据
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

API = "https://trendforge-v2-api.onrender.com"
POLL_SLEEP = 15
POLL_MAX = 80  # 最多 ~20 分钟等一条（免费实例冷启 + 顺序 LLM 较慢）


def _req(method: str, path: str, data=None, timeout=90):
    url = API + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def run_one(market: str) -> dict:
    rec = {"market": market, "t": time.strftime("%H:%M:%S")}
    try:
        res = _req("POST", "/api/pipeline/run", {"market": market, "force": True})
        job_id = res.get("job_id")
        if not job_id:
            rec.update(error="no_job_or_cached", raw=res)
            return rec
        rec["job_id"] = job_id
        job = {}
        for _ in range(POLL_MAX):
            time.sleep(POLL_SLEEP)
            job = _req("GET", f"/api/pipeline/jobs/{job_id}")
            if job.get("status") in ("done", "failed"):
                break
        rec["job_status"] = job.get("status")
        if job.get("status") != "done":
            rec["error"] = "job_not_done"
            rec["job"] = job
            return rec
        cid = (job.get("result") or {}).get("content_id")
        if not cid:
            rec["error"] = "no_content_id"; rec["job"] = job; return rec
        rec["content_id"] = cid
        rec["detail"] = _req("GET", f"/api/contents/{cid}")
        try:
            rec["trace"] = _req("GET", f"/api/contents/{cid}/trace")
        except Exception as e:
            rec["trace_error"] = str(e)[:200]
        rec["ok"] = True
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return rec


def main():
    markets = (sys.argv[1] if len(sys.argv) > 1 else "US,JP,KR,BR,CN").split(",")
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/tf_runreport.json"

    jobs = [(m, i) for m in markets for i in range(per)]
    results = []
    print(f"[batch] {len(jobs)} runs across {markets} x{per} -> {out}", flush=True)

    def _save():
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"markets": markets, "per": per, "results": results}, f,
                      ensure_ascii=False, indent=2)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(run_one, m): (m, i) for (m, i) in jobs}
        done = 0
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            done += 1
            ok = "OK" if rec.get("ok") else f"ERR:{rec.get('error','?')}"
            print(f"[batch] {done}/{len(jobs)} {rec['market']} {ok}", flush=True)
            _save()  # 即时落盘
    print(f"[batch] DONE. {sum(1 for r in results if r.get('ok'))}/{len(results)} ok", flush=True)


if __name__ == "__main__":
    main()
