"""Hacker News（Algolia API）真实信号源

为什么用 Algolia 而非 Firebase 旧接口：Algolia 端点对服务器 / datacenter IP 友好、
不限流、返回结构稳定，且字段含真实 points（人类投票）与 num_comments（真实讨论量）。
HN 是全球科技 / 创业 / AI 受众最集中的社区之一，其互动数据是「真实人类内容消费信号」。
"""
from __future__ import annotations

import asyncio

from app.sources.base import RawSignal, http_get_json

HN_SEARCH = "https://hn.algolia.com/api/v1/search"

# 市场 → 英文热点关键词（取自各市场 interests 主类目的英文映射）
MARKET_QUERIES: dict[str, list[str]] = {
    "US": ["artificial intelligence", "technology", "startups", "business"],
    "JP": ["Japan AI", "Japan technology", "robotics", "Japanese startups"],
    "KR": ["South Korea AI", "Korean tech", "semiconductor", "K-pop"],
    "BR": ["Brazil technology", "fintech Brazil", "Brazil AI", "soccer tech"],
    "CN": ["China AI", "Chinese technology", "semiconductor China", "electric vehicles"],
    "GB": ["UK technology", "UK AI", "London startups", "fintech UK"],
    "IN": ["India technology", "India AI", "Indian startups", "UPI payments"],
}

# query 关键词 → 项目类目
CATEGORY_FOR = {
    "ai": "ai", "artificial intelligence": "ai", "technology": "tech", "tech": "tech",
    "startups": "business", "business": "business", "robotics": "tech",
    "k-pop": "entertainment", "soccer": "sports", "semiconductor": "tech",
    "fintech": "business", "electric vehicles": "tech", "upi payments": "business",
    "london startups": "business",
}


async def fetch_hn(market_code: str, *, limit_per_query: int = 12) -> list[RawSignal]:
    out: list[RawSignal] = []
    for q in MARKET_QUERIES.get(market_code, ["technology"]):
        data = None
        # 限流（Algolia 超限常返回 400/429）需要退避重试；递增退避给服务端恢复窗口
        for attempt in range(3):
            try:
                data = await http_get_json(HN_SEARCH, params={
                    "query": q, "tags": "story", "hitsPerPage": limit_per_query,
                    "date_range": "past_month"})  # 只取近一月信号，避免历史热门帖拉低"时效性"
                break
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        if not data:
            continue
        cat = "tech"
        for k, v in CATEGORY_FOR.items():
            if k in q.lower():
                cat = v
                break
        for h in data.get("hits", []):
            obj_id = h.get("objectID")
            url = h.get("url") or (f"https://news.ycombinator.com/item?id={obj_id}" if obj_id else "")
            out.append(RawSignal(
                title=h.get("title") or "(untitled)",
                url=url, source="Hacker News",
                # HN 是全球英文社区：按市场关键词检索 ≠ 本地内容，来源地区如实标 GLOBAL
                country="GLOBAL",
                language="en", category=cat,
                published_at=(h.get("created_at") or "")[:10],
                engagement={"score": h.get("points") or 0, "comments": h.get("num_comments") or 0},
                raw_lang="en", snippet=h.get("story_text") or h.get("title") or "",
            ))
    return out
