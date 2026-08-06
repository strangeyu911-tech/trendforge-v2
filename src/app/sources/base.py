"""真实信号源基础设施：RawSignal 数据类 + HTTP 工具 + 本地缓存 + 去重合并

设计要点（面试话术核心）：
- 三个公开数据源（HN Algolia / Dev.to / GDELT）全部免费、无需 API Key
- HN / Dev.to 的 points / reactions / comments 是**真实的人类内容消费信号**
- 任何单源失败（限流 / 封禁 / 超时）都静默降级，主链路永不裸崩
- 抓取结果落本地缓存（TTL），既加速冷启动又规避限流
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import DATA_DIR

HTTP_TIMEOUT = 15.0
# 合规 UA：说明用途与仓库，避免被当成恶意爬虫
UA = "TrendForgeBot/1.0 (academic research; +https://github.com/strangeyu911-tech/trendforge-v2)"
CACHE_DIR = DATA_DIR / "signal_cache"
CACHE_TTL_HOURS = 6


@dataclass
class RawSignal:
    """单条真实信号（来源无关的统一结构）"""
    title: str
    url: str
    source: str                 # 平台 / 媒体名
    published_at: str           # YYYY-MM-DD
    country: str                # 市场码 US/JP/KR/BR/CN
    language: str               # 源语言
    category: str               # 推断的项目类目
    engagement: dict = field(default_factory=dict)   # {score, comments, tone?}
    raw_lang: str = ""
    snippet: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RawSignal":
        d = dict(d)
        d.setdefault("engagement", {})
        return cls(**d)


async def http_get_json(url: str, *, params: dict | None = None,
                        timeout: float = HTTP_TIMEOUT) -> dict:
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cache(market_code: str) -> list[dict] | None:
    p = CACHE_DIR / f"{market_code}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = data.get("saved_at")
        if ts:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
            if age > CACHE_TTL_HOURS:
                return None
        return data.get("signals")
    except Exception:
        return None


def save_cache(market_code: str, signals: list[RawSignal]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = CACHE_DIR / f"{market_code}.json"
        p.write_text(json.dumps(
            {"saved_at": _now_iso(), "signals": [s.to_dict() for s in signals]},
            ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def dedupe_merge(signals: list[RawSignal]) -> list[RawSignal]:
    seen: dict[str, RawSignal] = {}
    for s in signals:
        key = (s.url or "").strip().lower() or hashlib.sha256(s.title.encode()).hexdigest()
        if key in seen:
            old = seen[key]
            old.engagement = {
                "score": max(old.engagement.get("score", 0), s.engagement.get("score", 0)),
                "comments": max(old.engagement.get("comments", 0), s.engagement.get("comments", 0)),
            }
        else:
            seen[key] = s
    return list(seen.values())


def sort_by_engagement(signals: list[RawSignal]) -> list[RawSignal]:
    def key(s: RawSignal) -> int:
        e = s.engagement or {}
        return (e.get("score", 0) or 0) + (e.get("comments", 0) or 0) * 2
    return sorted(signals, key=key, reverse=True)


async def safe_gather(*coros):
    """并发执行，异常转空列表，单源失败不影响其他源"""
    results = await asyncio.gather(*coros, return_exceptions=True)
    return [r if not isinstance(r, Exception) else [] for r in results]
