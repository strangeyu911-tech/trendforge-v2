"""临时探测：确认 GDELT / Hacker News / Reddit 三个公开数据源真实可达、返回字段结构。
不需要 LLM key，不需要第三方库（标准库 urllib）。运行：python tools/probe_sources.py
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request


def _get(url: str, headers: dict = None, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "TrendForgeBot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def probe_gdelt():
    print("=== GDELT DOC 2.0 ===")
    base = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": "(artificial intelligence OR AI) sourcecountry:US",
        "mode": "artlist", "format": "json", "maxrecords": "5", "sort": "datedesc",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    try:
        st, body = _get(url)
        print("HTTP", st, "len", len(body))
        data = json.loads(body)
        arts = data.get("articles", [])
        print("articles:", len(arts))
        if arts:
            print(json.dumps(arts[0], ensure_ascii=False, indent=2)[:900])
    except Exception as e:
        print("ERR", repr(e))


def probe_hn():
    print("\n=== Hacker News ===")
    try:
        _, body = _get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = json.loads(body)
        _, ib = _get(f"https://hacker-news.firebaseio.com/v0/item/{ids[0]}.json")
        item = json.loads(ib)
        print(json.dumps(item, ensure_ascii=False)[:600])
    except Exception as e:
        print("ERR", repr(e))


def probe_reddit():
    print("\n=== Reddit ===")
    headers = {"User-Agent": "TrendForgeBot/1.0 (research; contact: strange@example.com)"}
    try:
        st, body = _get("https://www.reddit.com/r/technology/hot.json?limit=3", headers)
        print("HTTP", st)
        data = json.loads(body)
        ch = data["data"]["children"]
        print("children:", len(ch))
        if ch:
            d = ch[0]["data"]
            print({k: d.get(k) for k in ("title", "url", "ups", "num_comments", "subreddit", "created_utc")})
    except Exception as e:
        print("ERR", repr(e))


if __name__ == "__main__":
    probe_gdelt()
    probe_hn()
    probe_reddit()
