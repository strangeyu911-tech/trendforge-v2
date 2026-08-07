"""11. FeedbackAnalyst 反馈分析（离线）：消费数据 → 评估报告 → 迭代建议

有意设计：只产出建议给人（运营/PM），不自动改系统 —— 人定义标准，AI 提供依据。
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.agents.base import AgentError, BaseAgent, RunContext
from app.llm import extract_json
from app.models import Content, ContentEvent
from app.prompts.manager import get_pm
from app.services.prompt_versions import current_template_content

# FeedbackAnalyst 可提议改写的候选模板（给 LLM 当前全文作为改写上下文）
_SUGGESTABLE_TEMPLATES = ["writer", "editor", "distributor", "angle_editor"]


class FeedbackAnalystAgent(BaseAgent):
    name = "feedback_analyst"
    prompt_name = "feedback_analyst"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        stats = await collect_metrics(ctx.session, market=ctx.market.code)
        if stats["exposed"] == 0:
            raise AgentError(self.name, "无消费事件数据")
        # 每条内容的表现 + 质量分
        rows = (await ctx.session.execute(
            select(Content).where(Content.market == ctx.market.code)
            .order_by(Content.created_at.desc()).limit(10))).scalars().all()
        per_content = []
        for c in rows:
            ev = await _content_metrics(ctx.session, c.id)
            per_content.append({
                "title": c.title[:40], "quality_avg": (c.quality or {}).get("avg", 0),
                **ev,
            })
        # 载入候选模板当前全文，作为 LLM 改写新版的上下文
        current_tpls: dict[str, str] = {}
        for tpl in _SUGGESTABLE_TEMPLATES:
            content, ver = await current_template_content(ctx.session, tpl)
            current_tpls[tpl] = f"### {tpl}（当前版本 {ver}）\n{content}"
        system, user = get_pm().render(
            self.prompt_name,
            market=ctx.market.name,
            stats=json.dumps(stats, ensure_ascii=False),
            per_content=json.dumps(per_content, ensure_ascii=False, indent=1),
            current_templates="\n\n".join(current_tpls.values()),
        )
        resp = await ctx.llm.chat(system, user, json_mode=True, max_tokens=8000)
        data = extract_json(resp.text)
        findings = [str(f) for f in (data.get("findings") or [])][:6]
        raw_sugs = data.get("suggestions") or []
        # 结构化建议（可一键采纳生成新版本）
        structured = []
        for s in raw_sugs:
            if not isinstance(s, dict):
                continue
            new_prompt = str(s.get("new_prompt") or "").strip()
            if not new_prompt or "---system---" not in new_prompt:
                # 缺少完整 new_prompt 的建议退化为纯文本建议，不进入可采纳流
                continue
            structured.append({
                "target_template": str(s.get("target_template") or ""),
                "section": str(s.get("section") or ""),
                "proposed_change": str(s.get("proposed_change") or ""),
                "rationale": str(s.get("rationale") or ""),
                "expected_metric": str(s.get("expected_metric") or ""),
                "new_prompt": new_prompt,
            })
        # 人类可读建议（供 EvalReport / 评估中心展示）
        readable = [f"[{s['target_template']}/{s['section']}] {s['proposed_change']}"
                    for s in structured] or [str(s) for s in raw_sugs][:6]
        report = {
            "metrics": stats,
            "quality_avg": _avg([p["quality_avg"] for p in per_content]),
            "findings": findings,
            "suggestions": readable,
            "structured_suggestions": structured,
            "per_content": per_content,
        }
        return {
            "eval_report": report, "_llm_resp": resp,
            "_decision": {"reason": f"分析 {len(per_content)} 条内容消费数据，"
                                   f"产出 {len(findings)} 条发现、"
                                   f"{len(structured)} 条可采纳迭代建议"},
        }

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        stats = await collect_metrics(ctx.session, market=ctx.market.code)
        report = {
            "metrics": stats, "quality_avg": 0,
            "findings": [f"CTR={stats['ctr']}，完读率={stats['finish_rate']}，负反馈率={stats['neg_rate']}"],
            "suggestions": ["LLM 不可用，仅提供原始指标；建议人工审阅低 CTR 内容的角度与钩子"],
            "per_content": [],
        }
        return {"eval_report": report,
                "_decision": {"reason": "兜底：仅 SQL 统计，无 LLM 建议"}}


async def _content_metrics(session, content_id: str) -> dict:
    rows = (await session.execute(
        select(ContentEvent.event_type, func.count())
        .where(ContentEvent.content_id == content_id)
        .group_by(ContentEvent.event_type))).all()
    m = {t: n for t, n in rows}
    exposed = m.get("exposed", 0)
    return {
        "exposed": exposed,
        "ctr": round(m.get("clicked", 0) / exposed, 3) if exposed else 0,
        "finish_rate": round(m.get("finished", 0) / max(m.get("clicked", 0), 1), 3),
        "neg": m.get("negative", 0),
    }


async def collect_metrics(session, market: str | None = None) -> dict:
    q = select(ContentEvent.event_type, func.count()).group_by(ContentEvent.event_type)
    if market:
        q = q.where(ContentEvent.market == market)
    rows = (await session.execute(q)).all()
    m = {t: n for t, n in rows}
    exposed = m.get("exposed", 0)
    clicked = m.get("clicked", 0)
    # 分形态 CTR
    fmt_rows = (await session.execute(
        select(ContentEvent.format, ContentEvent.event_type, func.count())
        .group_by(ContentEvent.format, ContentEvent.event_type))).all()
    by_format: dict[str, dict] = {}
    for fmt, etype, n in fmt_rows:
        d = by_format.setdefault(fmt, {})
        d[etype] = d.get(etype, 0) + n
    fmt_ctr = {f: round(d.get("clicked", 0) / d["exposed"], 3)
               for f, d in by_format.items() if d.get("exposed")}
    return {
        "exposed": exposed, "clicked": clicked,
        "ctr": round(clicked / exposed, 3) if exposed else 0,
        "finish_rate": round(m.get("finished", 0) / max(clicked, 1), 3),
        "engagement": round((m.get("liked", 0) + m.get("shared", 0)) / max(clicked, 1), 3),
        "neg_rate": round(m.get("negative", 0) / max(clicked, 1), 3),
        "by_format_ctr": fmt_ctr,
    }


def _avg(xs: list[float]) -> float:
    xs = [x for x in xs if x]
    return round(sum(xs) / len(xs), 2) if xs else 0.0
