"""M2 分析中心：SQL 驱动的指标层

所有分析均使用**手写 SQL**（不用 ORM），集中在 queries.py，且每个图表描述符
都携带「真实执行的 SQL 原文」——这是给面试官看的能力证据（JD 明确要求 SQL，
且是能力型而非功能型要求）。

设计原则：
- 供给效率 / 成本 / 质量 三类来自真实运行数据（task_spans / tasks / contents）
- 消费表现 来自 content_events（仿真器生成，UI 一律打「仿真」角标）
- 至少 2 条查询用到窗口函数或多级 CTE（下钻 / 同期群 / 排名）
"""
from __future__ import annotations

from sqlalchemy import text

# 人工对照基准（成本效率层用）：单条深度稿约 45 分钟人力，按社科/产品岗折算
HUMAN_MINUTES_PER_ARTICLE = 45
HUMAN_COST_PER_MIN_CNY = 2.0  # 运营/编辑时薪约 ¥120 → ¥2/min，仅用于对照演示


async def _run(session, sql: str):
    """执行一条 SQL，返回 (columns, rows)。sql 即 UI 展示的原文。"""
    res = await session.execute(text(sql))
    cols = list(res.keys())
    rows = [list(r) for r in res.fetchall()]
    return cols, rows


def _spec(*, id, title, reality, sql, columns, rows, chart, note="", headline=None):
    """统一图表描述符。chart ∈ bar|line|funnel|stat|table|cohort"""
    return {
        "id": id, "title": title, "reality": reality,  # real | simulated
        "sql": sql, "columns": columns, "rows": rows,
        "chart": chart, "note": note, "headline": headline or {},
    }


# ---------------------------------------------------------------------------
# 1. 北极星：有效内容供给率 QSR（多级 CTE）
# ---------------------------------------------------------------------------
QSR_SQL = """
WITH base AS (
  SELECT t.id,
         t.status                                                           AS ts,
         json_extract(c.quality, '$.verdict')                               AS verdict,
         json_extract(c.quality, '$.avg')                                   AS avg
  FROM tasks t
  LEFT JOIN contents c ON c.task_id = t.id
  WHERE t.kind = 'pipeline'
)
SELECT
  COUNT(*)                                                                  AS attempts,
  SUM(CASE WHEN ts = 'done' AND verdict = 'pass' AND avg >= 3.5
           THEN 1 ELSE 0 END)                                              AS qualified,
  ROUND(SUM(CASE WHEN ts = 'done' AND verdict = 'pass' AND avg >= 3.5
                 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 3)        AS qsr
FROM base;"""

QSR_BY_MARKET_SQL = """
WITH base AS (
  SELECT t.market                                                          AS mkt,
         t.status                                                           AS ts,
         json_extract(c.quality, '$.verdict')                               AS verdict,
         json_extract(c.quality, '$.avg')                                   AS avg
  FROM tasks t
  LEFT JOIN contents c ON c.task_id = t.id
  WHERE t.kind = 'pipeline'
)
SELECT mkt,
       COUNT(*)                                                            AS attempts,
       SUM(CASE WHEN ts = 'done' AND verdict = 'pass' AND avg >= 3.5
                THEN 1 ELSE 0 END)                                        AS qualified,
       ROUND(SUM(CASE WHEN ts = 'done' AND verdict = 'pass' AND avg >= 3.5
                     THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 3)   AS qsr
FROM base
GROUP BY mkt
ORDER BY qsr DESC;"""


async def spec_qsr(session):
    cols, rows = await _run(session, QSR_SQL)
    by_cols, by_rows = await _run(session, QSR_BY_MARKET_SQL)
    attempts = rows[0][0] if rows else 0
    qualified = rows[0][1] if rows else 0
    qsr = rows[0][2] if rows else 0
    # 把按市场 QSR 作为柱状图数据（columns=[mkt, qsr]）
    bar_labels = [r[0] for r in by_rows]
    bar_vals = [r[3] for r in by_rows]
    return _spec(
        id="qsr", title="北极星 · 有效内容供给率 QSR（真实）", reality="real",
        sql=QSR_BY_MARKET_SQL,
        columns=["市场", "QSR"], rows=list(zip(bar_labels, bar_vals)),
        chart="bar",
        note="QSR = 通过发布且质量均分≥3.5 的内容 / 进入生产的选题总数。反映引擎稳定供给可用内容的能力，是供给引擎的存在意义。",
        headline={"value": qsr, "sub": f"合格 {qualified} / 尝试 {attempts}", "suffix": "", "kind": "rate"},
    )


# ---------------------------------------------------------------------------
# 2. 消费漏斗（仿真）：曝光 → 点击 → 完读 → 互动
# ---------------------------------------------------------------------------
FUNNEL_SQL = """
SELECT event_type, COUNT(*) AS n
FROM content_events
GROUP BY event_type
ORDER BY n DESC;"""


async def spec_funnel(session):
    cols, rows = await _run(session, FUNNEL_SQL)
    m = {r[0]: r[1] for r in rows}
    exposed = m.get("exposed", 0)
    clicked = m.get("clicked", 0)
    finished = m.get("finished", 0)
    interacted = m.get("liked", 0) + m.get("shared", 0)
    stages = [
        ("曝光 Exposed", exposed),
        ("点击 Clicked", clicked),
        ("完读 Finished", finished),
        ("互动 Liked+Shared", interacted),
    ]
    ctr = round(clicked / exposed, 3) if exposed else 0
    fin = round(finished / max(clicked, 1), 3) if clicked else 0
    return _spec(
        id="funnel", title="消费漏斗（仿真）", reality="simulated",
        sql=FUNNEL_SQL,
        columns=["环节", "人数"], rows=stages, chart="funnel",
        note="数据来自 simulator.py 对真实互动锚点拟合后的行为仿真，UI 已标注「仿真」。CTR="
             f"{ctr}，完读率={fin}。真实分发消费数据校招生无法获取，故用有真实锚点的仿真替代。",
    )


# ---------------------------------------------------------------------------
# 3. 一次通过率 FPY + 重写率（多级 CTE）
# ---------------------------------------------------------------------------
FPY_SQL = """
WITH base AS (
  SELECT t.id,
         t.status                                                            AS task_status,
         t.review_rounds                                                     AS rr,
         json_extract(c.quality, '$.verdict')                                AS verdict
  FROM tasks t
  LEFT JOIN contents c ON c.task_id = t.id
  WHERE t.kind = 'pipeline'
)
SELECT
  COUNT(*)                                                                   AS attempts,
  ROUND(SUM(CASE WHEN verdict = 'pass' AND rr = 0 THEN 1 ELSE 0 END) * 1.0
       / NULLIF(COUNT(*), 0), 3)                                            AS fpy,
  ROUND(SUM(CASE WHEN rr > 0 THEN 1 ELSE 0 END) * 1.0
       / NULLIF(COUNT(*), 0), 3)                                            AS rewrite_rate
FROM base;"""


async def spec_fpy(session):
    cols, rows = await _run(session, FPY_SQL)
    attempts = rows[0][0] if rows else 0
    fpy = rows[0][1] if rows else 0
    rewrite = rows[0][2] if rows else 0
    return _spec(
        id="fpy", title="供给效率 · 一次通过率 FPY & 重写率（真实）", reality="real",
        sql=FPY_SQL,
        columns=["指标", "值"], rows=[("一次通过率 FPY", fpy), ("重写率", rewrite)],
        chart="bar",
        note="FPY=首次即 pass 的内容占比；重写率=触发过 revise 轮次的内容占比。两者共同刻画链路一次成稿能力。",
        headline={"value": fpy, "sub": f"重写率 {rewrite} · 尝试 {attempts}", "suffix": "", "kind": "rate"},
    )


# ---------------------------------------------------------------------------
# 4. Agent 降级率（窗口函数 RANK 排名最脆弱环节）
# ---------------------------------------------------------------------------
AGENT_DEGRADE_SQL = """
SELECT agent, spans, bad, degrade_rate,
       RANK() OVER (ORDER BY degrade_rate DESC) AS fragility_rank
FROM (
  SELECT agent,
         COUNT(*)                                                       AS spans,
         SUM(CASE WHEN status IN ('degraded', 'failed') THEN 1 ELSE 0 END) AS bad,
         ROUND(SUM(CASE WHEN status IN ('degraded', 'failed') THEN 1 ELSE 0 END) * 1.0
              / NULLIF(COUNT(*), 0), 3)                                AS degrade_rate
  FROM task_spans
  GROUP BY agent
)
ORDER BY degrade_rate DESC;"""


async def spec_agent_degrade(session):
    cols, rows = await _run(session, AGENT_DEGRADE_SQL)
    labels = [r[0] for r in rows]
    vals = [r[3] for r in rows]
    # 柱状图用 degrade_rate 作为数值列（完整 SQL/列已保留在 sql/columns 字段）
    bar_rows = [(r[0], r[3]) for r in rows]
    return _spec(
        id="agent_degrade", title="Agent 降级率（真实 · 窗口函数排名）", reality="real",
        sql=AGENT_DEGRADE_SQL,
        columns=["agent", "spans", "bad", "degrade_rate", "fragility_rank"],
        rows=bar_rows, chart="bar",
        note="各 Agent 的 task_spans 中 degraded/failed 占比，按 RANK() 窗口函数标出最脆弱环节。降级率升 → 触发 KBCurator 补库或调 Prompt。",
    )


# ---------------------------------------------------------------------------
# 5. 成本看板（窗口函数 RANK + 单位质量分成本）
# ---------------------------------------------------------------------------
COST_SQL = """
SELECT market, avg_cost_cny, avg_sec, cost_per_qscore,
       RANK() OVER (ORDER BY avg_cost_cny DESC) AS cost_rank
FROM (
  SELECT t.market,
         ROUND(AVG(t.total_cost_cny), 4)                                  AS avg_cost_cny,
         ROUND(AVG(t.total_duration_ms) / 1000.0, 1)                     AS avg_sec,
         ROUND(AVG(t.total_cost_cny / NULLIF(json_extract(c.quality, '$.avg'), 0)), 4)
                                                                          AS cost_per_qscore
  FROM tasks t
  JOIN contents c ON c.task_id = t.id
  WHERE t.status = 'done' AND json_extract(c.quality, '$.avg') > 0
  GROUP BY t.market
);"""

COST_GLOBAL_SQL = """
SELECT
  ROUND(AVG(total_cost_cny), 4)   AS avg_cost_cny,
  ROUND(AVG(total_duration_ms)/1000.0, 1) AS avg_sec,
  COUNT(*)                       AS n_done
FROM tasks t
JOIN contents c ON c.task_id = t.id
WHERE t.status = 'done' AND json_extract(c.quality, '$.avg') > 0;"""


async def spec_cost(session):
    cols, rows = await _run(session, COST_SQL)
    g_cols, g_rows = await _run(session, COST_GLOBAL_SQL)
    labels = [r[0] for r in rows]
    cost_vals = [r[1] for r in rows]
    # 成本对照（人工 vs AI）
    avg_cost = g_rows[0][0] if g_rows else 0
    avg_sec = g_rows[0][1] if g_rows else 0
    human_cost = round(HUMAN_MINUTES_PER_ARTICLE * HUMAN_COST_PER_MIN_CNY, 2)
    saving = round(human_cost / avg_cost, 1) if avg_cost else 0
    headline = {
        "value": f"¥{avg_cost:.3f}", "sub": f"人工约 ¥{human_cost:.2f}（{HUMAN_MINUTES_PER_ARTICLE}min）→ 省 {saving}×",
        "suffix": " /条", "kind": "cost",
    }
    # 柱状图用 avg_cost_cny 作为数值列（完整 SQL/列已保留在 sql/columns 字段）
    bar_rows = [(r[0], r[1]) for r in rows]
    return _spec(
        id="cost", title="成本效率 · 单条成本（真实）", reality="real",
        sql=COST_SQL,
        columns=["市场", "单条成本¥", "耗时s", "单位质量分成本", "cost_rank"],
        rows=bar_rows, chart="bar",
        note=f"单条 LLM 成本 ¥{avg_cost:.3f}（{avg_sec:.0f}s）。对照：人工写一条深度稿约 {HUMAN_MINUTES_PER_ARTICLE} 分钟 ≈ ¥{human_cost:.2f}，"
             "AI 把边际成本从人力线性压到算力近常数。单位质量分成本 = 总成本 / 质量均分，越低越划算。",
        headline=headline,
    )


# ---------------------------------------------------------------------------
# 6. 质量 Rubric 五维（真实，按市场）
# ---------------------------------------------------------------------------
RUBRIC_SQL = """
SELECT market,
       ROUND(AVG(json_extract(quality, '$.scores.accuracy')), 2)     AS accuracy,
       ROUND(AVG(json_extract(quality, '$.scores.angle')), 2)        AS angle,
       ROUND(AVG(json_extract(quality, '$.scores.readability')), 2)  AS readability,
       ROUND(AVG(json_extract(quality, '$.scores.local_fit')), 2)    AS local_fit,
       ROUND(AVG(json_extract(quality, '$.scores.engagement')), 2)   AS engagement,
       ROUND(AVG(json_extract(quality, '$.avg')), 2)                 AS avg,
       SUM(CASE WHEN json_extract(quality, '$.verdict') = 'pass'   THEN 1 ELSE 0 END) AS n_pass,
       SUM(CASE WHEN json_extract(quality, '$.verdict') = 'revise' THEN 1 ELSE 0 END) AS n_revise,
       SUM(CASE WHEN json_extract(quality, '$.verdict') = 'reject' THEN 1 ELSE 0 END) AS n_reject
FROM contents
WHERE quality IS NOT NULL
GROUP BY market;"""


async def spec_rubric(session):
    cols, rows = await _run(session, RUBRIC_SQL)
    dims = ["accuracy", "angle", "readability", "local_fit", "engagement"]
    labels = [r[0] for r in rows]
    # 每个市场一行；每组柱 = 5 维。rows 已是 [market, acc, ang, rd, lf, eng, avg, ...]
    series = [{"name": d, "data": [r[i + 1] for r in rows]} for i, d in enumerate(dims)]
    return _spec(
        id="rubric", title="质量 Rubric 五维（真实 · 按市场）", reality="real",
        sql=RUBRIC_SQL,
        columns=["市场"] + dims, rows=rows, chart="grouped_bar",
        note="EditorAgent 每次供给落库的五维评分（1–5）。哪个市场哪维最弱 → 定向补 market 文化注释或调 Prompt。",
        headline={"series": series, "labels": labels},
    )


# ---------------------------------------------------------------------------
# 7. 内容衰减曲线（同期群，窗口函数累计）—— 仿真
# ---------------------------------------------------------------------------
DECAY_SQL = """
SELECT market, hours_since, exposures,
       SUM(exposures) OVER (PARTITION BY market ORDER BY hours_since) AS cum
FROM (
  SELECT c.market                                                      AS market,
         CAST((julianday(e.ts) - julianday(c.created_at)) * 24 AS INTEGER) AS hours_since,
         COUNT(*)                                                     AS exposures
  FROM content_events e
  JOIN contents c ON c.id = e.content_id
  WHERE e.event_type = 'exposed'
  GROUP BY c.market, hours_since
)
ORDER BY market, hours_since;"""


async def spec_decay(session):
    cols, rows = await _run(session, DECAY_SQL)
    # pivot: x = hours_since, series per market (cumulative)
    hours = sorted({r[1] for r in rows})
    by_mkt = {}
    for r in rows:
        by_mkt.setdefault(r[0], {})[r[1]] = r[3]
    series = [{"name": m, "data": [by_mkt[m].get(h, 0) for h in hours]} for m in by_mkt]
    return _spec(
        id="decay", title="内容衰减曲线 · 同期群累计曝光（仿真）", reality="simulated",
        sql=DECAY_SQL,
        columns=["market", "hours_since", "exposures", "cum"], rows=rows, chart="cohort",
        note="按发布后小时累计曝光，观察内容生命周期。窗口函数 SUM() OVER (PARTITION BY market) 做累计。仿真数据。",
        headline={"series": series, "labels": [str(h) for h in hours]},
    )


# ---------------------------------------------------------------------------
# 8. 分形态 × 市场 CTR 下钻（仿真）
# ---------------------------------------------------------------------------
FORMAT_MARKET_SQL = """
SELECT format, market,
       SUM(CASE WHEN event_type = 'exposed' THEN 1 ELSE 0 END) AS exposed,
       SUM(CASE WHEN event_type = 'clicked' THEN 1 ELSE 0 END) AS clicked
FROM content_events
GROUP BY format, market;"""


async def spec_format_market(session):
    cols, rows = await _run(session, FORMAT_MARKET_SQL)
    # 计算每个 (format, market) 的 CTR，pivot 成 形态×市场 表
    ctr = {}
    for fmt, mkt, exp, clk in rows:
        ctr[(fmt, mkt)] = round(clk / exp, 3) if exp else 0
    formats = sorted({r[0] for r in rows})
    markets = sorted({r[1] for r in rows})
    table = [[fmt] + [ctr.get((fmt, m), 0) for m in markets] for fmt in formats]
    return _spec(
        id="format_market", title="分形态 × 市场 CTR 下钻（仿真）", reality="simulated",
        sql=FORMAT_MARKET_SQL,
        columns=["形态"] + markets, rows=table, chart="heat",
        note="每个单元格 = 该形态在该市场的点击率（clicked/exposed）。反向指导 format_plan 权重。仿真数据。",
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
async def build_dashboard(session):
    specs = [
        await spec_qsr(session),
        await spec_funnel(session),
        await spec_fpy(session),
        await spec_agent_degrade(session),
        await spec_cost(session),
        await spec_rubric(session),
        await spec_decay(session),
        await spec_format_market(session),
    ]
    return specs
