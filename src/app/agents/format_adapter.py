"""9. FormatAdapter 形态派生：一稿多发（短视频脚本/摘要卡/快讯/评论引导）

⚠️ 三道 ev 清洗防线（V1 踩坑）：喂 LLM 前 clean_ev → LLM 输出 clean_ev → 返回前再清洗
"""
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext, clean_ev
from app.config import settings
from app.llm import extract_json
from app.prompts.manager import get_pm

FORMAT_LABELS = {
    "video_script": "短视频脚本（钩子/分镜/口播/字幕/CTA/Hashtag）",
    "card": "资讯摘要卡片（3-5 条要点 + 关键数据）",
    "brief_news": "快讯（100 字内一句话新闻 + 背景）",
    "comment": "评论区引导（观点提问 + 2 个讨论角度）",
}


class FormatAdapterAgent(BaseAgent):
    name = "format_adapter"
    prompt_name = "format_adapter"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article, brief = inputs["article"], inputs["brief"]
        m = ctx.market
        wanted = [f for f in (brief.get("format_plan") or settings.formats)
                  if f in FORMAT_LABELS and f != "article"]
        if not wanted:
            wanted = [f for f in settings.formats]
        body_text = " ".join(s["text"] for s in article["body"]["sections"])
        system, user = get_pm().render(
            self.prompt_name,
            language=m.language, market=m.name,
            title=clean_ev(article["title"]), summary=clean_ev(article.get("summary", "")),
            body=clean_ev(body_text)[:4000],
            formats=json.dumps({f: FORMAT_LABELS[f] for f in wanted}, ensure_ascii=False),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=5000)
        data = extract_json(resp.text)
        formats = clean_ev(data.get("formats") or {})
        if not isinstance(formats, dict) or not formats:
            raise AgentError(self.name, "形态派生为空")
        formats = {k: v for k, v in formats.items() if k in FORMAT_LABELS}
        return {
            "formats": formats, "_llm_resp": resp,
            "_decision": {"reason": f"派生 {len(formats)} 种形态：{'、'.join(formats.keys())}",
                          "details": {"format_list": list(formats.keys())}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        article = inputs.get("article", {})
        title = clean_ev(article.get("title", ""))
        summary = clean_ev(article.get("summary", ""))
        sections = article.get("body", {}).get("sections", [])
        first = clean_ev(sections[0]["text"][:150]) if sections else summary
        points = [clean_ev(s["text"][:80]) for s in sections[:4]]
        formats = {
            "card": {"title": title, "points": points or [summary]},
            "brief_news": {"headline": title, "body": (summary or first)[:120]},
            "comment": {"question": f"你对「{title}」怎么看？", "angles": points[:2]},
        }
        return {"formats": formats,
                "_decision": {"reason": "兜底：模板截取生成摘要卡/快讯/评论引导 3 形态"}}
