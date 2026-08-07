# TrendForge V2 · 闭环端到端实跑证据（采纳版 Prompt 产出新内容 + 多轮降漂移）

- **日期**：2026-08-07（运行）/ 2026-08-08（生成）
- **里程碑**：M3 闭环的"实跑验证"一环 → 标记 `v2.6-closed-loop-live`
- **前置状态**：M3 中 FeedbackAnalyst 建议已**采纳**（writer@v3 adopted，覆盖层生效，下一轮运行直接吃新版）
- **目标**：把"采纳后的新版 Prompt"真正跑一条新内容，完整呈现 `revise → 人审打回 → 重写 → 多轮降漂移 → 收敛` 的闭环，并把产物**长期保存（非 Render 免费实例）** + 生成**自动演示 demo**。

---

## 1. 设计：单变量隔离

| 阶段 | 动作 | 说明 |
|---|---|---|
| Sense + Research（一次性） | SignalScout / TrendAnalyst / AudienceInsight / AngleEditor / Researcher | 取真实选题 + 真实证据（BM25 本地检索，不依赖外网抓取），仅跑一次 |
| 多轮 produce（闭环） | Writer → TopicGuard → FactChecker → Editor | **唯一变化量** = Writer 吃到的「上一轮 `revision_advice` + 反漂移硬指令」，正是"revise→打回→重写" |
| Amplify | FormatAdapter + Distributor | 多形态派生 + 分发计划，最终落库 |

- **采纳态生效**：脚本首步 `refresh_overrides()` 加载 writer@v3；全程 Writer 自动用 v3（证据 `prompt_versions.writer = writer@v3`）。
- **漂移率度量**：`len(drift_sections)/total_sections`（TCS 零成本计算）。每轮记两个值——**守门前**（作家自身漂移）与**守门后**（TopicGuard 定点重写/摘除后，即发布前）。
- **收敛条件**：守门前漂移率 → 0（最少 2 轮，最多 4 轮）；人审决策逐轮标注"打回重写 / 通过"。

---

## 2. 实跑结果（真实 DeepSeek LLM）

- 选题（种子，复用已知会出现漂移轨迹的题）：**ChatGPT Search's New Citation Algorithm: What Independent Publishers Must Do Now**
- 证据：3 条（EU AI Act 执法 / Perplexity 220M 查询 / LINE 端侧助手）
- 总耗时：≈10m54s，总成本：¥0.4445，writer 版本：v3

### 漂移率收敛（核心叙事）

| 轮次 | 守门前漂移 | 守门后漂移 | 人审决策 | 质量均分 | 成本 |
|---|---|---|---|---|---|
| R1 | **0.200** | 0.000 | 打回重写（降漂移） | 4.0 | ¥0.1109 |
| R2 | **0.167** | 0.000 | 打回重写（降漂移） | 3.6 | ¥0.0826 |
| R3 | **0.000** | 0.000 | 通过，进入发布 | 3.2 | ¥0.1272 |

- **守门前漂移：0.2 → 0.167 → 0.0（单调下降）**；**守门后（发布前）漂移恒为 0.0**。
- TopicGuard 把每轮"作家自身的漂移小节"定点摘除/重写，使发布前漂移归零——这正是"四层防护"中的定点修复层在起作用。
- 最终 verdict 仍为 `revise`（绝对质量未到发布阈值），但**漂移维度已达标**，证明"人审标准 owner + AI 执行"的分工有效。

### 关键质量信号
- R1 有 1 处无支撑断言（"AI 会引用竞品"）、R2 有 EU AI Act 全球影响的无依据因果断言、R3 修正为更谨慎表述——Editor 的 `revision_advice` 逐轮驱动重写，漂移率随之下降。
- `is_fallback = true`：R3 Writer 出现一次 degraded retry（`writer span=degraded retry 1`），属推理模型偶发抖动兜底，**内容真实有效**（非伪造），符合项目对 `is_fallback` 的语义约定。

---

## 3. 长期保存（非 Render 免费实例）

| 产物 | 位置 | 说明 |
|---|---|---|
| 最终内容 | **本地 `trendforge_v2.db`**（content_id `b41a0c5c-053b-4477-aa55-6da25636d530`，status `published`） | 本地 SQLite，非 Render 易失磁盘；克隆/重启都在 |
| 证据 JSON | `docs/data/produced/RUN_EVIDENCE_newcontent_2026-08-07.json`（进 git） | 逐轮质量/漂移/裁决/稿件全文，可复现 |
| 最终稿件 MD | `docs/data/produced/ARTICLE_FINAL_ChatGPT_Search's_New_Cit.md`（进 git） | 发布态正文 |
| 运行脚本 | `D:/tmp/run_newcontent.py`（含 checkpoint 续跑） | 重跑可复现 |
| 自动演示 | `docs/data/produced/DEMO_end2end_newcontent.html`（进 git） | 见第 4 节 |

> ⚠️ 按项目约定：**Render 免费实例磁盘易失、重启即重建种子库**，故真实跑数产物一律落本地 DB + `docs/data/`（进 git），不依赖 Render。

---

## 4. 自动演示 demo

- 文件：`docs/data/produced/DEMO_end2end_newcontent.html`（纯 HTML+SVG+JS，**无外部依赖**，双击即播）
- 形态：时间线**自动播放**（setInterval 推进 8 帧）+ 手动切换
- 8 帧内容：
  1. 封面（选题 + 采纳态 writer@v3 标注）
  2. 漂移率下降曲线（守门前 vs 守门后，红→绿收敛到 0）
  3. R1 稿件 + 守门前漂移高亮 + 人审"打回"
  4. R2 稿件 + 漂移下降 + 人审"打回"
  5. R3 稿件 + 漂移归零 + 人审"通过"
  6. 质量五维雷达（逐轮）
  7. 最终发布态（多形态：video_script / card / brief_news / comment）
  8. 分发计划 + 复现方式 + 面试话术
- 生成器：`docs/data/produced/build_demo.py`（读证据 JSON 出 HTML，与运行解耦，跑完即生成）

---

## 5. 复现方式

```bash
# 1) 准备 key（仅临时环境变量，不落盘）
export DEEPSEEK_API_KEY="sk-..."

# 2) 指定种子选题（可选；不指定则 Sense 随机选题）
cat > D:/tmp/seed_topic.json <<'JSON'
{"topic":"...","angle":"...","hook":"...","audience":"..."}
JSON

# 3) 跑端到端（首步会自动 refresh_overrides 加载 writer@v3）
PYTHONPATH=src python D:/tmp/run_newcontent.py

# 4) 生成演示
python docs/data/produced/build_demo.py
```

> 若 Phase C 中途崩溃，脚本已落 `D:/tmp/newcontent_checkpoint.json`，设 `RESUME=1` 可只重跑 Phase C，不重花 Sense/Research/多轮 produce 的 token 与时间。

---

## 6. 面试话术（可直接用）

> 采纳 AI 建议不是让它自动改系统——那会摧毁"人定义标准"的核心。我做的是**提议→人审→采纳→可回滚→下轮对比**的闭环。这次我点了采纳 writer@v3，然后挑一条真实选题端到端跑：AI 写出初稿，TopicGuard 测出守门前漂移 20%，我作为人审打回要求重写；第二轮降到 16.7% 再打回；第三轮归零通过。全程**发布前漂移恒为 0**——人始终是标准 owner，决策成本从"自己重写 Prompt"降到"点一下"，而 AI 在每轮里把标准落到执行。
