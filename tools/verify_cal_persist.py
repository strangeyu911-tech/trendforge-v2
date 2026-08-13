"""验证真人校准分持久化：落库 / 同名覆盖 / 跨人累积 / 聚合均值 / 对齐计算 / 报告。"""
import json, sqlite3, urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8015"
DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
DB = Path(__file__).resolve().parents[1] / "src" / "app" / "data" / "trendforge_v2.db"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def build(rater, val, reason="t"):
    s = get("/api/calibration/samples")
    ids = [x["id"] for x in s["samples"]]
    scores = {cid: {d: {"score": float(val), "reason": reason} for d in DIMS} for cid in ids}
    return {"rater": rater, "scores": scores}, len(ids)


print("=== 1) GET samples (初始 n_raters 应全为 0) ===")
s0 = get("/api/calibration/samples")
print("count:", s0["count"], "| n_raters:", [x["n_raters"] for x in s0["samples"]])

print("\n=== 2) POST rater=Strange 全 4.0 ===")
p, n = build("Strange", 4.0, "init")
r1 = post("/api/calibration/scores", p)
print("ok:", r1["ok"], "| n:", r1["n"])
pc = r1["per_content"]
print("per_content sample n_raters:", {k: pc[k]["n_raters"] for k in list(pc)[:2]}, "| avg:", {k: pc[k]["avg"]["accuracy"] for k in list(pc)[:2]})

print("\n=== 3) POST rater=Strange 全 2.0（同名应覆盖，n_raters 仍=1，avg=2.0）===")
p, _ = build("Strange", 2.0, "revise")
r2 = post("/api/calibration/scores", p)
pc2 = r2["per_content"]
print("per_content n_raters:", {k: pc2[k]["n_raters"] for k in list(pc2)[:2]}, "| avg(accuracy):", {k: pc2[k]["avg"]["accuracy"] for k in list(pc2)[:2]})

print("\n=== 4) POST rater=评审B 全 3.0（跨人累积，n_raters=2，avg=(2+3)/2=2.5）===")
p, _ = build("评审B", 3.0, "b")
r3 = post("/api/calibration/scores", p)
pc3 = r3["per_content"]
print("per_content n_raters:", {k: pc3[k]["n_raters"] for k in list(pc3)[:2]}, "| avg(accuracy):", {k: pc3[k]["avg"]["accuracy"] for k in list(pc3)[:2]})
print("overall_rho:", r3.get("overall_rho"), "| overall_adj:", r3.get("overall_adj"))

print("\n=== 5) GET report ===")
rep = get("/api/calibration/report")
print("markdown len:", len(rep["markdown"]), "| has '整体 Spearman':", "整体 Spearman" in rep["markdown"], "| chart:", bool(rep["chart"]))

print("\n=== 6) DB 状态核对 ===")
con = sqlite3.connect(str(DB)); cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM human_calibrations")
hc = cur.fetchone()[0]
print("human_calibrations 行数 (期望 13*3=39):", hc)
cur.execute("SELECT content_id, rater, scores FROM human_calibrations ORDER BY content_id, rater")
rows = cur.fetchall()
# 统计每个 content 的不同 rater 数
from collections import defaultdict
per = defaultdict(set)
for cid, rater, _ in rows:
    per[cid].add(rater)
print("不同 content 的 distinct rater 集合:", {c: sorted(rs) for c, rs in list(per.items())[:2]})
cur.execute("SELECT human_score_avg FROM contents WHERE id=?", (list(per.keys())[0],))
hsa = json.loads(cur.fetchone()[0] or "{}")
print("某 content human_score_avg:", hsa)
con.close()

print("\n=== 断言 ===")
assert hc == 39, f"human_calibrations 应为 39，实际 {hc}"
assert hsa["n_raters"] == 2, f"n_raters 应=2，实际 {hsa.get('n_raters')}"
assert abs(hsa["accuracy"] - 2.5) < 1e-6, f"accuracy avg 应=2.5，实际 {hsa.get('accuracy')}"
assert all(v["n_raters"] == 2 for v in pc3.values()), "per_content n_raters 应全=2"
assert "整体 Spearman" in rep["markdown"]
print("ALL ASSERTIONS PASSED ✅")
