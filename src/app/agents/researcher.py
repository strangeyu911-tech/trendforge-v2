"""5. Researcher 素材寻找：query 改写 + BM25 检索，产出可溯源证据集"""
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.store import retrieve


class ResearcherAgent(BaseAgent):
    name = "researcher"
    prompt_name = "researcher"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        brief = inputs["brief"]
        queries = await self._rewrite_queries(ctx, brief)
        evidences = await self._search(ctx, queries)
        if not evidences:
            raise AgentError(self.name, "检索无证据")
        sources = {e["source"] for e in evidences}
        return {
            "evidences": evidences, "queries": queries,
            "_decision": {"reason": f"改写 {len(queries)} 个 query，召回 {len(evidences)} 条证据，"
                                   f"覆盖 {len(sources)} 个来源",
                          "details": {"queries": queries}},
        }

    async def _rewrite_queries(self, ctx: RunContext, brief: dict) -> list[str]:
        try:
            system, user = get_pm().render(
                self.prompt_name,
                topic=brief.get("topic", ""), angle=brief.get("angle", ""),
                keywords=json.dumps(brief.get("keywords") or [], ensure_ascii=False),
            )
            resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=800)
            data = extract_json(resp.text)
            queries = [str(q) for q in (data.get("queries") or []) if q][:5]
            if queries:
                return queries
        except Exception:
            pass
        # query 改写失败不致命：用 brief 关键词
        kws = brief.get("keywords") or []
        return [brief.get("topic", ""), *kws][:5]

    async def _search(self, ctx: RunContext, queries: list[str]) -> list[dict]:
        seen: dict[str, dict] = {}
        for q in queries:
            for e in await retrieve(ctx.session, q, top_k=6, days=30):
                key = e["text"][:80]
                if key not in seen or e["score"] > seen[key]["score"]:
                    seen[key] = e
        evs = sorted(seen.values(), key=lambda e: (-e["credibility"], -e["score"]))
        evs = evs[: settings.top_evidences]
        for i, e in enumerate(evs, 1):
            e["ev_id"] = f"ev_{i:03d}"
        return evs

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        brief = inputs.get("brief", {})
        evs = await retrieve(ctx.session, brief.get("topic", "") or "AI",
                             top_k=settings.top_evidences, days=60)
        return {"evidences": evs, "queries": [brief.get("topic", "")],
                "_decision": {"reason": f"兜底：主题关键词直接检索，召回 {len(evs)} 条证据"}}
