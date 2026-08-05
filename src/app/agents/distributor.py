"""10. Distributor 分发策略：平台×形态×受众×时段 分发计划"""
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext, clean_ev
from app.llm import extract_json
from app.prompts.manager import get_pm


class DistributorAgent(BaseAgent):
    name = "distributor"
    prompt_name = "distributor"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article, brief, formats = inputs["article"], inputs["brief"], inputs["formats"]
        m = ctx.market
        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, language=m.language,
            platforms=json.dumps(m.platforms, ensure_ascii=False),
            title=clean_ev(article["title"]),
            audience=brief.get("audience", ""),
            available_formats=json.dumps(list(formats.keys()), ensure_ascii=False),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        plan = data.get("plan") or []
        if not isinstance(plan, list) or not plan:
            raise AgentError(self.name, "分发计划为空")
        norm = [{
            "platform": str(p.get("platform", "")),
            "format": str(p.get("format", "article")),
            "audience": str(p.get("audience", brief.get("audience", ""))),
            "timing": str(p.get("timing", "")),
            "reason": str(p.get("reason", "")),
            "priority": _int(p.get("priority"), 2),
        } for p in plan]
        norm.sort(key=lambda p: p["priority"])
        return {
            "distribution": {"plan": norm}, "_llm_resp": resp,
            "_decision": {"reason": f"制定 {len(norm)} 条分发计划，主发 {norm[0]['platform']}"
                                   f"（{norm[0]['format']}）",
                          "details": {"primary": norm[0]}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        m = ctx.market
        formats = list((inputs.get("formats") or {}).keys()) or ["article"]
        plan = []
        for i, (plat, spec) in enumerate((m.platforms or {}).items()):
            fmt = next((f for f in (spec.get("formats") or []) if f in formats), formats[0])
            plan.append({
                "platform": plat, "format": fmt,
                "audience": spec.get("audience", ""), "timing": spec.get("peak", "全天"),
                "reason": "市场档案默认平台偏好（兜底）", "priority": i + 1,
            })
        return {"distribution": {"plan": plan},
                "_decision": {"reason": f"兜底：按市场档案默认平台生成 {len(plan)} 条计划"}}


def _int(v, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d
