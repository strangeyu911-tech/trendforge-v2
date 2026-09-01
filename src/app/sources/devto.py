"""Dev.to 真实信号源

Dev.to 是开发者 / AI / 商业内容社区，公开 API 对 datacenter 友好、无需 Key。
字段含 positive_reactions_count（真实点赞）与 comments_count（真实讨论），可按 tag 覆盖类目。
"""
from __future__ import annotations

from app.sources.base import RawSignal, http_get_json

DEVTO = "https://dev.to/api/articles"

MARKET_TAGS: dict[str, list[str]] = {
    "US": ["ai", "webdev", "programming", "business"],
    "JP": ["ai", "webdev", "python"],
    "KR": ["ai", "webdev", "programming"],
    "BR": ["ai", "webdev", "brasil", "programming"],
    "CN": ["ai", "webdev", "python", "programming"],
}
TAG_CATEGORY = {"ai": "ai", "webdev": "tech", "programming": "tech",
                "python": "tech", "business": "business", "brasil": "business"}


async def fetch_devto(market_code: str, *, limit_per_tag: int = 10) -> list[RawSignal]:
    out: list[RawSignal] = []
    for tag in MARKET_TAGS.get(market_code, ["ai"]):
        try:
            data = await http_get_json(DEVTO, params={
                "tag": tag, "state": "rising", "per_page": limit_per_tag})
        except Exception:
            continue
        for a in data:
            out.append(RawSignal(
                title=a.get("title") or "(untitled)",
                url=a.get("url") or "", source="Dev.to",
                # Dev.to 是全球开发者社区：按 tag 检索 ≠ 本地内容，来源地区如实标 GLOBAL
                country="GLOBAL",
                language="en", category=TAG_CATEGORY.get(tag, "tech"),
                published_at=(a.get("published_at") or "")[:10],
                engagement={"score": a.get("positive_reactions_count") or 0,
                            "comments": a.get("comments_count") or 0},
                raw_lang="en", snippet=a.get("description") or "",
            ))
    return out
