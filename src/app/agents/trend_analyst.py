"""2. TrendAnalyst 趋势研判：信号聚类→趋势，评估热度/生命周期/跨市场潜力"""
from __future__ import annotations

import json
from collections import Counter

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm


class TrendAnalystAgent(BaseAgent):
    name = "trend_analyst"
    prompt_name = "trend_analyst"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        signals = inputs["signals"]
        system, user = get_pm().render(
            self.prompt_name, market=ctx.market.name, market_code=ctx.market.code,
            interests=json.dumps(ctx.market.interests, ensure_ascii=False),
            signals=json.dumps(signals, ensure_ascii=False, indent=1),
            top_n=settings.top_trends,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        trends = data.get("trends") or []
        if not isinstance(trends, list) or not trends:
            raise AgentError(self.name, "LLM 未返回有效趋势")
        norm = [{
            "topic": str(t.get("topic", ""))[:120],
            "summary": str(t.get("summary", "")),
            "heat": _int(t.get("heat"), 5),
            "lifecycle": str(t.get("lifecycle", "rising")),      # emerging/rising/peak/declining
            "cross_market": bool(t.get("cross_market", False)),
            "categories": t.get("categories") or [],
            "signal_titles": t.get("signal_titles") or [],
        } for t in trends[: settings.top_trends]]
        return {
            "trends": norm, "_llm_resp": resp,
            "_decision": {"reason": f"{len(signals)} 条信号聚为 {len(norm)} 个趋势，"
                                   f"最热：{norm[0]['topic']}（热度 {norm[0]['heat']}/10）",
                          "details": {"trend_count": len(norm)}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        signals = inputs.get("signals", [])
        by_cat = Counter(s.get("category", "general") for s in signals)
        trends = []
        for cat, cnt in by_cat.most_common(settings.top_trends):
            reps = [s for s in signals if s.get("category") == cat][:3]
            trends.append({
                "topic": f"{cat} 领域热点（{reps[0]['title'][:30]}等）",
                "summary": "；".join(r["title"] for r in reps),
                "heat": min(4 + cnt, 10), "lifecycle": "rising",
                "cross_market": False, "categories": [cat],
                "signal_titles": [r["title"] for r in reps],
            })
        return {"trends": trends,
                "_decision": {"reason": f"兜底：按类目频次聚合出 {len(trends)} 个趋势"}}


def _int(v, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d
