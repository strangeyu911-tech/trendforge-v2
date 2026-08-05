"""8. Editor 总编审核：质量 Rubric + 合规 + 文化敏感性 → pass/revise/reject"""
from __future__ import annotations

import json

from app.agents.base import AgentError, BaseAgent, RunContext, clean_ev
from app.config import COMPLIANCE_BLOCKLIST
from app.llm import extract_json
from app.prompts.manager import get_pm

RUBRIC_DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]


class EditorAgent(BaseAgent):
    name = "editor"
    prompt_name = "editor"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article = inputs["article"]
        fact_check = inputs["fact_check"]
        brief = inputs["brief"]
        m = ctx.market
        body_text = " ".join(s["text"] for s in article["body"]["sections"])

        # 规则合规扫描（先于 LLM，命中即 revise/reject 的硬依据）
        hits = [w for w in COMPLIANCE_BLOCKLIST if w.lower() in body_text.lower()]

        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, language=m.language, tone=m.tone,
            culture_notes=json.dumps(m.culture_notes, ensure_ascii=False),
            topic=brief.get("topic", ""), angle=brief.get("angle", ""),
            title=article["title"], body=clean_ev(body_text)[:5000],
            fact_check=json.dumps(fact_check, ensure_ascii=False),
            compliance_hits=json.dumps(hits, ensure_ascii=False),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        scores = {d: _clamp(data.get("scores", {}).get(d, 3)) for d in RUBRIC_DIMS}
        avg = round(sum(scores.values()) / len(scores), 2)
        verdict = str(data.get("verdict", "pass"))
        if verdict not in ("pass", "revise", "reject"):
            verdict = "pass"
        # 硬规则：无据论断多 → 至少 revise；合规命中 → revise
        if fact_check.get("unsupported_claims") and verdict == "pass":
            verdict = "revise"
        if hits and verdict == "pass":
            verdict = "revise"
        review = {
            "verdict": verdict,
            "scores": scores, "avg": avg,
            "comments": str(data.get("comments", "")),
            "revision_advice": str(data.get("revision_advice", "")),
            "compliance_hits": hits,
        }
        return {
            "review": review, "_llm_resp": resp,
            "_decision": {"reason": f"裁决 {verdict}：综合 {avg}/5"
                                   + (f"，合规命中 {len(hits)} 项" if hits else "")
                                   + (f"，修改意见：{review['revision_advice'][:40]}" if verdict == "revise" else ""),
                          "details": review},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        article = inputs.get("article", {})
        fact_check = inputs.get("fact_check", {})
        body_text = json.dumps(article.get("body", {}), ensure_ascii=False)
        hits = [w for w in COMPLIANCE_BLOCKLIST if w.lower() in body_text.lower()]
        conf = fact_check.get("confidence", 0.5)
        review = {
            "verdict": "revise" if hits else "pass",
            "scores": {d: 3 for d in RUBRIC_DIMS}, "avg": 3.0,
            "comments": "规则兜底审核（LLM 不可用）",
            "revision_advice": "清除敏感词" if hits else "",
            "compliance_hits": hits,
        }
        if hits:
            review["avg"] = 2.0
        return {"review": review,
                "_decision": {"reason": f"兜底审核：合规扫描 {len(hits)} 命中，"
                                       f"事实置信 {conf}，裁决 {review['verdict']}"}}


def _clamp(v) -> float:
    try:
        return max(1.0, min(5.0, float(v)))
    except (TypeError, ValueError):
        return 3.0
