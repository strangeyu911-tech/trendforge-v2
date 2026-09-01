# TrendForge V2 供给引擎指标体系 `METRICS_FRAMEWORK_v1.0`

> 一份给「内容供给引擎 PM」看的指标字典：不是把能数的都数一遍，而是**定义引擎的存在意义（北极星）**，再用四层指标回答"它为什么好 / 为什么不好 / 花了多少钱 / 用户买不买账"。
> 每个指标都给出**口径定义、数据来源表与字段、可执行 SQL**。SQL 在 M2 分析中心以 `queries.py` 集中管理、UI 可展开原文——本文档的 SQL 即该中心的实现契约。

---

## 0. 为什么要有这套指标（PM 视角）

内容供给引擎的核心商业问题是：**用尽可能低的边际成本，稳定地产出"在目标市场能打"的内容。**

这决定了指标体系的三条纪律：

1. **不堆虚荣指标**。阅读量、粉丝数这类"用户主体"指标，项目里没有用户实体，硬做必假。我们盯的是**内容侧 + 供给侧**指标——引擎自己能控制、能归因的那部分。
2. **质量与成本必须成对出现**。只看质量分高会鼓励"堆算力换质量"；只看成本低会鼓励"降级保成本"。北极星把两者绑死。
3. **诚实标注仿真**。真实分发消费数据（点击/完读）任何校招生都拿不到——没有产品、没有用户。后置分发表现由 `simulator.py` 参数化仿真生成，UI 一律打"仿真"角标；但**前置信号真实**（M1 接入 HN/Dev.to/GDELT，带真实 points/comments/tone），用真实互动分布去校准仿真器参数。所以我们的仿真"有真实锚点"，不是拍脑袋随机数。

---

## 1. 北极星指标：有效内容供给率（Qualified Supply Rate, QSR）

**定义**：进入生产的选题中，最终以"通过(pass)"发布、且质量均分 ≥ 阈值的内容占比。

```
QSR = 通过发布的内容数 / 进入生产的选题总数
```

- **分子**：`tasks.kind='pipeline'` 且 `tasks.status='done'`，且对应 `contents.quality.verdict='pass'` 且 `quality.avg ≥ 3.5`（阈值可配）。
- **分母**：`tasks.kind='pipeline'` 全部尝试（含被总编 reject、成稿失败、自动换题重试后仍失败的）。

**为什么是它**：它直接回答"引擎能不能稳定地产出可用内容"——这是供给引擎的**存在意义**。QSR 高，说明信号→选题→成稿→审核的全链路在真实输入下是通的；QSR 低，说明某一环在漏（信号不相关 / 选题撞车 / 事实撑不住 / 文化不服）。

**为什么阈值绑质量分**：避免"为了通过率而放水"——`verdict='pass'` 只是总编裁决，可能勉强通过。加 `avg ≥ 3.5` 把"勉强过"排除在北极星之外。

```sql
-- 北极星：有效内容供给率（阈值 3.5 可调）
SELECT
  COUNT(*)                                                          AS attempts,
  SUM(CASE WHEN t.status='done'
                AND json_extract(c.quality,'$.verdict')='pass'
                AND json_extract(c.quality,'$.avg') >= 3.5 THEN 1 ELSE 0 END) AS qualified,
  ROUND( SUM(CASE WHEN t.status='done'
                AND json_extract(c.quality,'$.verdict')='pass'
                AND json_extract(c.quality,'$.avg') >= 3.5 THEN 1 ELSE 0 END)
         * 1.0 / NULLIF(COUNT(*),0), 3)                            AS qsr
FROM tasks t
LEFT JOIN contents c ON c.task_id = t.id
WHERE t.kind = 'pipeline';
```

> 状态：QSR 当前即可计算（Tasks/Contents 字段齐全）。M2 分析中心把它做成首页大数字 + 按市场/周的下钻。

---

## 2. 四层指标分解

四层不是并列罗列，而是北极星的**因果拆解**：`供给效率`决定"能不能稳定产出"，`内容质量`决定"产出的东西达不达标"，`消费表现`决定"达标的东西市场认不认"（仿真），`成本效率`决定"这件事划不划算"。

### 2.1 供给效率（Supply Efficiency）—— 链路通不通

| 指标 | 定义 / 口径 | 数据来源 | 状态 |
|---|---|---|---|
| 信号→发布 Lead Time | 从信号被 `SignalScout` 拉取到内容 `published` 的总时延中位/均值（秒）。信号时间取 `signals[].published_at` 最新者，发布时间取 `contents.created_at` | `task_spans`(signal_scout 起)、`contents.created_at`/`signals` | 已可算 |
| 一次通过率 FPY | `verdict='pass'` 且 `review_rounds=0` 的内容 / 总供给尝试 | `tasks.status`, `contents.quality.verdict`, `tasks.review_rounds` | 已可算 |
| 重写率 | `verdict='revise'` 触发过 `run_revise_rounds` 的内容占比（含最终仍 revise/reject） | `tasks.review_rounds > 0` | 已可算 |
| Agent 降级率 | 各 Agent 的 `task_spans.status` 为 `degraded`/`failed` 的 span 占比，按 agent 下钻 | `task_spans(agent,status)` | 已可算 |
| 选题否决率 | `PipelineRejected`（无证据 / 总编 reject / 换题重试）次数 / 尝试数；区分 `Q`(质量) 与 `F`(事实) 否决 | `bad_cases.category`, `tasks.error` | 已可算 |

```sql
-- 一次通过率 + 重写率（同一条 CTE 一次算清）
WITH base AS (
  SELECT t.id, t.status AS task_status, t.review_rounds,
         json_extract(c.quality,'$.verdict') AS verdict
  FROM tasks t LEFT JOIN contents c ON c.task_id = t.id
  WHERE t.kind = 'pipeline'
)
SELECT
  ROUND(SUM(CASE WHEN verdict='pass' AND review_rounds=0 THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0),3) AS fpy,
  ROUND(SUM(CASE WHEN review_rounds>0 THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0),3) AS rewrite_rate
FROM base;
```

```sql
-- Agent 降级率（窗口函数：按 agent 聚合，标出最脆弱的环节）
SELECT agent,
       COUNT(*)                                                    AS spans,
       SUM(CASE WHEN status IN ('degraded','failed') THEN 1 ELSE 0 END) AS bad,
       ROUND(SUM(CASE WHEN status IN ('degraded','failed') THEN 1 ELSE 0 END)*1.0
            /NULLIF(COUNT(*),0),3)                                AS degrade_rate
FROM task_spans
GROUP BY agent
ORDER BY degrade_rate DESC;
```

### 2.2 内容质量（Content Quality）—— 产出达不达标

基于 `EditorAgent` 的 **Rubric 五维**（1–5 分）+ 总编裁决。五维即"人定义标准"的量化体现。

| 维度 | 含义 | 数据字段 |
|---|---|---|
| `accuracy` 准确性 | 事实是否站得住（与 `fact_check` 互证） | `quality.scores.accuracy` |
| `angle` 角度 | 选题切入是否新颖、有信息增量 | `quality.scores.angle` |
| `readability` 可读性 | 结构、节奏、是否易读 | `quality.scores.readability` |
| `local_fit` 本土适配 | 是否符合市场文化/禁忌/语气 | `quality.scores.local_fit` |
| `engagement` 吸引力 | 钩子、CTA、互动引导是否到位 | `quality.scores.engagement` |

衍生指标：

- **Rubric 五维均值分布**：按市场/形态下钻，定位"哪个市场哪维最弱"（例：JP 的 `local_fit` 常因敬语不足被扣）。
- **裁决结构**：`pass / revise / reject` 占比（健康系统应 pass 占多数，reject 极少数）。
- **无据论断数**：`quality.fact_check.unsupported_claims` 计数——衡量"AI 有没有在编"。

#### 2.2.1 主题一致性（TCS）闸门指标（M5 新增）

TopicGuard 在 Writer 后、FactChecker 前插入，用 `[ev_xxx]` 引用结构（**零 token 成本、语言无关**）计算 **Topic Consistency Score**，专门拦截"拼盘稿"（主线被无关新闻污染）。

| 字段 | 含义 | 数据来源 |
|---|---|---|
| `quality.topic_guard.tcs` | 主题一致性分（0–1，越高越纯） | `app/rag/tcs.py` 规则计算 |
| `quality.topic_guard.main_ratio` | 主干引用占比（≥0.6 才放行） | 引用 × `is_main` 标记 |
| `quality.topic_guard.cross_docs` | 不同文档数（≤2 才放行） | 引用去重计数 |
| `quality.topic_guard.drift_sections` | 被判定为漂移的节索引 | 有引用却无主干引用 / 词面相关度 <0.15 |
| `quality.topic_guard.passed` | 是否通过闸门 | 上述三条件全满足 |

**为什么放进质量层**：拼盘稿即便 Rubric 五维分高，也是"内容不达标"——它破坏的是"这篇内容是否在讲一件事"。TCS 把"主题纯度"显式量化，和 Rubric 互补：Rubric 管"写得好不好"，TCS 管"有没有跑题"。

```sql
-- 主题一致性闸门命中率（漂移拦截 / 总供给）
SELECT
  COUNT(*)                                                       AS attempts,
  SUM(CASE WHEN json_extract(quality,'$.topic_guard.passed')=1
           THEN 1 ELSE 0 END)                                   AS passed,
  ROUND(SUM(CASE WHEN json_extract(quality,'$.topic_guard.passed')=1
                 THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0),3)   AS tcs_pass_rate,
  ROUND(AVG(json_extract(quality,'$.topic_guard.tcs')),3)       AS avg_tcs
FROM contents
WHERE quality IS NOT NULL;
```

> 状态：TCS 自 M5 起每次供给自动落库 `quality.topic_guard`；闸门可零成本全量计算。设计取舍见 [docs/DRIFT_GUARD_DESIGN_v1.0.md](docs/DRIFT_GUARD_DESIGN_v1.0.md)。

```sql
-- 五维均值 + 裁决结构（JSON 字段用 json_extract 展开）
SELECT
  market,
  ROUND(AVG(json_extract(quality,'$.scores.accuracy')),2)     AS accuracy,
  ROUND(AVG(json_extract(quality,'$.scores.angle')),2)        AS angle,
  ROUND(AVG(json_extract(quality,'$.scores.readability')),2)   AS readability,
  ROUND(AVG(json_extract(quality,'$.scores.local_fit')),2)     AS local_fit,
  ROUND(AVG(json_extract(quality,'$.scores.engagement')),2)    AS engagement,
  ROUND(AVG(json_extract(quality,'$.avg')),2)                  AS avg,
  SUM(CASE WHEN json_extract(quality,'$.verdict')='pass'   THEN 1 ELSE 0 END) AS n_pass,
  SUM(CASE WHEN json_extract(quality,'$.verdict')='revise' THEN 1 ELSE 0 END) AS n_revise,
  SUM(CASE WHEN json_extract(quality,'$.verdict')='reject' THEN 1 ELSE 0 END) AS n_reject
FROM contents
WHERE quality IS NOT NULL
GROUP BY market;
```

> 状态：五维评分随每次供给自动落库，分布图是 M2 分析中心的标配。

### 2.3 消费表现（Consumption，仿真）—— 市场认不认

**诚实前提**：`content_events` 由 `simulator.py` 生成，是对"真实互动锚点"拟合后的**行为仿真**，UI 全部标注"仿真"。它不是真实业务数据，是"在真实信号校准下的合理预期"。

漏斗：`exposed 曝光 → clicked 点击 → finished 完读 → liked/shared 互动`（含 `negative` 负反馈、`completed_video` 视频看完）。

| 指标 | 口径 | 来源 |
|---|---|---|
| 点击率 CTR | `clicked / exposed` | `content_events` |
| 完读率 | `finished / clicked` | `content_events` |
| 互动率 | `(liked+shared) / exposed` | `content_events` |
| 负反馈率 | `negative / exposed` | `content_events` |
| 内容衰减曲线 | 发布后 24/48/72h 的 `exposed` 衰减 | `content_events.ts` 相对 `contents.created_at` |
| 分市场 × 形态下钻 | 上述指标按 `market × format` 切片 | `content_events.market/format` |
| **消费时长** | `AVG(read_duration_s)`（clicked/finished 事件，按形态/市场切片）；时长分桶 × 完读占比，定位"点了但留不住人"的形态 | `content_events.read_duration_s`（仿真器按形态基线生成） |

```sql
-- 漏斗 + CTR/完读率（同级 CTE：先按事件类型聚合，再做比率）
WITH ev AS (
  SELECT content_id,
         SUM(event_type='exposed')  AS exposed,
         SUM(event_type='clicked')  AS clicked,
         SUM(event_type='finished') AS finished,
         SUM(event_type='liked') + SUM(event_type='shared') AS interacted
  FROM content_events
  GROUP BY content_id
)
SELECT
  ROUND(AVG(exposed),1)                                           AS avg_exposed,
  ROUND(AVG(clicked)*1.0/NULLIF(AVG(exposed),0),3)                AS ctr,
  ROUND(AVG(finished)*1.0/NULLIF(AVG(clicked),0),3)               AS finish_rate,
  ROUND(AVG(interacted)*1.0/NULLIF(AVG(exposed),0),3)             AS interact_rate
FROM ev;
```

```sql
-- 同期群衰减：发布批次 × 小时后曝光（窗口函数做累计）
SELECT
  c.market,
  CAST((julianday(e.ts) - julianday(c.created_at)) * 24 AS INT) AS hours_since,
  COUNT(*)                                                      AS exposures
FROM content_events e
JOIN contents c ON c.id = e.content_id
WHERE e.event_type = 'exposed'
GROUP BY c.market, hours_since
ORDER BY c.market, hours_since;
```

### 2.4 成本效率（Cost Efficiency）—— 划不划算

这是**成本最低、PM 味最浓**的一层：`Task`/`TaskSpan` 已在每次供给时存了 token 与耗时，只需聚合成"单条 = X token = Y 元 vs 人工 Z 分钟"。

| 指标 | 口径 | 来源 |
|---|---|---|
| 单条内容成本 | `tasks.total_cost_cny` 均值（仅 done） | `tasks` |
| 单条耗时 | `tasks.total_duration_ms` 均值 / 1000 | `tasks` |
| Agent 级成本分布 | 各 agent 的 `cost_cny` 汇总，定位"最贵的一环" | `task_spans` |
| **单位质量分成本** | `total_cost_cny / quality.avg`（越低越划算） | `tasks` × `contents.quality` |
| 人工对比 | 单条 ¥X vs 人工 Y 分钟（运营写一条深度稿 ~45min，按岗位成本折算） | 外部基准 |

```sql
-- 单条成本 + 单位质量分成本（跨表 join，分母是质量均分）
SELECT
  t.market,
  ROUND(AVG(t.total_cost_cny), 4)                               AS avg_cost_cny,
  ROUND(AVG(t.total_duration_ms)/1000.0, 1)                     AS avg_sec,
  ROUND(AVG(t.total_cost_cny / NULLIF(json_extract(c.quality,'$.avg'),0)), 4)
                                                                 AS cost_per_qscore
FROM tasks t
JOIN contents c ON c.task_id = t.id
WHERE t.status = 'done' AND json_extract(c.quality,'$.avg') > 0
GROUP BY t.market;
```

> 定价：DeepSeek `deepseek-v4-flash`，入 ¥2 / 1M tokens、出 ¥8 / 1M tokens（`config.py` `llm_price_in/out`，仅展示用）。单条约 3–8 万 token，成本量级 ¥0.1–0.4/条。

### 2.5 留存与消费时长（Retention & Dwell）—— 边界、代理指标与规模化口径

**边界声明（先说不做什么）**：留存是**用户主体**指标（次日/7 日留存 = 用户维度聚合），本项目没有用户实体（`content_events` 不含 user_id），在无真实用户的 Demo 上硬造留存数字 = 造假。因此 Demo 阶段交付的是**内容侧代理指标**；规模化口径给出设计，待用户实体落地后实施。

**Demo 阶段：内容侧代理指标（已实现）**

| 代理指标 | 回答的问题 | 口径 |
|---|---|---|
| 内容衰减曲线 | 内容发出后还能"留住"消费多久？（内容侧留存形状） | `exposed` 按 `hours_since` 累计（§2.3，`DECAY_SQL` 窗口函数） |
| 消费时长 × 完读 | 点进来的人停留多久、在哪一档流失？ | `read_duration_s` 分桶 × `finish_share`（`DURATION_FINISH_SQL`） |
| 分形态时长 | 哪种形态天然留人？反推形态×市场权重 | `AVG(read_duration_s) GROUP BY format`（`READ_DURATION_SQL`） |

**规模化阶段：真实留存口径（设计，需用户实体）**

```sql
-- 前置：content_events 需增加 user_id 维度（埋点层），本 SQL 为规模化设计稿
-- 次日/7日留存（按内容消费定义：窗口内再次产生 clicked/finished 事件视为回访）
WITH first_read AS (
  SELECT user_id, MIN(date(ts)) AS first_day
  FROM content_events
  WHERE event_type IN ('clicked', 'finished') AND user_id IS NOT NULL
  GROUP BY user_id
)
SELECT
  f.first_day,
  ROUND(SUM(CASE WHEN EXISTS (
            SELECT 1 FROM content_events e
            WHERE e.user_id = f.user_id AND date(e.ts) = date(f.first_day, '+1 day')
              AND e.event_type IN ('clicked','finished'))
          THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 3) AS day1_retention,
  ROUND(SUM(CASE WHEN EXISTS (
            SELECT 1 FROM content_events e
            WHERE e.user_id = f.user_id AND date(e.ts) BETWEEN date(f.first_day,'+1 day')
                  AND date(f.first_day,'+7 day')
              AND e.event_type IN ('clicked','finished'))
          THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 3) AS day7_retention
FROM first_read f
GROUP BY f.first_day
ORDER BY f.first_day;
```

**留存怎么反哺供给引擎（规模化闭环）**：按"首读内容形态/品类 × 是否回访"切分留存 → 回访人群的首读品类即"引流品类"，流失人群的 last-read 品类即"流失品类" → 反馈到 `markets.json` interests 权重与 AngleEditor 的选题配比。这是内容供给侧能吃到的留存红利：**留存不是 App 的指标，是选题组合质量的滞后指标**。

---

## 3. 指标间因果链（怎么用这套指标做决策）

```
                 [北极星] 有效内容供给率 QSR
                          │
        ┌─────────┬───────┴────────┬────────────┐
   供给效率      内容质量        消费表现        成本效率
  (通不通)      (达不达标)      (认不认·仿真)   (划不划算)
   lead time     Rubric 五维      CTR/完读       单条成本
   FPY /重写率   裁决结构         衰减曲线       单位质量分成本
   Agent降级率    无据论断数      分市场×形态      Agent成本分布
```

**决策示例（把指标变成动作）**：
- QSR 跌但 FPY 正常 → 看 `消费表现`：内容质量达标却没人点 → 问题在 `angle/engagement` 或 `分发 timing`（Distributor）。
- QSR 跌且重写率高 → 看 `供给效率` 的 `Agent降级率`：若是 `researcher` 降级多 → 知识库覆盖不足，触发 KBCurator 补库。
- 单位质量分成本升 → 看 `Agent成本分布`：若是 `writer` 占大头 → 调 `max_review_rounds` 或减少重跑。
- 某市场 `local_fit` 持续低 → 进 `markets.json` 补 `culture_notes`，或调 `editor` prompt——这是"人定义标准"的闭环入口。

---

## 4. 数据来源与诚实标注一览

| 数据 | 真实性 | 说明 |
|---|---|---|
| 信号 `signals[]` | **真实** | M1：HN Algolia / Dev.to / GDELT，带真实 points/comments/tone 与原文链接 |
| 质量 Rubric / 裁决 | **真实**（LLM 产出） | `EditorAgent` 每次供给落库 |
| 主题一致性 TCS / 闸门 | **真实**（规则计算，零 token） | `TopicGuard` 每次供给落库 `quality.topic_guard` |
| 成本 / 耗时 / span | **真实** | 每次 LLM 调用记录 token 与耗时 |
| 失败归因 `bad_cases` | **真实** | 链路否决/降级自动入档 |
| 消费事件 `content_events` | **仿真** | `simulator.py` 对真实互动锚点拟合；UI 标"仿真" |

> 为什么消费是仿真而非造假：校招生没有真实产品与用户，硬造点击数是"假数据"，比"标注仿真"更减分。我们用真实信号校准仿真参数，把"仿真"变成"有真实锚点的合理预期"，并在任何消费图表上显式标注。

---

## 5. 与里程碑的关系

- **M2 分析中心**：把本文档的 SQL 落地为 `src/app/analytics/queries.py`，UI 每张图可展开真实 SQL 原文；≥4 张图、≥2 条用到窗口函数/多级 CTE。
- **M4 RUN_REPORT**：用本文档指标体系，对 M1 真实信号跑出的 N 条内容做汇总（成功率 / 失败归因 / lead time / 成本 / Rubric 五维分布）。
- **M3 闭环**：`FeedbackAnalyst` 建议结构化后，可直接指向本文档某指标（如"JP 的 local_fit 偏低，建议补充敬语约束"），形成"指标预警 → 建议 → 人审采纳 → 下轮对比"的闭环。
