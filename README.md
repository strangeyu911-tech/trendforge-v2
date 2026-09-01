# TrendForge V2 — AI Native 全球化内容供给引擎

> 把内容供给的关键流程交给 AI：信号捕捉、趋势研判、需求洞察、角度设计、素材寻找、生成、核查、审核、形态派生、分发策略。
> **人负责定义标准（市场档案 / Prompt 模板 / 评估 Rubric），AI 负责执行与进化。**

一个个人作品集项目：把全球化资讯内容的供给工作流抽象为可运行的 AI Agent 系统。

## 为什么有 V2

V1 证明了"AI 能走完内容生产链路"（8-Agent 流水线 + RAG + Prompt Center）。
V2 回答的是更进一步的问题：**AI 能不能像一支内容团队一样，理解市场、做出判断、产出多形态内容、并在反馈中进化？**

| 维度 | V1 | V2 |
|------|----|----|
| 定位 | 内容生产流水线 | 内容供给引擎（Sense-Produce-Amplify-Evaluate） |
| 选题 | 一步选题 | 趋势研判 → 需求洞察 → 角度设计，判断显性化 |
| 市场理解 | 静态 config | MarketProfile 一等公民：文化语境/禁忌/平台生态进 Prompt、进审核 |
| 内容形态 | 图文为主 | FormatAdapter：一次生产 → 短视频脚本/摘要卡/快讯/评论引导 |
| 评估 | A/B 实验 | Rubric 5 维评分 + 消费指标 + FeedbackAnalyst 迭代建议 |
| 工程 | ChromaDB 40MB 镜像、prompt 内联 | 纯 Python BM25 零重依赖、模板文件化、路由分域 |

## 系统架构：四段式供给引擎

```
SENSE 感知      SignalScout → TrendAnalyst → AudienceInsight → AngleEditor
PRODUCE 生产    Researcher → Writer → TopicGuard → FactChecker → Editor（revise 回退 ≤2 轮）
AMPLIFY 放大    FormatAdapter → Distributor
EVALUATE 进化   FeedbackAnalyst（离线：消费数据 → 评估报告 → 迭代建议）
```

- **拓扑固定，行为可配**：链路顺序是领域最佳实践，不做可拖拽 DAG；可配的是 Prompt 模板、市场档案、Rubric。这是有意的工程判断，不是偷懒。
- **判断显性化**：每个 Agent 产出 `_decision`（为什么这么做），全链路决策日志 + 每步 span（token/耗时/状态）在 Trace 页可复盘。
- **事实可溯源**：LLM 不联网，知识库是唯一事实来源；Writer 强制 `[ev_xxx]` 引用，FactChecker 独立核查。
- **人机分工**：FeedbackAnalyst 只产出建议给人，不自动改系统——人定义标准，AI 提供依据。

## 快速开始

```bash
cd src
pip install -r requirements.txt
export DEEPSEEK_API_KEY=<your key>   # 不配置也能跑（全链路规则兜底降级）

python main.py seed          # 初始化：5 市场档案 + 28 篇 KB + 12 个 Prompt 模板
python main.py serve         # http://localhost:8000/docs
python main.py run JP        # 端到端跑一次日本市场供给
python main.py simulate      # 模拟消费事件（反馈闭环演示）
```

前端控制台：`ui/`（静态，默认连线上 API；本地开发把 `ui/console/assets/api.js` 的 API_BASE 指向 localhost:8000）。

## 关键设计文档

- [docs/PRD.md](docs/PRD.md) — 产品定位、四段式架构、功能范围、设计原则
- [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md) — 12 个 Agent 的输入/输出/判断/降级规格
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — 数据模型、API、检索方案取舍

## PM 设计文档（M4 · `v2.5-pm-docs`）

> 从「AI Native 内容供给引擎 PM」视角补齐的四份交付物——用数据与矩阵回答"为什么是这套设计、怎么衡量、怎么规模化、真跑过吗"。

- [docs/METRICS_FRAMEWORK_v1.0.md](docs/METRICS_FRAMEWORK_v1.0.md) — 供给引擎指标体系：北极星（有效内容供给率 QSR）+ 四层分解（供给效率/内容质量/消费表现/成本效率），每指标含口径与可执行 SQL
- [docs/CONTENT_FORMAT_DESIGN_v1.0.md](docs/CONTENT_FORMAT_DESIGN_v1.0.md) — 内容形态设计：为什么是 article + 4 种派生形态、各形态消费场景/信息密度/钩子位置、形态 × 市场档案交叉矩阵
- [docs/SCALE_DESIGN_v1.0.md](docs/SCALE_DESIGN_v1.0.md) — 规模化设计：日产 10 万条的架构演进（队列/分片/缓存/成本模型/审核抽样率）+ 已评估但未做清单
- [docs/RUN_REPORT_v1.1.md](docs/RUN_REPORT_v1.1.md) — **真实运行报告（定稿版）**：M1 真实信号驱动、17 条真实运行，交出产出率 70.6% / FPY 0% / 单位有效成本 ¥0.50（含废稿摊销）/ 失败归因 / Rubric 五维，并记录一次扩样失败暴露的环境可靠性问题
  - 原始证据：[docs/data/RUN_EVIDENCE_v1.json](docs/data/RUN_EVIDENCE_v1.json)（任务/评分/信号/否决原文，可复现）
  - 历史版本：[docs/RUN_REPORT_v1.0.md](docs/RUN_REPORT_v1.0.md)
- [docs/CLOSED_LOOP_EVIDENCE_v1.0.md](docs/CLOSED_LOOP_EVIDENCE_v1.0.md) — **M3 闭环实跑证据（PM 向）**：2026-08-07 真实 LLM 跑通「FeedbackAnalyst→人审采纳→A/B」，写入覆盖层无需重启；同选题双跑质量 +0.4 / CTR +0.01 / 成本 −¥0.0681。原始证据 [docs/data/RUN_EVIDENCE_m3_closed_loop_2026-08-07.json](docs/data/RUN_EVIDENCE_m3_closed_loop_2026-08-07.json)
- [docs/CLOSED_LOOP_LIVE_EVIDENCE_v1.0.md](docs/CLOSED_LOOP_LIVE_EVIDENCE_v1.0.md) — **采纳版 Prompt 端到端实跑（v2.6-closed-loop-live）**：writer@v3 采纳后真实产出新内容，多轮 `revise→人审打回→重写` 把守门前漂移率从 0.2 单调降到 0.0、守门后恒为 0；自动演示 [docs/data/produced/DEMO_end2end_newcontent.html](docs/data/produced/DEMO_end2end_newcontent.html)，证据 [docs/data/produced/RUN_EVIDENCE_newcontent_2026-08-07.json](docs/data/produced/RUN_EVIDENCE_newcontent_2026-08-07.json)。注意：该次运行的选题与证据来自合成种子 KB（事实为演示用途虚构），验证对象是生产与治理链路本身

## 分析中心（M2 · `v2.3-analytics`）

> 把指标体系从「文档里的 SQL」变成「控制台里可点的图表」。所有分析用**手写 SQL（不用 ORM）**，每张图可展开真实 SQL 原文；真实数据（供给/质量/成本）与仿真数据（消费表现）在 UI 严格区分、仿真图统一打「仿真」角标。仿真器由 M1 真实信号互动分布校准（`compute_calibration`），事件按内容生命周期指数衰减摊开。

- 后端：`src/app/analytics/queries.py`（8 图 SQL，5/8 用窗口函数/多级 CTE）+ `src/app/api/routers/analytics.py`（`GET /center`、`GET /calibration`）
- 前端：控制台「分析中心」视图（`ui/console/`，纯 SVG 无第三方库）
- 校准：仿真器参数由 `contents.signals`（HN/Dev.to points/comments）拟合，详见 `src/app/simulator.py`

## 可执行闭环与 A/B（M3 · `v2.4-closed-loop`）

> 闭环只到「建议」为止是假闭环。M3 把「AI 提议」接上「人审闸门 + 一键采纳 + 可回滚 + A/B 验证」，**人始终是标准的定义者，但决策成本从「自己重写 Prompt」降到「点一下采纳」**。该模式先在 KB 治理（KBCurator）验证，直接复用到 Prompt 上，叙事自洽。

- 数据层：`PromptRecord` 扩展 `source`(file/human/ai_suggested) / `adopted` / `parent_version` / `adopted_at`；新增 `PromptSuggestion` 表（AI 建议 + 完整新版 Prompt，待人审）
- 运行时覆盖层：`PromptManager` 增加 in-memory override，DB 中 `adopted=True` 的版本自动覆盖文件模板生效；`seed_all` 启动时同步 → **采纳后下一轮运行即用新版本，无需重启**
- `FeedbackAnalyst` 建议结构化：`{target_template, section, proposed_change, rationale, expected_metric, new_prompt}`，LLM 直接产出可被一键采纳的完整新版 Prompt
- 人审闸门 API（`src/app/api/routers/prompts.py`）：`POST /prompts/suggestions/{id}/adopt` 采纳生成新版本、`/versions/{id}/adopt` 回滚、版本 `diff`
- 极简 A/B（`src/app/workflow/ab.py`）：同一选题用两版 Prompt 各跑一次 produce 段 → 仿真 → 对比质量分 / CTR / 成本；顺带交付「同选题多版本改写」
- 前端：控制台「迭代闭环」视图（提议 → 人审 → 采纳 → 回滚 → A/B，复用 KBCurator 人审闸门 UI）
- 实跑证据：[docs/CLOSED_LOOP_EVIDENCE_v1.0.md](docs/CLOSED_LOOP_EVIDENCE_v1.0.md) + 原始 JSON [docs/data/RUN_EVIDENCE_m3_closed_loop_2026-08-07.json](docs/data/RUN_EVIDENCE_m3_closed_loop_2026-08-07.json)（2026-08-07 真实 LLM 跑通全链路，采纳后覆盖层生效无需重启）

## 主题漂移防护（M5 · `v2.6`）

> 08-06 实测出现"拼盘稿"（主线被电竞/百度/中超/歌手等无关新闻污染）。M5 用四层防护彻底解决：L0 检索层修复放宽逻辑 → L1 新增 TopicGuard 硬闸门（TCS 主题一致性分，零 token、语言无关）→ L2 引用约束反转（60% 以上引用须来自主干）→ L3 fallback 去拼盘（兜底稿固定 2 节只引主干）。

- [docs/DRIFT_GUARD_DESIGN_v1.0.md](docs/DRIFT_GUARD_DESIGN_v1.0.md) — 主题漂移根因分析 + L0–L3 四层防护设计 + TCS 公式 + 已知局限
- [docs/METRICS_FRAMEWORK_v1.0.md](docs/METRICS_FRAMEWORK_v1.0.md) §2.2.1 — 主题一致性（TCS）闸门指标口径与 SQL
- 回归测试：[tools/test_drift_guard.py](tools/test_drift_guard.py)（22/22 通过，覆盖 L0–L3 四层与兜底稿中/英双语分支）

## 技术栈与取舍

| 层 | 选型 | 为什么 |
|----|------|--------|
| API | FastAPI + SQLAlchemy async + SQLite | 单进程可跑，Demo 零运维 |
| 检索 | 纯 Python BM25（<100 行） | V1 的 ChromaDB+MiniLM 让镜像 40MB、编译重；Demo 规模 BM25 足够，接口可插拔 |
| LLM | DeepSeek（OpenAI 兼容抽象） | 推理模型输出长，默认 max_tokens 8000 + JSON 截断修复 |
| 可靠性 | 异步 Job + 结果缓存 + 逐级降级 | 任何 Agent 失败走规则兜底，主链路永不裸 500；缓存命中秒开零额度 |

## 项目结构

```
v2_trendforge/
├── docs/                  # PRD / Agent 设计 / 数据模型
├── src/
│   ├── app/
│   │   ├── agents/        # 12 个 Agent + base（RunContext/决策日志/降级/ev清洗）
│   │   ├── workflow/      # 固定拓扑编排器（Editor 回退循环）
│   │   ├── rag/           # BM25 + KB 灌库 + 检索
│   │   ├── prompts/       # 模板文件化（templates/*.md）
│   │   ├── api/           # FastAPI 分域路由
│   │   ├── data/          # 市场档案 markets.json + KB 28 篇
│   │   └── simulator.py   # 消费事件模拟（反馈闭环）
│   ├── main.py            # seed / serve / run / simulate
│   └── Dockerfile
├── ui/                    # 门户 + 运营控制台（原生 JS，MVP 版）
└── render.yaml            # 后端 Docker + 前端静态站

