import json, re, urllib.request, urllib.error

BASE = "http://127.0.0.1:8013"

# 1) fetch samples
with urllib.request.urlopen(BASE + "/api/calibration/samples", timeout=30) as r:
    data = json.loads(r.read())
samples = data["samples"]
print("samples:", len(samples))

# 2) mimic EXACTLY what frontend calSubmit sends: scores[id][dim] = {score:float, reason:str}
DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
scores = {}
for i, s in enumerate(samples):
    scores[s["id"]] = {}
    for d in DIMS:
        # give human scores correlated with a plausible judge-ish value (3.0-4.5, 0.5 step)
        v = round((3.0 + (i % 4) * 0.5) * 2) / 2
        reason = f"测试理由-{DIMS.index(d)+1}：该维表现{'较好' if v>=4 else '中等'}"
        scores[s["id"]][d] = {"score": v, "reason": reason}

payload = {"rater": "HUMAN", "scores": scores}
req = urllib.request.Request(BASE + "/api/calibration/scores",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=120) as r:
    res = json.loads(r.read())
print("POST result:", json.dumps(res, ensure_ascii=False))

# 3) fetch report and check it contains reasons section
with urllib.request.urlopen(BASE + "/api/calibration/report", timeout=30) as r:
    rep = json.loads(r.read())
md = rep["markdown"]
print("report len:", len(md))
print("has '真人评分理由' section:", "真人评分理由" in md)
print("has chart svg:", bool(rep.get("chart")) and "<svg" in rep["chart"])
# show a couple reason lines
for line in md.split("\n"):
    if "测试理由" in line:
        print("  reason-line:", line.strip()[:80])
        break
