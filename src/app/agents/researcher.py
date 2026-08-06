"""5. Researcher 素材寻找：query 改写 + BM25 检索，产出可溯源证据集

反主题漂移（L0，2026-08-06）：
证据集是 Writer 的唯一素材来源，**污染在这里发生，漂移在下游爆炸**。
本文件的过滤链负责保证「交到 Writer 手上的证据集本身就是单主线的」。
"""
from __future__ import annotations

import json
from collections import Counter

from app.agents.base import AgentError, BaseAgent, RunContext
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.store import retrieve, retrieve_scores

# 主干文档最多贡献的证据块数；背景文档每篇只取 1 块、最多 2 篇。
# 目的：让证据集在物理结构上就是「主干主导」，Writer 的拼盘倾向失去素材基础。
MAIN_DOC_CHUNKS = 3
BG_DOC_MAX = 2
MIN_EVIDENCES = 3  # 少于此数才考虑放宽（且放宽只在主导类目内进行）


class ResearcherAgent(BaseAgent):
    name = "researcher"
    prompt_name = "researcher"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        brief = inputs["brief"]
        queries = await self._rewrite_queries(ctx, brief)
        evidences, guard = await self._search(ctx, queries, brief)
        if not evidences:
            raise AgentError(self.name, "检索无证据")
        sources = {e["source"] for e in evidences}
        return {
            "evidences": evidences, "queries": queries, "evidence_guard": guard,
            "_decision": {"reason": f"改写 {len(queries)} 个 query，{guard['candidates']} 条候选经"
                                   f"相关性/类目/主干三级过滤留 {len(evidences)} 条，"
                                   f"主干 {guard['main_chunks']} 块 + 背景 {guard['background_docs']} 篇，"
                                   f"覆盖 {len(sources)} 个来源",
                          "details": {"queries": queries, "guard": guard}},
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

    async def _search(self, ctx: RunContext, queries: list[str],
                      brief: dict) -> tuple[list[dict], dict]:
        seen: dict[str, dict] = {}
        for q in queries:
            for e in await retrieve(ctx.session, q, top_k=6, days=30):
                key = e["text"][:80]
                if key not in seen or e["score"] > seen[key]["score"]:
                    seen[key] = e
        candidates = list(seen.values())
        relevant, main_doc, guard = await self._filter(ctx, candidates, brief)
        evs = sorted(relevant, key=lambda e: (e["doc_title"] != main_doc,
                                              -e["credibility"], -e["score"]))
        evs = evs[: settings.top_evidences]
        for i, e in enumerate(evs, 1):
            e["ev_id"] = f"ev_{i:03d}"
            e["is_main"] = e["doc_title"] == main_doc  # 供 Writer / TopicGuard 判定主线
        guard["main_doc"] = main_doc
        guard["main_chunks"] = sum(1 for e in evs if e["is_main"])
        guard["kept"] = len(evs)
        guard.setdefault("background_docs", 0)
        return evs, guard

    async def _filter(self, ctx: RunContext, candidates: list[dict],
                      brief: dict) -> tuple[list[dict], str, dict]:
        """主题相关性过滤链：相关性双阈值 → 类目一致性 → 定向放宽 → 主干聚类"""
        guard: dict = {"candidates": len(candidates)}
        topic_q = f"{brief.get('topic', '')} {brief.get('angle', '')}".strip()
        scored = await retrieve_scores(ctx, candidates, topic_q)
        if not scored:
            return candidates, (candidates[0]["doc_title"] if candidates else ""), guard

        # ① 相关性双阈值：相对阈值防"矮子里拔将军"，绝对阈值防"全员不相关也硬凑"。
        #    跨语言场景（JP/KR/BR 选题 vs 英文 KB）BM25 词面失效 → 峰值趋近 0，
        #    此时相对阈值毫无意义，直接停用词面闸，交给类目 + 主干聚类兜底。
        top = max(s for _, s in scored)
        lexical_usable = top >= settings.topic_min_score
        guard["lexical_usable"] = lexical_usable
        if lexical_usable:
            floor = max(top * 0.3, settings.topic_min_score * 0.5)
            relevant = [e for e, s in scored if s >= floor]
        else:
            relevant = [e for e, _ in scored]
        guard["after_relevance"] = len(relevant)

        # ② 类目一致性：只保留主导类目（歌手 / 足球 / 电竞不能混进机器人稿）
        dom = ""
        if len(scored) >= 3:
            dom = Counter(e["category"] for e, _ in scored[:3]).most_common(1)[0][0]
            same_cat = [e for e in relevant if e["category"] == dom]
            if len(same_cat) >= 2:  # 旧实现要求 ≥3，恰好 3 条时必被 ③ 放宽抹掉
                relevant = same_cat
        guard["dominant_category"] = dom
        guard["after_category"] = len(relevant)

        # ③ 放宽（L0 关键修复）
        #    旧实现：`if len(relevant) < 4: relevant = [e for e, _ in scored][:8]`
        #    —— 无条件丢弃 ①② 的全部成果，把无关类目原样放回；且 same_cat 恰为 3
        #    时（3 < 4）必然触发，类目过滤 100% 白做。方向也是反的：证据越少越放宽，
        #    而"相关证据少"恰恰是最需要过滤的窄主题场景，防护在最该生效时自动关闭。
        #    现改为：只在主导类目内补齐，跨类目一律不补，宁可证据少也不放污染源进来。
        if len(relevant) < MIN_EVIDENCES:
            pool = [e for e, _ in scored if not dom or e["category"] == dom]
            relevant = pool[:MIN_EVIDENCES] or relevant
            guard["widened"] = True

        # ④ 主干聚类：主干文档最多 3 块，背景文档每篇 1 块、最多 2 篇
        keep_ids = {id(e) for e in relevant}
        ordered = [e for e, _ in scored if id(e) in keep_ids]
        main_doc = ordered[0]["doc_title"] if ordered else relevant[0]["doc_title"]
        by_doc: dict[str, list[dict]] = {}
        for e in ordered:
            by_doc.setdefault(e["doc_title"], []).append(e)
        picked = by_doc.get(main_doc, [])[:MAIN_DOC_CHUNKS]
        bg = 0
        for d, lst in by_doc.items():
            if d == main_doc or bg >= BG_DOC_MAX:
                continue
            picked.append(lst[0])
            bg += 1
        guard["background_docs"] = bg
        return (picked or relevant), main_doc, guard

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        brief = inputs.get("brief", {})
        hits = await retrieve(ctx.session, brief.get("topic", "") or "AI",
                              top_k=settings.top_evidences * 2, days=60)
        # 兜底同样守主干原则：命中第一篇为主线，背景文档每篇 1 块、最多 2 篇
        main_doc = hits[0]["doc_title"] if hits else ""
        evs = [e for e in hits if e["doc_title"] == main_doc][:MAIN_DOC_CHUNKS]
        seen_bg: set[str] = set()
        for e in hits:
            if e["doc_title"] == main_doc or len(seen_bg) >= BG_DOC_MAX:
                continue
            if e["doc_title"] not in seen_bg:
                seen_bg.add(e["doc_title"])
                evs.append(e)
        for i, e in enumerate(evs, 1):
            e["ev_id"] = f"ev_{i:03d}"
            e["is_main"] = e["doc_title"] == main_doc
        return {"evidences": evs, "queries": [brief.get("topic", "")],
                "evidence_guard": {"fallback": True, "main_doc": main_doc,
                                   "main_chunks": sum(1 for e in evs if e["is_main"]),
                                   "background_docs": len(seen_bg), "kept": len(evs)},
                "_decision": {"reason": f"兜底：主题关键词直接检索，主干 1 篇 + 背景 "
                                       f"{len(seen_bg)} 篇，共 {len(evs)} 条证据"}}
