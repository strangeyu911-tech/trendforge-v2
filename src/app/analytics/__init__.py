"""M2 分析中心：SQL 驱动的指标层包入口

所有分析用**手写 SQL**（不用 ORM），集中在 queries.py，每个图表描述符携带
真实执行的 SQL 原文。详见 queries.py。
"""
from app.analytics.queries import (
    build_dashboard,
    spec_qsr,
    spec_funnel,
    spec_fpy,
    spec_agent_degrade,
    spec_cost,
    spec_rubric,
    spec_decay,
    spec_format_market,
)

__all__ = [
    "build_dashboard", "spec_qsr", "spec_funnel", "spec_fpy",
    "spec_agent_degrade", "spec_cost", "spec_rubric", "spec_decay",
    "spec_format_market",
]
