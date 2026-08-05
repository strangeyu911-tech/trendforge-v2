"""消费事件模拟器：让数据反馈闭环可演示

模拟逻辑符合产品直觉（面试可讲）：
- 质量分高 → CTR 高；video_script 形态 CTR 最高；负反馈与质量分负相关
- 不同市场有行为修正（日本完读高、巴西视频完播高等，来自市场档案直觉）
"""
from __future__ import annotations

import random

from sqlalchemy import select

from app.models import Content, ContentEvent, SessionLocal

# 市场行为修正系数（完读/完播倾向）
MARKET_FINISH_MOD = {"JP": 1.15, "KR": 1.0, "US": 1.0, "BR": 0.9, "CN": 1.05}
# 形态基础 CTR 倾向
FORMAT_CTR_MOD = {"video_script": 1.35, "card": 1.15, "brief_news": 1.0, "comment": 0.8, "article": 1.0}


async def simulate_events(content_id: str | None = None, per_content: int = 300,
                          seed: int | None = None) -> dict:
    rng = random.Random(seed)
    async with SessionLocal() as session:
        q = select(Content).where(Content.status == "published")
        if content_id:
            q = q.where(Content.id == content_id)
        contents = (await session.execute(q)).scalars().all()
        total = 0
        for c in contents:
            quality = (c.quality or {}).get("avg", 3.0) or 3.0
            q_mod = 0.5 + quality / 5.0  # 质量 1-5 → 0.7-1.5
            plan = (c.distribution or {}).get("plan") or [{"platform": "feed", "format": "article"}]
            for slot in plan:
                fmt = slot.get("format", "article")
                fmt_mod = FORMAT_CTR_MOD.get(fmt, 1.0)
                exposures = int(per_content * rng.uniform(0.7, 1.3) / max(len(plan) - 0.5, 1))
                ctr = min(0.02 + 0.08 * q_mod * fmt_mod * rng.uniform(0.8, 1.2), 0.6)
                clicked = int(exposures * ctr)
                finish_mod = MARKET_FINISH_MOD.get(c.market, 1.0)
                finished = int(clicked * min(0.3 * q_mod * finish_mod * rng.uniform(0.8, 1.2), 0.95))
                completed_video = int(clicked * min(0.4 * q_mod * finish_mod, 0.95)) if fmt == "video_script" else 0
                liked = int(clicked * 0.06 * q_mod * rng.uniform(0.5, 1.5))
                shared = int(clicked * 0.03 * q_mod * rng.uniform(0.5, 1.5))
                negative = int(clicked * max(0.04 - quality * 0.008, 0.002) * rng.uniform(0.5, 1.5))
                for etype, n in [("exposed", exposures), ("clicked", clicked),
                                 ("finished", finished), ("completed_video", completed_video),
                                 ("liked", liked), ("shared", shared), ("negative", negative)]:
                    for _ in range(max(n, 0)):
                        session.add(ContentEvent(
                            content_id=c.id, event_type=etype, market=c.market,
                            platform=slot.get("platform", "feed"), format=fmt))
                        total += 1
        await session.commit()
    return {"contents": len(contents), "events": total}
