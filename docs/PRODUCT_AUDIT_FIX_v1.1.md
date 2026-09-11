# TrendForge V2 审计整改记录 v1.1

> 对应审计：`docs/PRODUCT_AUDIT_v1.0.md`（2026-09-12 全站审计）
> 整改日期：2026-09-12（深夜一轮做完）
> 原则：**一条不落地执行审计清单，但不用编数据的方式让指标好看。**

---

## 0. 一句话总结

19 项清单里 **17 项已落地**（含全部 P0/P1），2 项经判断后**刻意不做**并说明理由（见 §5）。
最大的三处变化：

1. **迭代闭环 → 系统进化**：补上了闭环缺的第四段「效果回收」，采纳之后到底变好了没有，现在有真实数据回答；
2. **全站标签层**：11 处内部标识泄漏（brief_news / video_script / signal_scout / ab …）一次性收敛；
3. **Demo 数据重做**：快照补进 7 个市场档案 + 5 条真人校准分，第一屏新增「机器裁决 vs 人工打分」真实对照——
   不再靠粉饰数字讲好故事，而是把差值本身讲成产品存在的理由。

---

## 1. P0 逐项

| # | 审计项 | 做了什么 | 位置 |
|---|---|---|---|
| 1 | 重建 demo_snapshot.db | 新增 `human_calibrations` 表 + 5 条真人打分（剔除全 1 分无理由的退化测试行）、回填 `contents.human_score_avg`、补齐 GB/IN 两个市场档案；**不同步**本地那 3 条兜底垃圾内容 | `tools/rebuild_snapshot.py`、`src/app/data/demo_snapshot.db`、`docs/data/SNAPSHOT_REBUILD_LOG_v1.1.md` |
| 2 | 全局 LABEL 翻译层 | 新增 `LABEL` / `lb()` / `lbEnum()` / `kindLabel()` / `sourceLabel()` / `modelLabel()` / `stageLabel()`，覆盖形态、Agent、Rubric 五维、类目、事件阶段、模板名、任务类型；图表渲染函数与表格全部过一遍 | `ui/console/assets/app.js` |
| 3 | 仿真/合成口径统一 | 评估中心加「本页全部消费指标为仿真口径」横幅 + 4 个 KPI 各带仿真角标；知识库页加「合成 KB」角标与说明；分析中心保留每图角标 | `app.js` `evalView` / `kbView` |
| 4 | 内容筛选语义 | 筛选从 `status` 改为 **按质量裁决**（可发布/需修改/不通过），列表每行显示裁决标签 | `app.js` `contents()` / `paintContentsTable` / `contentsTable` |
| 5 | 报告建议去标记 | 建议文案由 `[writer/结构要求] …` 改为「建议调整「写作」· 结构要求：…」 | `src/app/agents/feedback_analyst.py`（新增 `TEMPLATE_CN` + `readable_suggestions()`） |

**为什么没有把本地那 3 条 "pass" 内容同步进快照**：它们是未配置 LLM 时的兜底产物，
标题是「ai 领域热点（Stratagems #23…）」这种中文模板串，却挂在英国/印度（英文）市场下。
同步上去能把 QSR 从 0 变成非 0，但会换来一个更刺眼的破绽——**不划算，也不诚实**。

---

## 2. P1 逐项

| # | 审计项 | 做了什么 |
|---|---|---|
| 6 | 迭代闭环重构 | ① 改名「系统进化」+ 一句人话副标题（"这里决定 AI 下一轮怎么写，所有改动都由你确认，可随时回滚"）；② 新增 ③ 采纳效果回收（见下）；③ A/B 去掉机器判胜负，改人二选一并可直接生效；④ 删除评估中心重复的 FeedbackAnalyst 入口（全站只留一个）；⑤ 模板名/版本号/来源全部中文化（`写作`、`第 3 版`、`AI 提议`） |
| 7 | 内容详情默认双语 | `ZH.mode` 默认由「仅中文」改为「双语对照」，英文母稿立刻可见、中文异步补上；7 个 Tab 分主次（母稿/多形态/分发计划/简报/信号源｜证据：质量/Trace） |
| 8 | 命名统一 + 演示路径 | 首页 Agent 名与控制台彻底对齐（14 个：主管线 11 + 治理分析 3）；首页新增「3 分钟看懂这个 Demo」五站引导 + 8 份设计文档入口 + 诚实标注声明 |
| 9 | 校准页预置报告 | 快照带真人打分后，校准页**默认铺开**已生成的对齐报告（不再藏在按钮后面）；实测 5 条已校准、报告接口正常返回 |

### 采纳效果回收（本次最有价值的新增）

- 新接口 `GET /api/prompts/adoption-impact`：`src/app/api/routers/prompts.py`
- 口径：按运行记录里**实际使用的 Prompt 版本**分组（`tasks.prompt_versions`），
  对比「用旧版跑出的内容」与「用采纳版跑出的内容」的质量均分 / 单条成本 / 耗时 / 裁决分布；
  全部取自已落库的真实数据，**样本数如实标注**，样本 <4 时前端主动降调为「这是信号不是结论」。
- 实测（快照真实数据）：写作 Prompt 第 3 版（AI 提议、已采纳）
  → 内容数 11 条 → 3 条，质量 3.44 → 3.53，单条成本 ¥0.4137 → ¥0.3443，耗时 658s → 443s。

### A/B 判据的修正

`simulator.py` 里 CTR 由 quality 派生并叠加 ±20% 噪声，再拿 CTR 反证 Prompt 对 quality 的改善是循环论证。
现在：`run_ab()` 返回值新增 `note` 字段主动标注该局限，前端把 CTR 降为灰色参考、并写明「不作判优依据」，
两版各给一个「选用这版并生效」按钮——**由人拍板**，选完走 `PromptManager` 覆盖层即刻生效。

---

## 3. P2 逐项

| # | 审计项 | 做了什么 |
|---|---|---|
| 10 | 分析中心分组 + SQL 折叠 | 9 张图按「供给效率 / 内容质量 / 成本与稳定性 / 消费表现」四组重排并各带口径角标；SQL 折叠标题改为「技术细节 · 驱动此图的 SQL」（原本默认就是折叠的，保留可展开） |
| 11 | 跑供给体验 | 加时长与成本预估说明（本地 1–3 分钟 / ¥0.3–0.6，线上因冷启动更慢）；Tab 分主次见 P1-7 |
| 12 | 死端点接线 | `GET /api/bad-cases` 原本零消费者，现在「跑供给」页新增「失败案例库（BadCase）」面板，展示根因与自愈动作 |

另外顺手修掉一处漏网：`queries.py` 里阅读时长图的说明文字仍写 `video_script≈45s`，已改为「短视频脚本≈45s」。

---

## 4. 验证方式（不是"应该能跑"，是真的跑过）

1. **后端真实起服务**：`uvicorn app.api.main:app`，DB 指向快照副本，逐条验证
   `/markets`（7 个）、`/contents`（verdict/quality_avg 字段齐全）、`/calibration/samples`（5 条已评）、
   `/prompts/adoption-impact`（真实聚合值）、`/calibration/report`（报告可生成）、`/bad-cases`。
2. **前端渲染冒烟**：用 Node + 极简 DOM 桩 + 真实接口响应回放，把 9 个视图 + 内容详情 + 2 个子面板全部渲染一遍，
   另加 18 条「整改点可见性」断言（含"确认已移除 signal_scout / video_script / target_template"这类反向断言）。
   **结果：42 项检查全绿。** 脚本：`D:/tmp/tf_smoke_render.js`（临时工具，验证用）。
3. `node --check` 通过；相关 Python 文件 `py_compile` 通过。

---

## 5. 刻意没做的两件事（以及理由）

| 项 | 理由 |
|---|---|
| **不把 QSR / 通过率"修"成好看的数字** | 唯一诚实的办法是跑真实 LLM 产出合格内容，本地没有 key。改分、改裁决等于伪造证据——这个项目卖的就是"诚实标注"，自相矛盾代价太大。改为在仪表盘把「机器 3.3 分 / 人 3.9 分」的差值**直接摆在第一屏**，并说明这正是人工校准模块存在的理由。 |
| **不加「批量跑全部市场」** | 免费实例重启会丢数据（运行中的任务会变失联、DB 回退种子），批量跑会放大这个风险，与既有运维结论冲突。改为在跑供给页明确预估耗时/成本，让用户有预期。 |

---

## 6. 值得肯定的部分：保留清单（重构中确认未丢）

- ✅ 分析中心每张图可展开的**真实 SQL**（含窗口函数/CTE）—— 仅把折叠标题改为「技术细节」，内容一字未动。
- ✅ 人审闸门范式：知识库补丁逐项审批、Prompt 建议采纳闸门，均原样保留，且系统进化页把它讲得更清楚了。
- ✅ 信号溯源（真实抓取 + 可点原文 + 真实互动数）保持不变。
- ✅ `RunState` / `ReviseState` 全局任务态（切页面刷新不中断）未动。
- ✅ 诚实标注哲学不只保留，而是扩到全站：评估中心横幅、知识库合成 KB 角标、A/B 局限自陈、效果回收样本量降调。

---

## 7. 改动文件清单

**前端**
- `ui/console/assets/app.js`（标签层、系统进化页、评估中心、内容筛选/详情、分析中心分组、校准页、仪表盘人机卡）
- `ui/console/assets/api.js`（新增 `promptAdoptionImpact` / `badCases`）
- `ui/console/assets/styles.css`（新增仿真/合成标注、证据 Tab、效果回收卡等样式）
- `ui/console/index.html`（侧边栏改名、静态资源版本号 v2.2）
- `ui/index.html`（Agent 命名统一 + 3 分钟演示路径 + 文档入口 + 诚实声明）

**后端**
- `src/app/api/routers/prompts.py`（新增 `/prompts/adoption-impact`）
- `src/app/agents/feedback_analyst.py`（建议文案中文化）
- `src/app/workflow/ab.py`（A/B 返回值自陈局限 `note`）
- `src/app/analytics/queries.py`（说明文字去内部标识）

**数据 / 工具 / 文档**
- `src/app/data/demo_snapshot.db`（重建）
- `tools/rebuild_snapshot.py`（新增，可重跑）
- `docs/data/SNAPSHOT_REBUILD_LOG_v1.1.md`、`docs/PRODUCT_AUDIT_FIX_v1.1.md`（本文件）
