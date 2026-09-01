"""GDELT DOC 2.0 真实信号源（best-effort）

GDELT 是唯一能按国家码 + 语言覆盖本地新闻的源（市场本地化信号的关键）。
但 datacenter / 共享 IP 常被限流（HTTP 429）甚至封禁，因此本源定位为
「best-effort」：成功则补充本地化新闻，失败/限流则静默返回空，绝不阻塞主链路。
其余两个源（HN / Dev.to）已能稳定提供跨市场的全球科技 / 商业真实信号。
"""
from __future__ import annotations

from app.sources.base import RawSignal, http_get_json

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
FIPS = {"US": "US", "JP": "JA", "KR": "KS", "BR": "BR", "CN": "CH",
        "GB": "UK", "IN": "IN"}
# GDELT sourcelang 参数值：与 sourcecountry 同用，保证 JP/KR/BR/CN 市场拿到的是
# 本地语言新闻，而非"该国出版的英文报道"——本地化信号真实性的关键一步
SOURCELANG = {"US": "english", "JP": "japanese", "KR": "korean",
              "BR": "portuguese", "CN": "chinese", "GB": "english", "IN": "english"}
MARKET_LANGUAGE = {"US": "en", "JP": "ja", "KR": "ko", "BR": "pt", "CN": "zh",
                   "GB": "en", "IN": "en"}
MARKET_QUERY = {
    "US": "(artificial intelligence OR AI OR technology OR business)",
    "JP": "(Japan) (technology OR AI OR business OR robotics)",
    "KR": "(South Korea) (technology OR AI OR semiconductor OR entertainment)",
    "BR": "(Brazil) (technology OR AI OR business OR sports)",
    "CN": "(China) (technology OR AI OR business OR semiconductor)",
    "GB": "(Britain OR UK) (technology OR AI OR business OR finance)",
    "IN": "(India) (technology OR AI OR business OR startups)",
}


async def fetch_gdelt(market_code: str, *, limit: int = 15) -> list[RawSignal]:
    fips = FIPS.get(market_code)
    query = MARKET_QUERY.get(market_code)
    if not fips or not query:
        return []
    lang_param = SOURCELANG.get(market_code, "english")
    try:
        data = await http_get_json(GDELT, params={
            "query": f"{query} sourcecountry:{fips} sourcelang:{lang_param}",
            "mode": "artlist",
            "format": "json", "maxrecords": limit, "sort": "datedesc"})
    except Exception:
        return []  # best-effort：限流/封禁时静默降级
    out: list[RawSignal] = []
    for a in data.get("articles", []):
        tone = a.get("tone")
        out.append(RawSignal(
            title=a.get("title") or "(untitled)",
            url=a.get("url") or "", source=a.get("domain") or "GDELT",
            # sourcecountry 过滤保证来源地区 = 检索国别；语言按市场真实语言记录
            country=market_code, language=MARKET_LANGUAGE.get(market_code, "en"),
            category="news",
            published_at=(a.get("seendate") or "")[:10],
            engagement={"score": 0, "comments": 0,
                        "tone": float(tone) if tone not in (None, "") else None},
            raw_lang=a.get("sourcecountry", ""), snippet=a.get("title") or "",
        ))
    return out
