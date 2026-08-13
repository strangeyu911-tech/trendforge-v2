"""人工校准路由：真人打分 ↔ LLM 评委对齐（Evaluate 段人机闭环，DB 驱动）

- GET  /api/calibration/samples  返回待校准内容（标题 + 全文 + 市场 + 已有真人打分人数/均值），**隐藏评委分**
- POST /api/calibration/scores   接收真人打分（含 0.5 半分 + 理由），落库 human_calibrations →
                                  聚合写回 contents.human_score_avg → 库内算 Spearman 对齐
- GET  /api/calibration/report   从 DB 实时重算并返回最新校准报告（markdown + 对齐图 SVG）

数据模型：
  human_calibrations 每行 = 某 rater 对某内容的一次五维打分（append-only）。
  同一 rater 续打 = 再插一行，聚合按 (content_id, rater) 取最新 → 「每人留最新」；
  不同 rater 累积，对齐时按 content 对 distinct rater 最新分逐维取均值 = 真人共识分。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, text

from app.models import Content, HumanCalibration, SessionLocal

router = APIRouter()

# 仓库根：src/app/api/routers -> parents[4] = 仓库
REPO = Path(__file__).resolve().parents[4]
CAL_DIR = REPO / "tools" / "calibration"
DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
EV_RE = re.compile(r"\s*\[ev_\d+\]")


def _body_to_text(body) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return body
    if isinstance(body, dict):
        return "\n".join(s.get("text", "") for s in body.get("sections", []) if isinstance(s, dict))
    if isinstance(body, list):
        return "\n".join((s.get("text", "") if isinstance(s, dict) else str(s)) for s in body)
    return str(body)


def _clean_ev(text: str) -> str:
    return EV_RE.sub("", text or "")


# compute_alignment.py 以文件方式加载（避免侵入式 import），缓存复用
_ca = None


def get_ca():
    global _ca
    if _ca is None:
        spec = importlib.util.spec_from_file_location(
            "tf_compute_alignment", str(CAL_DIR / "compute_alignment.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ca = mod
    return _ca


async def _aggregate_content(session, content_id) -> dict | None:
    """按 (content_id, rater) 取最新一行 → 逐维平均。返回 {dim:均值, n_raters, updated_at} 或 None。"""
    rows = (await session.execute(
        select(HumanCalibration)
        .where(HumanCalibration.content_id == content_id)
        .order_by(HumanCalibration.created_at.desc())
    )).scalars().all()
    if not rows:
        return None
    latest = {}
    for r in rows:
        latest.setdefault(r.rater, r)   # 倒序首见 = 每人最新
    n_raters = len(latest)
    agg = {d: 0.0 for d in DIMS}
    for r in latest.values():
        sc = r.scores or {}
        for d in DIMS:
            agg[d] += float(sc.get(d, 0) or 0)
    for d in DIMS:
        agg[d] = round(agg[d] / n_raters, 2)
    agg["n_raters"] = n_raters
    agg["updated_at"] = max((r.created_at.isoformat() for r in latest.values()), default=None)
    return agg


async def _latest_reasons(session, content_id) -> dict:
    row = (await session.execute(
        select(HumanCalibration)
        .where(HumanCalibration.content_id == content_id)
        .order_by(HumanCalibration.created_at.desc()).limit(1)
    )).scalars().first()
    return dict(row.reasons or {}) if row else {}


async def _recompute_alignment(session):
    """遍历全部内容：聚合真人分写回 contents.human_score_avg，并算 Spearman 对齐。

    返回 (summary, per_content)：
      summary: {overall_rho, overall_adj, overall_exact, n, common}
      per_content: {cid: {n_raters, avg:{dim:val}}}
    """
    contents = (await session.execute(select(Content))).scalars().all()
    judge_map, meta, human_map, reasons_map, per_content = {}, {}, {}, {}, {}
    for c in contents:
        q = c.quality or {}
        sc = (q.get("scores") or {}) if isinstance(q, dict) else {}
        if not all(d in sc for d in DIMS):
            continue
        judge_map[c.id] = {d: float(sc[d]) for d in DIMS}
        meta[c.id] = {"market": c.market, "title": c.title}
        agg = await _aggregate_content(session, c.id)
        if agg:
            human_map[c.id] = {d: agg[d] for d in DIMS}
            reasons_map[c.id] = await _latest_reasons(session, c.id)
            per_content[c.id] = {"n_raters": agg["n_raters"], "avg": {d: agg[d] for d in DIMS}}
            c.human_score_avg = agg
        else:
            c.human_score_avg = {}
    await session.commit()

    if not human_map:
        return {"overall_rho": None, "overall_adj": None, "overall_exact": None,
                "n": 0, "common": []}, per_content

    ca = get_ca()
    try:
        summary = ca.compute_alignment_core(human_map, judge_map, meta,
                                            rater_label="HUMAN(聚合)", reasons_map=reasons_map)
    except ValueError:
        summary = {"overall_rho": None, "overall_adj": None, "overall_exact": None, "common": []}
    summary["n"] = len(summary.get("common", []))
    return summary, per_content


@router.get("/calibration/samples")
async def calibration_samples():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Content))).scalars().all()
    out = []
    for c in rows:
        q = c.quality or {}
        scores = (q.get("scores") or {}) if isinstance(q, dict) else {}
        if not all(d in scores for d in DIMS):
            continue
        hsa = c.human_score_avg or {}
        out.append({
            "id": c.id,
            "market": c.market,
            "language": c.language or "",
            "title": c.title,
            "excerpt": _clean_ev(_body_to_text(c.body).strip()),
            "n_raters": hsa.get("n_raters", 0),
            "human_avg": {d: hsa.get(d) for d in DIMS},
        })
    return {"count": len(out), "samples": out}


@router.post("/calibration/scores")
async def calibration_scores(payload: dict):
    scores = payload.get("scores") or {}
    rater = (payload.get("rater") or "HUMAN").strip() or "HUMAN"
    if not scores:
        raise HTTPException(400, "scores 为空")

    async with SessionLocal() as session:
        # 防御性建表：极端情况下（部署迁移未覆盖 / 冷启动竞态）human_calibrations
        # 缺失会让 INSERT 直接 500。这里幂等兜底，保证表一定存在。
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS human_calibrations ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, content_id VARCHAR(36), "
            "rater VARCHAR(64), scores JSON, reasons JSON, created_at TIMESTAMP)"))
        await session.commit()
        for cid, dims in scores.items():
            if not isinstance(dims, dict):
                continue
            sc = {d: float(dims[d]["score"]) for d in DIMS if d in dims and isinstance(dims[d], dict)}
            rs = {d: (dims[d].get("reason", "") if isinstance(dims[d], dict) else "")
                  for d in DIMS if d in dims}
            session.add(HumanCalibration(content_id=cid, rater=rater, scores=sc, reasons=rs))
        await session.commit()
        try:
            summary, per_content = await _recompute_alignment(session)
        except Exception as e:  # 计算/写盘异常不应让提交整体 500
            import sys
            print(f"[calibration] 对齐计算跳过: {e!r}", file=sys.stderr)
            summary, per_content = ({"overall_rho": None, "overall_adj": None,
                                     "overall_exact": None, "n": 0, "common": [],
                                     "compute_error": str(e)}, {})

    return {"ok": True, **summary, "per_content": per_content}


@router.get("/calibration/report")
async def calibration_report():
    async with SessionLocal() as session:
        summary, _ = await _recompute_alignment(session)
    if not summary.get("common"):
        raise HTTPException(404, "尚无真人校准数据，请先提交打分")
    report_path = CAL_DIR / "calibration_report.md"
    chart_path = CAL_DIR / "calibration_chart.svg"
    if not report_path.exists():
        raise HTTPException(404, "报告生成失败")
    return {
        "markdown": report_path.read_text(encoding="utf-8"),
        "chart": chart_path.read_text(encoding="utf-8") if chart_path.exists() else "",
    }
