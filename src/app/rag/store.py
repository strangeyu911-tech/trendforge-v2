"""知识库：KB markdown 采集（frontmatter 解析）、分块、BM25 检索、新鲜度治理"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.config import DATA_DIR, KB_DIR
from app.models import Chunk, Document, SessionLocal
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


def _doc_hash(url: str, title: str) -> str:
    return hashlib.sha256((url or title).encode()).hexdigest()


def _iso(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(str(d)[:10]).date()
    except Exception:
        return None


def _reference_date(docs: list[Document]) -> date | None:
    dates = [_iso(d.published_at) for d in docs]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


async def ingest_document(session, *, title: str, source: str = "", url: str = "",
                          category: str = "general", country: str = "", language: str = "en",
                          credibility: int = 2, published_at: str = "", body: str = "",
                          verified_at: str | None = None, ttl: int = 90) -> int:
    """单篇入库（补丁应用 / 候选采纳用），幂等（按 url/title 去重）"""
    h = _doc_hash(url, title)
    exists = await session.scalar(select(Document.id).where(Document.hash == h))
    if exists:
        return 0
    doc = Document(
        title=title, source=source, url=url, category=category, country=country,
        language=language, credibility=int(credibility or 2),
        published_at=published_at, hash=h,
        last_verified_at=verified_at or published_at or date.today().isoformat(),
        freshness_ttl=int(ttl or 90),
    )
    session.add(doc)
    await session.flush()
    for i, c in enumerate(_chunk(body)):
        session.add(Chunk(doc_id=doc.id, idx=i, section=title, text=c))
    return 1


async def ingest_kb(session: AsyncSession) -> int:
    """把 data/kb/*.md 灌入 documents/chunks（hash 去重，幂等）。返回新增文档数"""
    added = 0
    for path in sorted(KB_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title", path.stem)
        h = _doc_hash(meta.get("url", ""), title)
        exists = await session.scalar(select(Document.id).where(Document.hash == h))
        if exists:
            continue
        doc = Document(
            title=title, source=meta.get("source", ""), url=meta.get("url", ""),
            category=meta.get("category", "general"), country=meta.get("country", ""),
            language=meta.get("language", "en"),
            credibility=int(meta.get("credibility", "2") or 2),
            published_at=meta.get("published_at", ""), hash=h,
            last_verified_at=meta.get("verified_at") or meta.get("published_at", "") or "",
            freshness_ttl=int(meta.get("ttl", "90") or 90),
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
                       language: str | None = None,
                       include_retired: bool = False) -> list[tuple[Chunk, Document]]:
    q = select(Chunk, Document).join(Document, Chunk.doc_id == Document.id)
    if not include_retired:
        q = q.where(Document.retired == False)  # 退役文档不参与检索
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


def _freshness(doc: Document, ref: date | None) -> tuple[bool, int]:
    """返回 (is_stale, age_days)。verified = 最后核实日，超 ttl 即过期"""
    verified = _iso(doc.last_verified_at) or _iso(doc.published_at)
    pub = _iso(doc.published_at)
    age = (ref - pub).days if (ref and pub) else 0
    if not (ref and verified and doc.freshness_ttl):
        return False, age
    is_stale = (ref - verified).days > doc.freshness_ttl
    return is_stale, age


async def retrieve(session: AsyncSession, query: str, *, top_k: int = 10,
                   days: int | None = None, category: str | None = None,
                   language: str | None = None) -> list[dict]:
    """BM25 检索 + 新鲜度衰减：过期文档降权但仍可召回（可溯源红线）"""
    rows = await _load_chunks(session, days=days, category=category, language=language)
    if not rows:
        return []
    ref = _reference_date([d for _, d in rows])
    bm = BM25().fit([c.text for c, _ in rows])
    hits = bm.top_k(query, k=top_k)
    out = []
    for rank, (i, score) in enumerate(hits, 1):
        chunk, doc = rows[i]
        is_stale, age = _freshness(doc, ref)
        eff = round(score * (0.4 if is_stale else 1.0), 3)  # 过期降权
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
            "last_verified_at": doc.last_verified_at,
            "is_stale": is_stale,
            "age_days": age,
            "score": round(score, 3),
            "score_adj": eff,
        })
    out.sort(key=lambda x: x["score_adj"], reverse=True)
    return out


async def collect_kb_state(session: AsyncSession) -> dict:
    """供 KBCurator 使用的知识库状态快照：覆盖度 + 过期清单"""
    docs = (await session.execute(select(Document))).scalars().all()
    ref = _reference_date(docs)
    by_category: dict[str, int] = {}
    by_country: dict[str, int] = {}
    stale: list[dict] = []
    for d in docs:
        if d.retired:
            continue
        by_category[d.category] = by_category.get(d.category, 0) + 1
        by_country[d.country] = by_country.get(d.country, 0) + 1
        is_stale, age = _freshness(d, ref)
        if is_stale:
            stale.append({
                "id": d.id, "title": d.title, "category": d.category,
                "country": d.country, "published_at": d.published_at,
                "last_verified_at": d.last_verified_at, "ttl": d.freshness_ttl,
                "age_days": age,
            })
    return {
        "total": len([d for d in docs if not d.retired]),
        "by_category": by_category, "by_country": by_country,
        "ref_date": ref.isoformat() if ref else "",
        "stale": stale,
    }


async def apply_kb_patch(session: AsyncSession, patch: dict) -> dict:
    """应用已审核补丁：add=入库候选文档；retire=标记退役（软删，不参与检索）"""
    added = 0
    retired = 0
    for item in (patch.get("items") or []):
        action = item.get("action")
        if action == "add":
            added += await ingest_document(
                session, title=item["title"], source=item.get("source", ""),
                url=item.get("url", ""), category=item.get("category", "general"),
                country=item.get("country", ""), language=item.get("language", "en"),
                credibility=int(item.get("credibility", 2) or 2),
                published_at=item.get("published_at", ""), body=item.get("body", ""),
                ttl=int(item.get("ttl", 90) or 90),
            )
        elif action == "retire":
            target = item.get("title") or item.get("replaces") or ""
            if target:
                d = (await session.execute(
                    select(Document).where(Document.title == target)
                )).scalars().first()
                if d and not d.retired:
                    d.retired = True
                    retired += 1
    await session.commit()
    return {"added": added, "retired": retired}


async def retrieve_scores(ctx, candidates: list[dict], query: str) -> list[tuple[dict, float]]:
    """对已召回的候选证据按新 query 重新打 BM25 分（主题相关性过滤用）"""
    if not candidates:
        return []
    bm = BM25().fit([c["text"] for c in candidates])
    scores = bm.scores(query)
    pairs = list(zip(candidates, scores))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs


def load_candidates() -> list[dict]:
    """待审候选源（模拟"流入信号"，不自爬网络）：运营/PM 维护的候选文档池"""
    path = DATA_DIR / "kb_candidates.json"
    if not path.exists():
        return []
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


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
