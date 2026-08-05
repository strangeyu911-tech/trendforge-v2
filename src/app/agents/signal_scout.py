"""1. SignalScout 信号捕捉：从近期知识库文档归一化出信号流"""
from __future__ import annotations

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.store import recent_documents


class SignalScoutAgent(BaseAgent):
    name = "signal_scout"
    prompt_name = "signal_scout"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        docs = await recent_documents(ctx.session, days=12, limit=50)
        if not docs:
            raise AgentError(self.name, "知识库为空，无信号源")
        doc_lines = "\n".join(
            f"- [{d.id}] ({d.category}|{d.country}|{d.published_at}) {d.title} — {d.source}"
            for d in docs
        )
        system, user = get_pm().render(
            self.prompt_name, market=ctx.market.name, market_code=ctx.market.code,
            language=ctx.market.language, docs=doc_lines, top_n=settings.top_signals,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        signals = data.get("signals") or []
        if not isinstance(signals, list) or not signals:
            raise AgentError(self.name, "LLM 未返回有效信号")
        # 归一化 + 挂上文档元数据
        doc_map = {d.id: d for d in docs}
        norm = []
        for s in signals[: settings.top_signals]:
            ref = doc_map.get(_safe_int(s.get("doc_id")))
            norm.append({
                "title": str(s.get("title", ""))[:120],
                "angle_hint": str(s.get("angle_hint", "")),
                "category": (ref.category if ref else s.get("category", "general")),
                "country": (ref.country if ref else ""),
                "source": (ref.source if ref else ""),
                "published_at": (ref.published_at if ref else ""),
                "strength": _safe_int(s.get("strength"), 5),
                "doc_id": ref.id if ref else None,
            })
        return {
            "signals": norm, "_llm_resp": resp,
            "_decision": {"reason": f"从 {len(docs)} 篇近期文档提取 {len(norm)} 条信号",
                          "details": {"doc_count": len(docs)}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        docs = await recent_documents(ctx.session, days=12, limit=settings.top_signals)
        signals = [{
            "title": d.title, "angle_hint": "", "category": d.category,
            "country": d.country, "source": d.source,
            "published_at": d.published_at, "strength": 5, "doc_id": d.id,
        } for d in docs]
        return {"signals": signals,
                "_decision": {"reason": f"兜底：按发布时间取最新 {len(signals)} 篇作为信号"}}


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
