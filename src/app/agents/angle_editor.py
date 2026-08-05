"""4. AngleEditor 角度设计：选题+角度 → ContentBrief（供给链路的"主编判断"）"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.models import Content
from app.prompts.manager import get_pm


class AngleEditorAgent(BaseAgent):
    name = "angle_editor"
    prompt_name = "angle_editor"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        insights = inputs["insights"]
        m = ctx.market
        # 已发布标题 + 已被总编否决的选题（重试时传入）用于角度去重
        existing = (await ctx.session.execute(
            select(Content.title).where(Content.market == m.code).limit(20))).scalars().all()
        rejected = inputs.get("rejected_topics") or []
        existing.extend(f"[已否决] {t}" for t in rejected)
        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, market_code=m.code, language=m.language,
            tone=m.tone, default_style=m.default_style,
            platforms=json.dumps(m.platforms, ensure_ascii=False),
            culture_notes=json.dumps(m.culture_notes, ensure_ascii=False),
            insights=json.dumps(insights, ensure_ascii=False, indent=1),
            existing_titles=json.dumps(existing, ensure_ascii=False),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        brief = data.get("brief") or data
        if not brief.get("topic") or not brief.get("angle"):
            raise AgentError(self.name, "Brief 缺少 topic/angle")
        norm = {
            "topic": str(brief.get("topic", "")),
            "angle": str(brief.get("angle", "")),
            "hook": str(brief.get("hook", "")),
            "audience": str(brief.get("audience", "")),
            "style": str(brief.get("style", m.default_style)),
            "why_now": str(brief.get("why_now", "")),
            "avoid": brief.get("avoid") or [],
            "format_plan": brief.get("format_plan") or list(ctx_market_default_formats(m)),
            "keywords": brief.get("keywords") or [],
        }
        return {
            "brief": norm, "_llm_resp": resp,
            "_decision": {"reason": f"选题「{norm['topic']}」，角度={norm['angle']}，"
                                   f"钩子={norm['hook'][:30]}",
                          "details": {"style": norm["style"], "avoid": norm["avoid"]}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        insights = inputs.get("insights", [])
        top = insights[0] if insights else {"topic": "行业热点", "audience": "大众用户"}
        m = ctx.market
        brief = {
            "topic": top.get("topic", "行业热点"),
            "angle": "事实梳理与影响解读",
            "hook": "最新进展与关键数据",
            "audience": top.get("audience", "大众用户"),
            "style": m.default_style, "why_now": "近期热度上升",
            "avoid": [], "format_plan": list(ctx_market_default_formats(m)),
            "keywords": [top.get("topic", "")],
        }
        return {"brief": brief,
                "_decision": {"reason": "兜底：取消费价值最高的洞察 + 默认事实梳理角度"}}


def ctx_market_default_formats(m) -> list[str]:
    fmts = []
    for plat, spec in (m.platforms or {}).items():
        for f in (spec.get("formats") or []):
            if f not in fmts:
                fmts.append(f)
    return fmts or ["article", "card", "brief_news"]
