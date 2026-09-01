# TrendForge V2 — 数据模型与接口设计

## 1. ER 总览

```
markets (5 个预置市场档案)
signals → trends（流水线中间产物，随 run 存 task.output）
documents 1───* chunks                 （RAG 知识库）
tasks 1───* task_spans                 （运行 + Trace/决策日志）
tasks 1───* contents 1───* content_events
contents 1───* eval_reports
bad_cases
pipeline_cache                         （Demo 稳定性缓存）
prompts（模板版本登记）
```

## 2. 表结构（SQLAlchemy 2.0 / SQLite）

### markets — 市场档案（V2 新增核心）
| 字段 | 类型 | 说明 |
|------|------|------|
| code | str PK | US/JP/KR/BR/CN |
| name / language / timezone | str | |
| media_landscape | json | 主流媒体与平台生态描述 |
| culture_notes | json | 文化语境与内容禁忌（Editor 审核用） |
| interests | json | 兴趣画像（类目→权重） |
| platforms | json | 平台偏好（平台→适合形态/受众/活跃时段） |
| tone / default_style | str | 调性 / 默认内容风格 |

### documents / chunks — 知识库
documents: id, title, source, url, category, country, language, credibility(1-3), published_at, hash(去重)
chunks: id, doc_id FK, idx, text, section, meta(json)

### tasks / task_spans — 运行与可解释性
tasks: id, kind(pipeline), market, status(queued/running/done/failed), input(json), output(json), decision_log(json), prompt_versions(json), total_duration_ms, total_cost_cny, review_rounds, error, created_at, finished_at
task_spans: id, task_id FK, agent, status(ok/degraded/failed), model, tokens_in/out, cost_cny, duration_ms, warnings(json), decision_reason, started_at

### contents — 供给产物（一条 run 一条母稿）
| 字段 | 说明 |
|------|------|
| id / task_id FK / market / language / status(published/retracted) | |
| brief(json) | AngleEditor 的选题简报（角度/钩子/受众/需求假设/形态建议） |
| title / summary / body(json: sections[]，含 ev 引用) | 母稿 |
| evidences(json) | 证据集（ev_id→引文/来源/可信度） |
| formats(json) | 多形态派生 {video_script, card, brief_news, comment} |
| distribution(json) | 分发计划 |
| quality(json) | Editor Rubric 5 维评分 + verdict + fact_check 摘要 |
| decision_log(json) / prompt_versions(json) | 可解释性 |
| is_fallback / created_at | |

### content_events — 消费事件（模拟回流）
id, content_id FK, event_type(exposed/clicked/finished/liked/shared/negative/completed_video), market, platform, format(article/video_script/card/...), ts

### eval_reports — 评估报告（FeedbackAnalyst 产出）
id, content_id FK(null=全局), quality_avg, metrics(json: ctr/finish_rate/engagement/neg_rate), findings(json), suggestions(json), created_at

### bad_cases
id, content_id, category(F事实/H合规/C文化/Q质量), root_cause, fix_action, status, created_at

### pipeline_cache — Demo 缓存
key PK（请求签名 sha256）, response(json), created_at

### prompts — 模板版本登记
name+version 联合唯一, content, status, created_at

## 3. API 设计（/api 前缀）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查（含 LLM 配置状态） |
| GET | /markets | 市场档案列表 |
| POST | /pipeline/run | 发起供给 run {market, force} → {job_id}（异步） |
| GET | /pipeline/jobs/{id} | Job 状态轮询（含进度：当前 agent） |
| GET | /contents?market=&limit= | 内容列表 |
| GET | /contents/{id} | 内容详情（母稿+形态+分发+质量） |
| GET | /contents/{id}/trace | Trace（spans + decision_log） |
| POST | /contents/{id}/revise | 对已发布内容发起人工修订（Produce 子链重跑） |
| POST | /contents/{id}/zh | 生成中文回译镜像 |
| GET | /contents/jobs/{id} | 内容级异步任务（revise/zh）状态 |
| GET | /tasks | 运行历史 |
| POST | /events/simulate | 模拟消费事件（反馈闭环演示） |
| GET | /analytics/center | 分析中心 8 图（QSR/漏斗/FPY/降级/成本/Rubric/衰减/形态×市场） |
| GET | /analytics/calibration | 仿真器校准元信息 |
| GET | /analytics/overview | 漏斗/CTR/完播/按市场拆分 |
| GET | /analytics/reports | eval_reports 列表 |
| POST | /analytics/run-feedback | 触发 FeedbackAnalyst |
| GET | /prompts | Prompt 模板列表（兼容旧入口） |
| GET/POST | /prompts/templates · /prompts/versions | 模板与版本列表 / 手工登记新版本 |
| POST | /prompts/versions/{id}/adopt · /prompts/versions/{a}/diff/{b} | 版本采纳（运行时覆盖层生效）/ 版本 diff |
| GET/POST | /prompts/suggestions | FeedbackAnalyst 迭代建议列表 /（建议落库由 run-feedback 触发） |
| POST | /prompts/suggestions/{id}/adopt · /reject | 人审闸门：采纳（生成新版本）/ 驳回 |
| POST | /prompts/ab/run | 同选题双版 Prompt A/B 对比 |
| POST | /prompts/feedback | 触发 FeedbackAnalyst（Prompt 域入口） |
| GET | /kb/stats · /kb/search?q= · /kb/freshness | 知识库统计 / 检索演示 / 新鲜度 |
| POST | /kb/curate | 触发 KBCurator 覆盖度/过期扫描 |
| GET/POST | /kb/patches · /kb/patches/{id}/approve · /reject | KB 待审补丁 + 人审闸门 |
| GET | /calibration/samples | 待校准内容（隐藏评委分） |
| POST | /calibration/scores | 提交真人五维打分（落库 + 实时对齐） |
| GET | /calibration/report | 真人 vs 评委对齐报告（markdown + SVG） |
| GET | /bad-cases | Bad case 列表 |

## 4. LLM 配置

- 厂商抽象保留（OpenAI 兼容协议），默认 **DeepSeek**：`base_url=https://api.deepseek.com`，模型 `deepseek-v4-flash`（env: `DEEPSEEK_API_KEY` / `TF_LLM_MODEL`）。
- 指数退避重试 3 次 + JSON 容错解析（extract_json）+ 成本统计（pricing 表可配）。
- pipeline_cache：run 请求归一化签名 → 命中秒回；`force=true` 刷新。

## 5. 检索方案：BM25（对 V1 的工程修正）

- V1 用 ChromaDB + MiniLM：镜像 40MB 数据 + 编译依赖，冷启动慢。
- V2 用**纯 Python BM25**（手写 <100 行，jieba 分词可选降级为字符 bigram）：零重依赖、Docker 秒构建、检索质量对 demo 规模（~50 文档/数百 chunk）足够。
- 接口抽象 `Retriever`：未来可换向量后端，业务代码无感知。这是"够用就好"的有意取舍，写进 README。
