# TrendForge V2 — Agent 架构设计

## 1. 总览

12 个 Agent，四段式拓扑。**主链路 1–11 固定顺序执行**（9 号 Editor 可回退 6 号 Writer，最多 2 轮）；12 号 FeedbackAnalyst 离线运行（消费数据回流后）。

```
SENSE     SignalScout → TrendAnalyst → AudienceInsight → AngleEditor
PRODUCE   Researcher → Writer → TopicGuard → FactChecker → Editor (revise→Writer, ≤2轮)
AMPLIFY   FormatAdapter → Distributor
EVALUATE  FeedbackAnalyst（离线闭环）
```

## 2. Agent 规格表

| # | Agent | 输入 | 输出 | 关键判断（_decision 示例） | 降级策略 |
|---|-------|------|------|---------------------------|---------|
| 1 | SignalScout | 信号源（KB 近期文档/内置热榜信号） | signals[]（标题/摘要/来源/市场/类目/强度） | "从 42 篇近 7 天文档提取 12 条有效信号" | 规则提取（按类目抽样） |
| 2 | TrendAnalyst | signals[] | trends[]（主题/热度/生命周期/跨市场潜力/代表信号） | "12 信号聚为 4 趋势，AI Agent 热度最高且跨市场" | 按类目频次排序取 topN |
| 3 | AudienceInsight | trends[], MarketProfile | insights[]（需求假设/为什么关心/情绪/风险） | "日本通勤族对'AI 抢工作'焦虑↑，需安抚型角度" | 用市场档案默认兴趣模板 |
| 4 | AngleEditor | insights[], 市场档案, 已发布标题 | brief（选题/角度/钩子/受众/形态建议/不做清单） | "选'AI 助理进入日本职场'，角度=效率而非失业，避开焦虑叙事" | 取热度最高趋势 + 默认角度模板 |
| 5 | Researcher | brief, KB | evidences[]（ev_id/引文/来源/可信度/时间/is_main） | "改写 4 个 query，召回 5 条证据：主干≤3 篇 + 背景≤2 篇" | 放宽过滤直取关键词检索（主干文档 + ≤2 背景文档） |
| 6 | Writer | brief, evidences | article（标题/正文[ev_xxx]/摘要） | "按 deep_dive 写成 900 字，60% 引用来自主干证据" | 2 节主干兜底稿（事件概览 + 为何值得关注） |
| 7 | TopicGuard | article, brief, evidences | article（去漂移版）/ topic_guard（TCS 评分） | "TCS=0.33 拦截，第 [2,3] 节脱离主线 → 定点重写" | 规则删节兜底：摘除漂移节，结构保底留 2 节 |
| 8 | FactChecker | article, evidences | fact_check（论断数/有据率/未证实清单/置信度） | "核查 6 论断，5 有据，1 弱支持" | 引用计数启发式 |
| 9 | Editor | article, fact_check, 市场禁忌 | verdict（pass/revise/reject + Rubric 5 维评分 + 意见） | "质量 4.2/5，无合规命中，pass" | 规则合规扫描 + 默认 pass-with-warning |
| 10 | FormatAdapter | article, 形态清单 | formats{video_script, card, brief_news, comment} | "派生 4 形态，短视频脚本钩子=数据冲击型" | 模板截取（摘要=首段等） |
| 11 | Distributor | article, formats, 市场档案 | plan（平台×形态×受众×时段×理由） | "日本主发 LINE 摘要卡（通勤场景），X 发快讯" | 市场档案默认平台×全形态 |
| 12 | FeedbackAnalyst | contents + events | eval_report（质量/消费指标/问题/迭代建议） | "短视频脚本 CTR 高于图文 22%，建议 JP 市场加大视频权重" | 仅 SQL 统计无 LLM 建议 |

## 3. 关键机制

### 3.1 RunContext 与决策日志
- `RunContext` 随链路流转：task_id / session / llm / market / brief / spans[] / decision_log{}。
- 每个 Agent 返回 `{"_decision": {"reason": ..., "details": {...}}}`，基类自动归集 → 落库 `task.decision_log` + `content.decision_log`。
- 每个 Agent 产出 Span（model/tokens/cost/耗时/状态/警告）→ 落库 `task_spans`。

### 3.2 失败与降级
- `_exec()` 统一 try/except：Agent 异常 → 该 Agent 的 `fallback()`（规则兜底，span 标 degraded），主链路**永不因单点 LLM 失败整体 500**。
- Editor 裁决 `revise` → 携带修改意见回 Writer 重写，最多 `max_review_rounds=2` 轮；`reject` → 记 bad_case 并终止该条。
- LLM 层：指数退避重试（上限 3 次）+ 超时 60s；全部失败抛 AgentError 走 fallback。

### 3.3 Prompt 管理
- 所有 Prompt 存 `prompts/templates/*.md`（不内联代码），支持 `{{var}}` 渲染。
- 内容落库时记录每个环节所用模板名+版本（`prompt_versions`），保证可复盘"这篇内容是哪套 Prompt 生的"。

### 3.4 引用溯源与脱敏（V1 踩坑复用）
- Writer 强制 `[ev_xxx]` 引用；FactChecker 以此核查。
- **任何对外/派生内容（多形态、摘要、卡片）在喂 LLM 前 + 输出后都经 `clean_ev()` 剔除引用标记**，落库前再清洗一次。

### 3.5 为什么 FeedbackAnalyst 在主链路之外
反馈是**离线异步**的（消费数据随时间累积），不属于一次生产 run。它读取 contents+events 产出 eval_report，建议对象是人（运营/PM）而非自动改系统——**人定义标准，AI 提供依据**，这是有意的边界。

### 3.6 主题一致性闸门（TopicGuard，M5 新增）

**问题**：08-06 实测出现"拼盘稿"——机器人主线稿里混入了电竞/百度/中超/歌手等无关新闻。根因是检索层放宽逻辑在 `same_cat >= 3` 边界处 100% 浪费了类目过滤结果，且 Writer 的引用约束**奖励广度**（"引用 ≥2 处不同来源"），制度性地鼓励拼盘。

**四层修复（L0–L3）**，TopicGuard 是 L1 硬闸门：

- **L0 检索层**：`_search` 放宽分支改为"仅在证据不足时回补同类目候选"，不再无条件 `[...][:8]` 覆盖过滤成果；主干文档聚类（主干 ≤3 篇、背景 ≤2 篇），每条证据打 `is_main` 标记。
- **L1 TCS 硬闸门**：`app/rag/tcs.py` 用 `[ev_xxx]` 引用结构算 **Topic Consistency Score**（零 token 成本、与语言无关）：
  - `TCS = 0.6 × 主干引用占比 + 0.4 × (1 − min(跨文档数, 上限)/上限)`
  - 放行条件：`主干引用占比 ≥ 0.6` 且 `跨文档数 ≤ 2` 且**无漂移节**。
  - 漂移节判定：某节"有引用却无一条来自主干"，或其词面相关度（BM25）低于 0.15。
  - 不达标 → **定点重写**仅漂移节（只喂主干证据）→ 复评；仍不达标 → **删节兜底**（摘除漂移节，结构保底留 2 节，诚实记录实际摘除数）。
- **L2 引用约束反转**：Writer 改为"引用的 60% 以上必须来自主干证据"，不再奖励多源广度。
- **L3 fallback 去拼盘**：Writer 降级稿固定 2 节（事件概览 + 为何值得关注），只引用主干文档，不再"每条证据一节"拼装。

指标落库：`content.quality.topic_guard` = TCS 评分（tcs/main_ratio/cross_docs/drift_sections/passed）。详见 [docs/DRIFT_GUARD_DESIGN_v1.0.md](docs/DRIFT_GUARD_DESIGN_v1.0.md)。

## 4. 与 V1 的差异（面试叙事）

| 维度 | V1 | V2 |
|------|----|----|
| 定位 | 内容生产流水线 | 内容供给引擎（Sense-Produce-Amplify-Evaluate） |
| 选题 | TopicSelector 一步搞定 | 拆成 趋势研判→需求洞察→角度设计 三步，判断显性化 |
| 市场理解 | 国家策略是静态 config | MarketProfile 是一等公民数据模型，进 Prompt、进审核 |
| 形态 | 图文为主，短视频外挂 | FormatAdapter 一等公民，一次生产 4 形态 |
| 评估 | A/B 实验 + eval_score | Rubric 5 维评分 + 消费指标 + FeedbackAnalyst 迭代建议 |
| 工程 | ChromaDB 40MB 镜像、prompt 内联、单文件路由 | BM25 零依赖检索、模板文件化、路由分域 |
