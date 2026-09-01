"""1. SignalScout 信号捕捉：从真实公开数据源实时拉取信号（入口真实化）

主路径：实时拉取 HN / Dev.to / GDELT 真实信号 → LLM 归纳成带真实溯源字段的信号。
降级：真实源全失败（限流/封禁/断网）→ 退回本地 KB（保留原「近未来」演示语料）。
真实文章同时自动入库 KB（documents），让下游 Researcher 的证据也变真实，
并降低 KB 中虚构 example.com 条目占比（M1 验收项）。
"""
from __future__ import annotations

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.store import ingest_document, recent_documents
from app.sources import fetch_market_signals


class SignalScoutAgent(BaseAgent):
    name = "signal_scout"
    prompt_name = "signal_scout"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        # ---- 主路径：实时真实信号 ----
        raw, diag = await fetch_market_signals(
            ctx.market.code, ctx.market.interests, ctx.market.language)
        if not raw:
            return await self._from_kb(ctx, degraded=True)

        # 组装喂给 LLM 的真实信号文本（含来源 / 发布时间 / 真实互动 / 原文链接）
        lines = "\n".join(
            f"- [{i+1}] ({s.category}|{s.country}|{s.published_at}) {s.title} — {s.source} "
            f"[互动 {s.engagement}] 原文: {s.url}"
            for i, s in enumerate(raw)
        )
        system, user = get_pm().render(
            self.prompt_name, market=ctx.market.name, market_code=ctx.market.code,
            language=ctx.market.language, signals=lines, top_n=settings.top_signals,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        picked = data.get("signals") or []
        if not isinstance(picked, list) or not picked:
            raise AgentError(self.name, "LLM 未返回有效信号")

        # 把 LLM 选出的信号挂上真实元数据（URL / 来源 / 时间 / 互动）
        # country 记录的是内容真实来源地区（GLOBAL=全球英文社区 / 国别=GDELT 按国过滤），
        # 不再盖消费市场码——"为 JP 市场拉的英文信号"不等于"JP 本地内容"
        by_key = {s.title: s for s in raw}
        norm = []
        for r in picked[: settings.top_signals]:
            title = str(r.get("title", ""))[:160]
            ref = by_key.get(title) or next(
                (s for s in raw if title and (title in s.title or s.title in title)), None)
            norm.append({
                "title": title,
                "angle_hint": str(r.get("angle_hint", "")),
                "category": (ref.category if ref else r.get("category", "general")),
                "country": (ref.country if ref else "UNKNOWN"),
                "source": (ref.source if ref else ""),
                "source_url": (ref.url if ref else ""),
                "published_at": (ref.published_at if ref else ""),
                "engagement": (ref.engagement if ref else {}),
                "raw_lang": (ref.raw_lang if ref else ""),
                "strength": _safe_int(r.get("strength"), 5),
                "doc_id": None,
            })

        # ---- 真实文章自动入库 KB（证据真实化 + 降低虚构占比）----
        added = await self._ingest_real(ctx, raw)

        return {
            "signals": norm, "_llm_resp": resp,
            "_decision": {
                "reason": f"实时拉取 {diag['hn']+diag['devto']+diag['gdelt']} 条真实信号"
                          f"（HN {diag['hn']} / Dev.to {diag['devto']} / GDELT {diag['gdelt']}）"
                          f"，LLM 归纳 {len(norm)} 条；回流 KB {added} 篇真实文档",
                "details": {**diag, "kb_added": added, "real_source": True},
            },
        }

    async def _ingest_real(self, ctx: RunContext, raw) -> int:
        """把真实信号文章入库 documents（幂等去重，每市场上限保护，避免 KB 无限膨胀）"""
        added = 0
        # 优先入库带真实外链的（HN/Dev.to 帖子或 GDELT 新闻），上限取 top_signals
        for s in raw[: settings.top_signals]:
            if not s.url:
                continue
            added += await ingest_document(
                ctx.session, title=s.title, source=s.source, url=s.url,
                category=s.category, country=s.country, language=s.language,
                published_at=s.published_at, body=s.snippet or s.title,
                credibility=2, ttl=30,
            )
        if added:
            await ctx.session.commit()
        return added

    async def _from_kb(self, ctx: RunContext, degraded: bool = False) -> dict:
        """兜底：从本地 KB（近未来演示语料）提取信号，保证主链路不裸崩"""
        docs = await recent_documents(ctx.session, days=12, limit=50)
        if not docs:
            raise AgentError(self.name, "知识库为空，无信号源")
        doc_lines = "\n".join(
            f"- [{d.id}] ({d.category}|{d.country}|{d.published_at}) {d.title} — {d.source}"
            for d in docs
        )
        system, user = get_pm().render(
            self.prompt_name, market=ctx.market.name, market_code=ctx.market.code,
            language=ctx.market.language, signals=doc_lines, top_n=settings.top_signals,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        signals = data.get("signals") or []
        if not isinstance(signals, list) or not signals:
            raise AgentError(self.name, "LLM 未返回有效信号")
        doc_map = {d.id: d for d in docs}
        norm = []
        for s in signals[: settings.top_signals]:
            ref = doc_map.get(_safe_int(s.get("doc_id")))
            norm.append({
                "title": str(s.get("title", ""))[:120],
                "angle_hint": str(s.get("angle_hint", "")),
                "category": (ref.category if ref else s.get("category", "general")),
                "country": (ref.country if ref else "UNKNOWN"),
                "source": (ref.source if ref else ""),
                "source_url": (ref.url if ref else ""),
                "published_at": (ref.published_at if ref else ""),
                "engagement": {},
                "raw_lang": (ref.language if ref else ""),
                "strength": _safe_int(s.get("strength"), 5),
                "doc_id": ref.id if ref else None,
            })
        return {
            "signals": norm, "_llm_resp": resp,
            "_decision": {
                "reason": f"真实源不可用，降级从本地 KB {len(docs)} 篇文档提取 {len(norm)} 条信号",
                "details": {"degraded": degraded, "real_source": False},
            },
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        docs = await recent_documents(ctx.session, days=12, limit=settings.top_signals)
        signals = [{
            "title": d.title, "angle_hint": "", "category": d.category,
            "country": d.country or "UNKNOWN", "source": d.source,
            "source_url": d.url, "published_at": d.published_at,
            "engagement": {}, "raw_lang": d.language,
            "strength": 5, "doc_id": d.id,
        } for d in docs]
        return {"signals": signals,
                "_decision": {"reason": f"兜底：按发布时间取最新 {len(signals)} 篇作为信号"}}


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
