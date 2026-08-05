"""3. AudienceInsight 需求洞察：结合市场档案回答"用户为什么关心" """
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.prompts.manager import get_pm


class AudienceInsightAgent(BaseAgent):
    name = "audience_insight"
    prompt_name = "audience_insight"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        trends = inputs["trends"]
        m = ctx.market
        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, market_code=m.code, language=m.language,
            media_landscape=json.dumps(m.media_landscape, ensure_ascii=False),
            culture_notes=json.dumps(m.culture_notes, ensure_ascii=False),
            interests=json.dumps(m.interests, ensure_ascii=False),
            trends=json.dumps(trends, ensure_ascii=False, indent=1),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        insights = data.get("insights") or []
        if not isinstance(insights, list) or not insights:
            raise AgentError(self.name, "LLM 未返回有效洞察")
        norm = [{
            "topic": str(i.get("topic", ""))[:120],
            "demand_hypothesis": str(i.get("demand_hypothesis", "")),   # 用户为什么关心
            "audience": str(i.get("audience", "")),
            "emotion": str(i.get("emotion", "")),                       # 焦虑/好奇/兴奋/愤怒…
            "consumption_value": _int(i.get("consumption_value"), 5),   # 消费价值 1-10
            "risk": str(i.get("risk", "")),                             # 文化/合规风险
        } for i in insights]
        return {
            "insights": norm, "_llm_resp": resp,
            "_decision": {"reason": f"为 {m.name} 市场生成 {len(norm)} 条需求洞察，"
                                   f"首选主题：{norm[0]['topic']}",
                          "details": {"top_emotion": norm[0].get("emotion", "")}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        m = ctx.market
        insights = [{
            "topic": t.get("topic", ""),
            "demand_hypothesis": f"{m.name}用户对该领域有持续关注（基于市场兴趣画像）",
            "audience": "大众用户", "emotion": "好奇",
            "consumption_value": t.get("heat", 5), "risk": "",
        } for t in inputs.get("trends", [])]
        return {"insights": insights,
                "_decision": {"reason": "兜底：基于市场兴趣画像生成模板化需求假设"}}


def _int(v, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d
