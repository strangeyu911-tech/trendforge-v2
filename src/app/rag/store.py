"""知识库：KB markdown 采集（frontmatter 解析）、分块、BM25 检索"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import KB_DIR
from app.models import Chunk, Document
from app.rag.bm25 import BM25

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    m = _FM_RE.match(raw.strip())
    if not m:
        return {}, raw
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2).strip()


def _chunk(text: str, max_len: int = 500) -> list[str]:
    """按段落分块，过长段落按句号切，合并短块"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) <= max_len:
            buf = f"{buf}\n{p}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


async def ingest_kb(session: AsyncSession) -> int:
    """把 data/kb/*.md 灌入 documents/chunks（hash 去重，幂等）。返回新增文档数"""
    added = 0
    for path in sorted(KB_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title", path.stem)
        h = hashlib.sha256((meta.get("url") or title).encode()).hexdigest()
        exists = await session.scalar(select(Document.id).where(Document.hash == h))
        if exists:
            continue
        doc = Document(
            title=title, source=meta.get("source", ""), url=meta.get("url", ""),
            category=meta.get("category", "general"), country=meta.get("country", ""),
            language=meta.get("language", "en"), credibility=int(meta.get("credibility", "2") or 2),
            published_at=meta.get("published_at", ""), hash=h,
        )
        session.add(doc)
        await session.flush()
        for i, c in enumerate(_chunk(body)):
            session.add(Chunk(doc_id=doc.id, idx=i, section=title, text=c))
        added += 1
    await session.commit()
    return added


async def kb_stats(session: AsyncSession) -> dict:
    docs = await session.scalar(select(func.count(Document.id))) or 0
    chunks = await session.scalar(select(func.count(Chunk.id))) or 0
    cats = (await session.execute(
        select(Document.category, func.count()).group_by(Document.category))).all()
    return {"documents": docs, "chunks": chunks,
            "by_category": {c: n for c, n in cats}}


async def _load_chunks(session: AsyncSession, days: int | None = None,
                       category: str | None = None,
                       language: str | None = None) -> list[tuple[Chunk, Document]]:
    q = select(Chunk, Document).join(Document, Chunk.doc_id == Document.id)
    if category:
        q = q.where(Document.category == category)
    if language:
        q = q.where(Document.language == language)
    rows = (await session.execute(q)).all()
    if days:
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        # KB 是"近未来"演示数据（2026-07），按数据内最新日期回推更稳
        dates = [d.published_at for _, d in rows if d.published_at]
        if dates:
            latest = max(dates)
            cutoff = (datetime.fromisoformat(latest) - timedelta(days=days)).date().isoformat()
            rows = [(c, d) for c, d in rows if not d.published_at or d.published_at >= cutoff]
    return rows


async def retrieve(session: AsyncSession, query: str, *, top_k: int = 10,
                   days: int | None = None, category: str | None = None,
                   language: str | None = None) -> list[dict]:
    """BM25 检索，返回证据 dict 列表"""
    rows = await _load_chunks(session, days=days, category=category, language=language)
    if not rows:
        return []
    bm = BM25().fit([c.text for c, _ in rows])
    hits = bm.top_k(query, k=top_k)
    out = []
    for rank, (i, score) in enumerate(hits, 1):
        chunk, doc = rows[i]
        out.append({
            "ev_id": f"ev_{rank:03d}",
            "text": chunk.text,
            "doc_title": doc.title,
            "source": doc.source,
            "url": doc.url,
            "category": doc.category,
            "country": doc.country,
            "language": doc.language,
            "credibility": doc.credibility,
            "published_at": doc.published_at,
            "score": round(score, 3),
        })
    return out


async def retrieve_scores(ctx, candidates: list[dict], query: str) -> list[tuple[dict, float]]:
    """对已召回的候选证据按新 query 重新打 BM25 分（主题相关性过滤用）"""
    if not candidates:
        return []
    bm = BM25().fit([c["text"] for c in candidates])
    scores = bm.scores(query)
    pairs = list(zip(candidates, scores))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs


async def recent_documents(session: AsyncSession, days: int = 10, limit: int = 60) -> list[Document]:
    rows = await _load_chunks(session, days=None)
    docs = {d.id: d for _, d in rows}.values()
    docs = sorted(docs, key=lambda d: d.published_at or "", reverse=True)
    if days:
        dates = [d.published_at for d in docs if d.published_at]
        if dates:
            cutoff = (datetime.fromisoformat(max(dates)) - timedelta(days=days)).date().isoformat()
            docs = [d for d in docs if not d.published_at or d.published_at >= cutoff]
    return list(docs)[:limit]
