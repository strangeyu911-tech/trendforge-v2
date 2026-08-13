import json, re, urllib.request, urllib.error, random, sys

BASE = "http://127.0.0.1:8013"

def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]

print("=== GET /api/calibration/samples ===")
st, data = get("/api/calibration/samples")
print("HTTP", st)
if isinstance(data, dict):
    print("count=", data.get("count"))
    s = data.get("samples", [])
    if s:
        s0 = s[0]
        print("keys=", sorted(s0.keys()))
        print("sample0 id/market/lang/title=", s0.get("id"), s0.get("market"), s0.get("language"), (s0.get("title") or "")[:40])
        leaks = {x["id"]: len(re.findall(r"\[ev_\d+\]", x.get("excerpt", ""))) for x in s if re.search(r"\[ev_\d+\]", x.get("excerpt", ""))}
        print("excerpt [ev_] leaks:", leaks if leaks else "NONE")
        print("sample0 excerpt length:", len(s0.get("excerpt", "")))
        print("sample0 excerpt head:", (s0.get("excerpt", "")[:120]).replace("\n", " "))

    # Build plausible human scores (0.5 step) to test compute path
    DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
    scores = {}
    for x in s:
        sc = {}
        for d in DIMS:
            v = round(random.uniform(2.5, 4.5) * 2) / 2
            sc[d] = v
        scores[x["id"]] = sc
    print("\n=== POST /api/calibration/scores ===")
    st2, res = post("/api/calibration/scores", {"rater": "HUMAN_VERIFY", "scores": scores})
    print("HTTP", st2)
    print("result:", json.dumps(res, ensure_ascii=False)[:400])

    print("\n=== GET /api/calibration/report ===")
    st3, rep = get("/api/calibration/report")
    print("HTTP", st3)
    if isinstance(rep, dict):
        md = rep.get("markdown", "")
        print("markdown len:", len(md))
        print("markdown head:", md[:200].replace("\n", " "))
        print("has chart:", bool(rep.get("chart")))
else:
    print("UNEXPECTED:", str(data)[:200])
