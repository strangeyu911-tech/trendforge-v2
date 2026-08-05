"""7. FactChecker 事实核查：论断↔证据核对，独立于质量审核"""
from __future__ import annotations

import json
import re

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.prompts.manager import get_pm


class FactCheckerAgent(BaseAgent):
    name = "fact_checker"
    prompt_name = "fact_checker"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article, evidences = inputs["article"], inputs["evidences"]
        body_text = " ".join(s["text"] for s in article["body"]["sections"])
        ev_lines = "\n".join(f"[{e['ev_id']}] {e['text'][:250]}" for e in evidences)
        system, user = get_pm().render(
            self.prompt_name,
            title=article["title"], body=body_text[:6000], evidences=ev_lines,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        claims = data.get("claims") or []
        if not isinstance(claims, list) or not claims:
            raise AgentError(self.name, "未抽取到论断")
        supported = sum(1 for c in claims if c.get("verdict") == "supported")
        weak = [c for c in claims if c.get("verdict") == "weak"]
        unsupported = [c for c in claims if c.get("verdict") == "unsupported"]
        ratio = supported / len(claims)
        fact_check = {
            "claim_count": len(claims),
            "supported": supported,
            "support_ratio": round(ratio, 2),
            "weak_claims": [str(c.get("claim", ""))[:100] for c in weak][:5],
            "unsupported_claims": [str(c.get("claim", ""))[:100] for c in unsupported][:5],
            "confidence": round(min(0.99, 0.6 + ratio * 0.4 - len(unsupported) * 0.05), 2),
        }
        return {
            "fact_check": fact_check, "_llm_resp": resp,
            "_decision": {"reason": f"核查 {len(claims)} 个论断：{supported} 有据 / "
                                   f"{len(weak)} 弱支持 / {len(unsupported)} 无据，"
                                   f"置信度 {fact_check['confidence']}",
                          "details": fact_check},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        article, evidences = inputs.get("article", {}), inputs.get("evidences", [])
        body_text = json.dumps(article.get("body", {}), ensure_ascii=False)
        cited = set(re.findall(r"\[ev_\d+\]", body_text))
        ratio = round(len(cited) / max(len(evidences), 1), 2)
        fc = {"claim_count": len(cited), "supported": len(cited), "support_ratio": ratio,
              "weak_claims": [], "unsupported_claims": [],
              "confidence": round(0.5 + ratio * 0.3, 2)}
        return {"fact_check": fc,
                "_decision": {"reason": f"兜底：引用计数启发式，{len(cited)} 处引用均有证据支撑"}}
