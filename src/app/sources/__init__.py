"""真实信号源统一入口

fetch_market_signals(market_code, interests, language) -> (signals, diagnostics)

流程：
1. 命中本地缓存则直接返回（加速冷启动 + 规避限流）
2. 否则并行拉取 HN + Dev.to + GDELT（best-effort）+ 本地媒体 RSS（best-effort）
3. 合并去重 → 按真实互动量排序 → 截断 → 写缓存
4. 任一真实源可用即视为成功；全失败返回空（由 SignalScout 降级到本地 KB）
"""
from __future__ import annotations

from app.config import settings
from app.sources.base import (RawSignal, dedupe_merge, load_cache, safe_gather,
                              save_cache, sort_by_engagement)
from app.sources.devto import fetch_devto
from app.sources.gdelt import fetch_gdelt
from app.sources.hackernews import fetch_hn
from app.sources.rss_local import fetch_rss_local


async def fetch_market_signals(market_code: str, interests: dict | None = None,
                               language: str = "en") -> tuple[list[RawSignal], dict]:
    diag = {"hn": 0, "devto": 0, "gdelt": 0, "rss": 0, "cache_hit": False, "degraded": False}

    cached = load_cache(market_code)
    if cached:
        diag["cache_hit"] = True
        return [RawSignal.from_dict(d) for d in cached], diag

    hn, dev, gd, rss = await safe_gather(
        fetch_hn(market_code), fetch_devto(market_code),
        fetch_gdelt(market_code), fetch_rss_local(market_code))
    diag["hn"], diag["devto"], diag["gdelt"], diag["rss"] = len(hn), len(dev), len(gd), len(rss)

    merged = dedupe_merge(list(hn) + list(dev) + list(gd) + list(rss))
    if not merged:
        diag["degraded"] = True
        return [], diag

    merged = sort_by_engagement(merged)[: settings.top_signals * 2]
    save_cache(market_code, merged)
    return merged, diag
