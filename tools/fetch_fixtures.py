# -*- coding: utf-8 -*-
"""抓取冒烟测试用的真实 API 响应（Node fetch 在沙箱被拦，改用本地 curl/python 抓好后回放）。"""
import json
import os
import re
import urllib.parse
import urllib.request

BASE = os.environ.get("TF_FIX_BASE", "http://127.0.0.1:8013/api")
FX = "D:/tmp/fx"
ID = "b41a0c5c-053b-4477-aa55-6da25636d530"

PATHS = [
    "/health", "/markets", "/contents?limit=200", f"/contents/{ID}", f"/contents/{ID}/trace",
    "/pipeline/tasks?limit=50",
    "/prompts/suggestions?status=pending", "/prompts/suggestions?status=adopted",
    "/prompts/suggestions?status=all", "/prompts/templates",
    "/prompts/versions?template=writer", "/prompts/adoption-impact",
    "/analytics/overview", "/analytics/reports", "/analytics/center",
    "/kb/stats", "/kb/documents", "/kb/freshness", "/kb/patches",
    "/calibration/samples", "/calibration/report", "/bad-cases",
]


def sanitize(u: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", re.sub(r"https?://", "", u))


os.makedirs(FX, exist_ok=True)
for p in PATHS:
    url = BASE + p
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = r.read()
        # 冒烟脚本按 API_BASE（恒为 localhost:8000）生成文件名，这里把实际端口对齐过去
        path = os.path.join(FX, sanitize(url.replace(f":{urllib.parse.urlsplit(BASE).port}", ":8000")) + ".json")
        with open(path, "wb") as f:
            f.write(body)
        print(f"{r.status}  {len(body):>6}B  {p}")
    except Exception as e:
        print(f"ERR  {p}: {e}")
