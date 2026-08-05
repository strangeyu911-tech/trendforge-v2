"""KBCurator 知识库策展人（离线）：扫描覆盖度/过期 → 产出「待审补丁」

有意设计（与 FeedbackAnalyst 同构）：只提议、不改系统 ——
AI 发现知识库缺口与过期文档、给出候选补丁（add/retire + 理由），
人 approve 才入库。这是「人定义标准、AI 执行与进化」主线的延伸。
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.models import Document
from app.prompts.manager import get_pm
from app.rag.store import collect_kb_state, load_candidates


class KBCuratorAgent(BaseAgent):
    name = "kb_curator"
    prompt_name = "kb_curator"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        state = await collect_kb_state(ctx.session)
        candidates = load_candidates()
        if not candidates:
            raise AgentError(self.name, "无候选源")
        system, user = get_pm().render(
            self.prompt_name,
            ref_date=state["ref_date"],
            state=json.dumps(state, ensure_ascii=False, indent=1),
            candidates=json.dumps(candidates, ensure_ascii=False, indent=1),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=1500)
        data = extract_json(resp.text)
        items = [self._normalize(it) for it in (data.get("items") or []) if it]
        rationale = str(data.get("rationale") or "")
        if not items:
            raise AgentError(self.name, "LLM 未产出有效补丁项")
        return {
            "rationale": rationale, "items": items,
            "_decision": {"reason": f"扫描 {state['total']} 篇文档（过期 {len(state['stale'])} 篇），"
                                   f"审阅 {len(candidates)} 条候选，提议 {len(items)} 项补丁"},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        """规则兜底：候选全量提议 add；引用 replaces 的提议 retire 旧条目"""
        state = await collect_kb_state(ctx.session)
        candidates = load_candidates()
        titles = {d.title for d in (await ctx.session.execute(select(Document))).scalars().all()}
        items: list[dict] = []
        for c in candidates:
            replaces = c.get("replaces")
            if replaces and replaces in titles:
                items.append({"action": "retire", "title": replaces,
                              "reason": f"候选《{c['title']}》为更新版本，退役旧条目"})
            items.append(self._normalize({"action": "add", **c}))
        rationale = (f"规则兜底：提议入库 {len(candidates)} 条候选"
                     f"{('；退役 ' + str(sum(1 for i in items if i['action'] == 'retire')) + ' 条旧条目') if any(i['action'] == 'retire' for i in items) else ''}。"
                     f"当前过期文档 {len(state['stale'])} 篇。")
        return {"rationale": rationale, "items": items,
                "_decision": {"reason": "兜底：候选全量提议 + replaces 触发退役，无 LLM 参与"}}

    @staticmethod
    def _normalize(it: dict) -> dict:
        it = dict(it)
        it["action"] = str(it.get("action", "add")).lower()
        for k in ("title", "source", "url", "category", "country", "language",
                  "published_at", "body", "reason", "replaces"):
            it.setdefault(k, "")
        it["credibility"] = int(it.get("credibility", 2) or 2)
        it["ttl"] = int(it.get("ttl", 90) or 90)
        return it
