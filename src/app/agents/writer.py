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
        # 证据集显式区分主干/背景：让"哪条是主线"从模型的自由判断变成给定条件
        ev_lines = "\n".join(
            f"[{e['ev_id']}] 【{'主干' if e.get('is_main') else '背景'}】"
            f"({e['source']}|可信度{e['credibility']}|{e['published_at']}) {e['text'][:600]}"
            for e in evidences
        )
        main_ids = [e["ev_id"] for e in evidences if e.get("is_main")]
        system, user = get_pm().render(
            self.prompt_name,
            language=m.language, market=m.name, tone=m.tone,
            topic=brief.get("topic", ""), angle=brief.get("angle", ""),
            hook=brief.get("hook", ""), audience=brief.get("audience", ""),
            style_label=style_info["label"], length=style_info["length"],
            avoid=json.dumps(brief.get("avoid") or [], ensure_ascii=False),
            evidences=ev_lines, editor_feedback=feedback or "（无，初稿）",
            main_ev_ids=", ".join(f"[{i}]" for i in main_ids) or "（未标注，以第一条为准）",
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
        cited = sorted(set(re.findall(r"ev_\d+", full_text)))
        # L2 引用约束反转（2026-08-06）：
        # 旧规则 `len(cited) < max(2, len(evidences)//3)` 是**引用数下限**，
        # top_evidences=10 时等于强制模型至少引用 3 个不同来源——而 prompt 铁律写的是
        # "主干为主、其余合计不超过 3 处"。代码和 prompt 方向相反，模型只会听代码的
        # （只有代码会真的报错），这正是拼盘稿的制度性成因。
        # 新规则改为**主干引用下限**：不再奖励引用广度，只要求主线站得住。
        main_ids = {e["ev_id"] for e in evidences if e.get("is_main")}
        main_cited = [c for c in cited if c in main_ids] if main_ids else cited
        if not cited:
            raise AgentError(self.name, "全文无证据引用，拒绝无溯源成稿")
        if len(main_cited) < min(2, len(main_ids) or 2):
            raise AgentError(self.name,
                             f"主干证据引用不足（{len(main_cited)} 处），主线不成立")
        return {
            "article": {
                "title": clean_ev(title),
                "summary": clean_ev(str(data.get("summary", ""))),
                "body": body,
            }, "_llm_resp": resp,
            "_decision": {"reason": f"按 {style_info['label']} 成稿 {len(full_text)} 字，"
                                   f"引用证据 {len(cited)}/{len(evidences)}"
                                   f"（主干 {len(main_cited)} 处）"
                                   + ("（按总编意见重写）" if feedback else ""),
                          "details": {"citations": len(cited),
                                      "main_citations": len(main_cited),
                                      "rewrite": bool(feedback)}},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        """L3：兜底稿只用主干文档写 2 节

        旧实现是 `for e in evidences[:4]: 每条证据写一节` —— 兜底逻辑本身就在
        制造它要防的问题（US 那条"多篇新闻片段拼凑"就是这个形状）。
        宁可短、宁可信息量小，也绝不生成拼盘。
        """
        brief, evidences = inputs.get("brief", {}), inputs.get("evidences", [])
        topic = brief.get("topic", "行业动态")
        main = [e for e in evidences if e.get("is_main")] or evidences[:2]
        lead = main[0] if main else None
        sections = [{
            "heading": "事件概览",
            "text": (f"近期，{topic}受到关注。{brief.get('why_now', '')} "
                     + (f"{lead['text'][:260]} [{lead['ev_id']}]" if lead else "")).strip(),
        }]
        detail = " ".join(f"{e['text'][:220]} [{e['ev_id']}]" for e in main[1:3])
        sections.append({
            "heading": "为何值得关注",
            "text": (f"{brief.get('angle', '')} {brief.get('hook', '')} {detail}").strip()
                    or (f"{lead['text'][260:520]} [{lead['ev_id']}]" if lead else topic),
        })
        article = {
            "title": f"{topic}：关键进展",
            "summary": f"围绕{topic}的核心事实梳理（规则兜底稿，仅采用主干来源）。",
            "body": {"sections": sections},
        }
        return {"article": article,
                "_decision": {"reason": f"兜底：仅用主干来源生成 2 节精简稿"
                                       f"（{len(main)} 条主干证据），不做多源拼装"}}
