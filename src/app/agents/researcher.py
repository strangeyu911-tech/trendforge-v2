"""5. Researcher 素材寻找：query 改写 + BM25 检索，产出可溯源证据集"""
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.store import retrieve, retrieve_scores


class ResearcherAgent(BaseAgent):
    name = "researcher"
    prompt_name = "researcher"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        brief = inputs["brief"]
        queries = await self._rewrite_queries(ctx, brief)
        evidences = await self._search(ctx, queries, brief)
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

    async def _search(self, ctx: RunContext, queries: list[str], brief: dict) -> list[dict]:
        seen: dict[str, dict] = {}
        for q in queries:
            for e in await retrieve(ctx.session, q, top_k=6, days=30):
                key = e["text"][:80]
                if key not in seen or e["score"] > seen[key]["score"]:
                    seen[key] = e
        candidates = list(seen.values())
        # 主题相关性过滤：用 topic+angle 重新打分
        # （防止多主题证据混杂导致 Writer 跑题 —— 两次 Editor reject 的前车之鉴）
        topic_q = f"{brief.get('topic', '')} {brief.get('angle', '')}"
        scored = await retrieve_scores(ctx, candidates, topic_q)
        if scored:
            top = max(s for _, s in scored)
            relevant = [e for e, s in scored if top <= 0 or s >= top * 0.3]
            # 类目一致性：只保留 top-3 命中主导类目的证据（歌手/足球/机器人不能混）
            if len(scored) >= 3:
                from collections import Counter
                dom_cats = Counter(e["category"] for e, _ in scored[:3])
                dom = dom_cats.most_common(1)[0][0]
                same_cat = [e for e in relevant if e["category"] == dom]
                if len(same_cat) >= 3:
                    relevant = same_cat
            if len(relevant) < 4:  # 过滤太狠则放宽到排序前 8
                relevant = [e for e, _ in scored][:8]
            # 文档聚类：主线文档（top1 所在 doc）必须保留，其余 doc 最多再留 2 个
            # （证据来自 1 个主干新闻 + ≤2 个背景来源，防止"每条证据写一节"的拼盘稿）
            if scored:
                main_doc = scored[0][0]["doc_title"]
                by_doc: dict[str, list[dict]] = {}
                for e in relevant:
                    by_doc.setdefault(e["doc_title"], []).append(e)
                keep_docs = [main_doc] + [d for d in by_doc if d != main_doc][:2]
                relevant = [e for d in keep_docs for e in by_doc.get(d, [])]
        else:
            relevant = candidates
        evs = sorted(relevant, key=lambda e: (-e["credibility"], -e["score"]))
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
