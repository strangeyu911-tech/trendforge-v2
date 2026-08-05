"""6. Writer 生成：按 Brief + 证据成稿，强制 [ev_xxx] 引用；支持 Editor 修改意见重写"""
from __future__ import annotations

import json
import re

from app.agents.base import AgentError, BaseAgent, RunContext, clean_ev
from app.config import CONTENT_STYLES
from app.llm import extract_json
from app.prompts.manager import get_pm


class WriterAgent(BaseAgent):
    name = "writer"
    prompt_name = "writer"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        brief, evidences = inputs["brief"], inputs["evidences"]
        feedback = inputs.get("editor_feedback", "")  # revise 回退时非空
        m = ctx.market
        style = brief.get("style", m.default_style)
        style_info = CONTENT_STYLES.get(style, CONTENT_STYLES["deep_dive"])
        ev_lines = "\n".join(
            f"[{e['ev_id']}] ({e['source']}|可信度{e['credibility']}|{e['published_at']}) {e['text'][:600]}"
            for e in evidences
        )
        system, user = get_pm().render(
            self.prompt_name,
            language=m.language, market=m.name, tone=m.tone,
            topic=brief.get("topic", ""), angle=brief.get("angle", ""),
            hook=brief.get("hook", ""), audience=brief.get("audience", ""),
            style_label=style_info["label"], length=style_info["length"],
            avoid=json.dumps(brief.get("avoid") or [], ensure_ascii=False),
            evidences=ev_lines, editor_feedback=feedback or "（无，初稿）",
        )
        resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=12000)
        data = extract_json(resp.text)
        title = str(data.get("title", "")).strip()
        sections = data.get("sections") or []
        if not title or not isinstance(sections, list) or len(sections) < 2:
            raise AgentError(self.name, "成稿结构不完整")
        body = {"sections": [{
            "heading": str(s.get("heading", "")),
            "text": str(s.get("text", "")),
        } for s in sections if s.get("text")]}
        full_text = title + " " + " ".join(s["text"] for s in body["sections"])
        cited = sorted(set(re.findall(r"\[ev_\d+\]", full_text)))
        if len(cited) < max(2, len(evidences) // 3):
            raise AgentError(self.name, f"引用不足（{len(cited)} 处），拒绝无溯源成稿")
        return {
            "article": {
                "title": clean_ev(title),
                "summary": clean_ev(str(data.get("summary", ""))),
                "body": body,
            }, "_llm_resp": resp,
            "_decision": {"reason": f"按 {style_info['label']} 成稿 {len(full_text)} 字，"
                                   f"引用证据 {len(cited)}/{len(evidences)}"
                                   + ("（按总编意见重写）" if feedback else ""),
                          "details": {"citations": len(cited), "rewrite": bool(feedback)}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        brief, evidences = inputs.get("brief", {}), inputs.get("evidences", [])
        topic = brief.get("topic", "行业动态")
        sections = [{"heading": "事件概览", "text": f"近期，{topic}受到关注。" + brief.get("why_now", "")}]
        for e in evidences[:4]:
            sections.append({"heading": e["doc_title"][:30],
                             "text": f"{e['text'][:200]} [{e['ev_id']}]"})
        article = {
            "title": f"{topic}：最新进展盘点",
            "summary": f"关于{topic}的近期动态汇总（规则兜底稿）。",
            "body": {"sections": sections},
        }
        return {"article": article,
                "_decision": {"reason": "兜底：基于证据拼装的模板稿（标注 is_fallback）"}}
