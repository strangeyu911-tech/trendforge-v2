"""本地主流媒体 RSS 信号源（best-effort）

修复的问题：此前 JP/KR 等非英语市场的信号全部来自 HN/Dev.to（全球英文社区，
country=GLOBAL），"日本市场"实际吃的是英文开发者内容。本模块为各市场接入
**本地语言的本地主流媒体 RSS**，让 SENSE 端真正"说当地话"。

设计原则（与项目抓取哲学一致）：
- 全部为媒体官方公开 RSS，遵守 robots 与频率限制，合规 UA 标明仓库
- best-effort：任一 feed 失败静默跳过，绝不阻塞主链路
- RSS 无互动数据，engagement 留空（不造假数字），排序天然让位于带真实互动的源

feed 列表的可达性随网络环境浮动（如本机网络不可达 NHK/BBC，Render 数据中心可达），
因此每个市场配置多条互为备份，单市场全失败则该市场退化为 GLOBAL 信号。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from app.sources.base import RawSignal, http_get_text

# 每市场：(媒体名, RSS URL, 类目)。语言 = 市场语言（本地媒体本地语言）
FEEDS: dict[str, list[tuple[str, str, str]]] = {
    "JP": [
        ("Yahoo!ニュース", "https://news.yahoo.co.jp/rss/topics/top-picks.xml", "news"),
        ("NHK", "https://www3.nhk.or.jp/rss/news/cat0.xml", "news"),
    ],
    "KR": [
        ("한겨레", "https://www.hani.co.kr/rss/", "news"),
        ("경향신문", "https://www.khan.co.kr/rss/rssdata/total_news.xml", "news"),
    ],
    "BR": [
        ("G1", "https://g1.globo.com/rss/g1/", "news"),
    ],
    "GB": [
        ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "news"),
    ],
    "IN": [
        ("Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "news"),
    ],
}

MARKET_LANGUAGE = {"US": "en", "GB": "en", "IN": "en", "JP": "ja", "KR": "ko", "BR": "pt", "CN": "zh"}


def _parse_published(raw: str) -> str:
    """RSS pubDate (RFC822) → YYYY-MM-DD；解析失败返回空串"""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw.strip()).strftime("%Y-%m-%d")
    except Exception:
        return raw[:10] if len(raw) >= 10 else ""


def _parse_feed(xml_text: str) -> list[dict]:
    """容错解析 RSS 2.0 / RDF / Atom，统一取 title/link/pubDate"""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    # RSS 2.0 / RDF：channel 下的 item；Atom：feed 下的 entry
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        d = {}
        for child in it.iter():
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title" and child.text:
                d["title"] = child.text.strip()
            elif ctag == "link" and (child.text or "").strip():
                d["link"] = child.text.strip()
            elif ctag == "pubDate" and child.text:
                d["pubDate"] = child.text
            elif ctag in ("published", "updated") and child.text:
                d["pubDate"] = d.get("pubDate") or child.text
            elif ctag == "description" and child.text:
                d["desc"] = child.text
            elif ctag == "summary" and child.text:
                d["desc"] = d.get("desc") or child.text
        # Atom entry 的 link 是属性
        for child in it.iter():
            if child.tag.rsplit("}", 1)[-1] == "link" and (child.get("href") or ""):
                d["link"] = child.get("href")
                break
        if d.get("title"):
            items.append(d)
    return items


async def fetch_rss_local(market_code: str, *, limit_per_feed: int = 10) -> list[RawSignal]:
    feeds = FEEDS.get(market_code, [])
    if not feeds:
        return []
    language = MARKET_LANGUAGE.get(market_code, "en")
    out: list[RawSignal] = []
    for name, url, cat in feeds:
        try:
            xml_text = await http_get_text(url)
        except Exception:
            continue  # best-effort：单 feed 失败静默跳过
        for d in _parse_feed(xml_text)[:limit_per_feed]:
            out.append(RawSignal(
                title=d.get("title") or "(untitled)",
                url=d.get("link") or "", source=name,
                # 本地媒体：来源地区 = 市场国别（这是真正的本地内容信号）
                country=market_code, language=language, category=cat,
                published_at=_parse_published(d.get("pubDate", "")),
                engagement={},  # RSS 无互动数据，留空不造假
                raw_lang=language,
                snippet=(d.get("desc") or d.get("title") or "")[:300],
            ))
    return out
