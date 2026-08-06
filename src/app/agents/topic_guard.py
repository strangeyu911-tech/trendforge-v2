"""6.5 TopicGuard 主题一致性闸门：Writer 出稿后立刻拦截漂移，定点重写而非整篇废掉

位置很关键——它插在 Writer 与 FactChecker 之间，**在下游烧钱之前**拦截：
  旧链路：Writer → FactChecker → Editor(reject) → 整条废稿（¥0.28 / 11 分钟）
  新链路：Writer → TopicGuard(检测零成本) → 只重写漂移小节（约 ¥0.02）→ FactChecker

三级处置，逐级降级但绝不整篇废：
  1. TCS 通过           → 放行，零成本
  2. TCS 不通过         → 只把漂移小节喂给 LLM 重写，且只提供主干证据
  3. 重写后仍不通过     → 摘除漂移小节（保底留 2 节），标记 degraded
"""
from __future__ import annotations

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.prompts.manager import get_pm
from app.rag.tcs import drop_drift_sections, score_article


def _drop(article: dict, drift: list[int]) -> tuple[dict, int]:
    """摘除漂移小节，返回 (新稿, 实际摘除数)。

    实际摘除数可能小于 drift 数——2 节结构下限会把节加回来，
    此时必须如实上报 0，不能让决策日志说谎。
    """
    before = len((article.get("body") or {}).get("sections") or [])
    out = drop_drift_sections(article, drift)
    return out, before - len((out.get("body") or {}).get("sections") or [])


class TopicGuardAgent(BaseAgent):
    name = "topic_guard"
    prompt_name = "topic_guard"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article = inputs.get("article") or {}
        brief = inputs.get("brief") or {}
        evidences = inputs.get("evidences") or []
        report = score_article(article, brief, evidences)
        if report["passed"]:
            return {"topic_guard": report,
                    "_decision": {"reason": f"主题一致性通过 TCS={report['tcs']}"
                                            f"（{report['reason']}）",
                                  "details": {"tcs": report["tcs"],
                                              "main_ratio": report["main_ratio"]}}}

        sections = (article.get("body") or {}).get("sections") or []
        drift = [i for i in report["drift_sections"] if 0 <= i < len(sections)]
        if not drift:
            # 结构性不达标（如主干占比不足）但无法定位到具体小节：交给下游 Editor 判
            return {"topic_guard": report,
                    "_warnings": [f"主题一致性不达标但无法定位漂移小节：{report['reason']}"],
                    "_decision": {"reason": f"TCS={report['tcs']} 不达标（{report['reason']}），"
                                            f"无定点修复目标，转交 Editor"}}

        repaired, resp = await self._rewrite(ctx, article, brief, evidences, drift, report)
        after = score_article(repaired, brief, evidences)
        # 重写未必更好（弱模型可能越改越偏），劣化则回退原稿走删节路径
        if after["tcs"] < report["tcs"] and len(after["drift_sections"]) >= len(drift):
            repaired, after = article, report

        if after["passed"]:
            return {"article": repaired, "topic_guard": after, "_llm_resp": resp,
                    "_decision": {"reason": f"检出第 {[i + 1 for i in drift]} 节脱离主线，"
                                            f"定点重写后 TCS {report['tcs']}→{after['tcs']} 通过",
                                  "details": {"tcs_before": report["tcs"],
                                              "tcs_after": after["tcs"],
                                              "rewritten": len(drift)}}}

        # 三级：删节兜底
        final, removed = _drop(repaired, after["drift_sections"])
        final_report = score_article(final, brief, evidences)
        final_report["dropped"] = removed
        tail = (f"摘除 {removed} 节后 TCS={final_report['tcs']}" if removed
                else "受 2 节结构下限保护未能摘除，转交 Editor 判定")
        return {"article": final, "topic_guard": final_report, "_llm_resp": resp,
                "_warnings": [f"重写后仍漂移（TCS {after['tcs']}），{tail}"],
                "_decision": {"reason": f"定点重写未收敛（TCS {report['tcs']}→{after['tcs']}），"
                                        + tail,
                              "details": {"dropped": removed}}}

    async def _rewrite(self, ctx: RunContext, article: dict, brief: dict,
                       evidences: list[dict], drift: list[int], report: dict):
        """只重写漂移小节，且只提供主干证据——从素材上断掉跑题的可能"""
        sections = (article.get("body") or {}).get("sections") or []
        main_evs = [e for e in evidences if e.get("is_main")] or evidences[:2]
        m = ctx.market
        system, user = get_pm().render(
            self.prompt_name,
            language=m.language, tone=m.tone,
            topic=brief.get("topic", ""), angle=brief.get("angle", ""),
            problem=report["reason"],
            evidences="\n".join(
                f"[{e['ev_id']}] ({e.get('source', '')}) {e.get('text', '')[:500]}"
                for e in main_evs),
            drift_sections="\n\n".join(
                f"[idx={i}] {sections[i].get('heading', '')}\n"
                f"{sections[i].get('text', '')[:600]}" for i in drift),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=6000)
        data = extract_json(resp.text)
        new_secs = data.get("sections") or []
        if not new_secs:
            raise AgentError(self.name, "重写返回空小节")

        merged = [dict(s) for s in sections]
        for pos, item in enumerate(new_secs):
            idx = item.get("idx")
            if not isinstance(idx, int) or not (0 <= idx < len(merged)):
                idx = drift[pos] if pos < len(drift) else None
            if idx is None or not str(item.get("text", "")).strip():
                continue
            merged[idx] = {"heading": str(item.get("heading") or merged[idx].get("heading", "")),
                           "text": str(item["text"])}
        out = dict(article)
        out["body"] = {**article.get("body", {}), "sections": merged}
        return out, resp

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        """LLM 重写失败：纯规则删节，不依赖模型"""
        article = inputs.get("article") or {}
        brief = inputs.get("brief") or {}
        evidences = inputs.get("evidences") or []
        report = score_article(article, brief, evidences)
        if report["passed"] or not report["drift_sections"]:
            return {"topic_guard": report,
                    "_decision": {"reason": f"兜底：重写不可用，TCS={report['tcs']} 直接放行"}}
        final, removed = _drop(article, report["drift_sections"])
        final_report = score_article(final, brief, evidences)
        final_report["dropped"] = removed
        return {"article": final, "topic_guard": final_report,
                "_decision": {"reason": f"兜底：重写不可用，摘除 {removed} 个漂移小节，"
                                        f"TCS {report['tcs']}→{final_report['tcs']}"
                                        + ("" if removed else "（受 2 节结构下限保护）")}}
