"""消费事件模拟器：让数据反馈闭环可演示

模拟逻辑符合产品直觉（面试可讲）：
- 质量分高 → CTR 高；video_script 形态 CTR 最高；负反馈与质量分负相关
- 不同市场有行为修正（日本完读高、巴西视频完播高等，来自市场档案直觉）

M2 升级：仿真器参数由 **M1 真实信号互动分布** 拟合（HN/Dev.to 的 points/comments），
函数携带 `calibrated_from` 元信息；UI 对仿真数据统一打「仿真」角标。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Content, ContentEvent, SessionLocal

# 市场行为修正系数（完读/完播倾向）
MARKET_FINISH_MOD = {"JP": 1.15, "KR": 1.0, "US": 1.0, "BR": 0.9, "CN": 1.05}
# 形态基础 CTR 倾向
FORMAT_CTR_MOD = {"video_script": 1.35, "card": 1.15, "brief_news": 1.0, "comment": 0.8, "article": 1.0}


async def compute_calibration(session) -> dict | None:
    """从 contents.signals（M1 真实抓取信号）拟合真实互动分布，用于校准仿真器。

    返回 None 表示无真实信号（降级为未校准的基线仿真）。
    """
    rows = (await session.execute(select(Content.signals))).scalars().all()
    scores, comments = [], []
    n = 0
    for sig_list in rows:
        if not sig_list:
            continue
        for s in sig_list:
            eng = (s or {}).get("engagement") or {}
            sc, cm = eng.get("score"), eng.get("comments")
            if isinstance(sc, (int, float)) and sc > 0:
                scores.append(float(sc)); n += 1
            if isinstance(cm, (int, float)) and cm > 0:
                comments.append(float(cm))

    def _median(xs):
        if not xs:
            return 0.0
        xs = sorted(xs)
        i = len(xs) // 2
        return xs[i] if len(xs) % 2 else (xs[i - 1] + xs[i]) / 2

    if not scores:
        return None
    return {
        "source": "M1 real signals (HN Algolia / Dev.to / GDELT)",
        "n_signals": n,
        "median_score": round(_median(scores), 1),
        "median_comments": round(_median(comments), 1),
        "calibrated_at": datetime.utcnow().isoformat(),
    }


async def simulate_events(content_id: str | None = None, per_content: int = 300,
                          seed: int | None = None,
                          calibration: dict | None = None,
                          auto_calibrate: bool = True) -> dict:
    rng = random.Random(seed)
    async with SessionLocal() as session:
        # 自动校准：用真实信号互动分布拟合基准曝光量
        if calibration is None and auto_calibrate:
            calibration = await compute_calibration(session)

        # 校准系数：真实 median score 越高 → 基准曝光越高（clamp 防极端）
        cal_factor = 1.0
        if calibration:
            med_score = calibration.get("median_score") or 0
            cal_factor = max(0.5, min(3.0, med_score / 100.0))

        q = select(Content).where(Content.status == "published")
        if content_id:
            q = q.where(Content.id == content_id)
        contents = (await session.execute(q)).scalars().all()
        total = 0
        now = datetime.utcnow()
        for c in contents:
            quality = (c.quality or {}).get("avg", 3.0) or 3.0
            q_mod = 0.5 + quality / 5.0  # 质量 1-5 → 0.7-1.5
            plan = (c.distribution or {}).get("plan") or [{"platform": "feed", "format": "article"}]
            for slot in plan:
                fmt = slot.get("format", "article")
                fmt_mod = FORMAT_CTR_MOD.get(fmt, 1.0)
                base = per_content * cal_factor
                exposures = int(base * rng.uniform(0.7, 1.3) / max(len(plan) - 0.5, 1))
                ctr = min(0.02 + 0.08 * q_mod * fmt_mod * rng.uniform(0.8, 1.2), 0.6)
                clicked = int(exposures * ctr)
                finish_mod = MARKET_FINISH_MOD.get(c.market, 1.0)
                finished = int(clicked * min(0.3 * q_mod * finish_mod * rng.uniform(0.8, 1.2), 0.95))
                completed_video = int(clicked * min(0.4 * q_mod * finish_mod, 0.95)) if fmt == "video_script" else 0
                liked = int(clicked * 0.06 * q_mod * rng.uniform(0.5, 1.5))
                shared = int(clicked * 0.03 * q_mod * rng.uniform(0.5, 1.5))
                negative = int(clicked * max(0.04 - quality * 0.008, 0.002) * rng.uniform(0.5, 1.5))
                for etype, nn in [("exposed", exposures), ("clicked", clicked),
                                  ("finished", finished), ("completed_video", completed_video),
                                  ("liked", liked), ("shared", shared), ("negative", negative)]:
                    for _ in range(max(nn, 0)):
                        # 在内容生命周期（72h）内按衰减分布打时间戳：越靠前曝光越多。
                        # 锚定到内容自身创建时间，保证 hours_since >= 0（事件不早于内容生成）
                        h = min(rng.expovariate(1 / 18.0), 72.0)
                        anchor = c.created_at or now
                        ts = anchor + timedelta(hours=h)
                        session.add(ContentEvent(
                            content_id=c.id, event_type=etype, market=c.market,
                            platform=slot.get("platform", "feed"), format=fmt, ts=ts))
                        total += 1
        await session.commit()
    return {
        "contents": len(contents), "events": total,
        # 校准元信息：告诉读者这份仿真「锚定」在多少真实信号上
        "calibrated_from": calibration,
    }
