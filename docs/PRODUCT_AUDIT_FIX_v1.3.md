# TrendForge V2 审计整改记录 v1.3 —— 全站文案中文化

> 触发：用户复查分析中心时指出——除了 Agent 名、KBCurator 这类专有名词和 SQL 函数名，
> 页面上不该再出现英文；`prompt` 应写作「提示词」。
> 上一轮（v1.2）只收敛了「枚举值」，这一轮把**混在中文句子里的英文标识**扫干净。
> 整改日期：2026-09-12　｜　回归：`tools/smoke_render.js` 66 项全绿

---

## 0. 一句话总结

上一轮修的是「枚举直接画到图上」（`video_script` / `signal_scout`），这一轮修的是
**「中文句子里夹的英文词」**——它更隐蔽，因为单看每一处都像术语，但连起来读就是给机器看的：

> 改前：各 Agent 的 task_spans 中 degraded/failed 占比，按 RANK() 窗口函数标出最脆弱环节。降级率升 → 触发 KBCurator 补库或调 Prompt。
> 改后：统计各 Agent 的执行记录里「降级 / 失败」所占比例，再用 RANK() 窗口函数排出最脆弱的环节。降级率上升 → 触发知识库补库，或调整该环节的提示词。

共修 **4 层、约 260 处**：分析中心图表文案 → 后端决策理由/进度/报错 → 控制台与落地页 → 数据库存量文案。

---

## 1. 判定口径（先立规矩，避免改错）

| 类别 | 处理 | 例 |
|---|---|---|
| SQL 函数名 / 语法 | **保留** | `RANK()`、`SUM() OVER()`、`PARTITION BY` |
| 产品专有名词 | **保留** | Agent、DeepSeek、Hacker News、Dev.to、GDELT、BM25、CTR、QSR、Spearman |
| 平台/品牌名 | **保留** | LINE、Naver、YouTube、X、LinkedIn |
| 结构字段名（传给大模型的 JSON key） | **保留** | `accuracy`、`verdict`、`why_now`、`{{market}}` |
| 市场代码 | **保留** | US / JP / BR（标识符，不是英文单词） |
| **代码标识符写进中文句子里** | **改** | `task_spans`→执行记录、`degraded/failed`→降级/失败、`Prompt`→提示词、`feedback_analyst`→反馈分析 |
| **同一 Agent 的两套中文名** | **统一** | 见 §2 |

---

## 2. 统一 Agent 中文名（消除双套译名）

根因：内容页进度条用 `AGENT_CHAIN`，图表/模板下拉用 `TEMPLATE_LABEL`，两处对同一批 Agent
给了两套中文名——同一个 Agent 在页面上有两个名字，比留英文更伤可信度。

以后端 `src/app/labels_cn.py:AGENT_CN` 为**唯一口径**，前端 `LABEL` 表与之镜像：

| 代号 | 旧（进度条） | 新（全站统一） |
|---|---|---|
| signal_scout | 信号侦察 | **信号捕捉** |
| trend_analyst | 趋势分析 | **趋势研判** |
| angle_editor | 选题编辑 | **角度设计** |
| topic_guard | 主题闸门 | **选题守卫** |
| editor | 总编复核 | **总编审核** |
| format_adapter | 多形态派生 | **形态适配** |
| distributor | 分发计划 | **分发策略** |

另：`kb_curator`→知识库治理、`feedback_analyst`→反馈分析、`zh_mirror`→中文对照，
`evidence_guard`→证据守卫、`language_guard`→语言守卫。

---

## 3. 逐层改动

### 3.1 分析中心（`src/app/analytics/queries.py`）

| 位置 | 改前 | 改后 |
|---|---|---|
| agent_degrade 注释 | `task_spans` / `degraded/failed` / `KBCurator` / `Prompt` | 执行记录 / 降级·失败 / 知识库补库 / 提示词 |
| agent_degrade 列名 | `Agent / spans / bad / degrade_rate / fragility_rank` | Agent / 执行步数 / 降级或失败步数 / 降级率 / 脆弱度排名 |
| fpy 注释 | `pass` / `revise` / `Editor` | 通过 / 重写 / 总编审核 |
| cost 列名 | `cost_rank` | 成本排名 |
| rubric 列名与图例 | `accuracy / angle / readability / local_fit / engagement` | 准确性 / 角度 / 可读性 / 本地适配 / 互动性（**图例同步中文化**） |
| decay 列名 | `market / hours_since / exposures / cum` | 市场 / 小时（发布后）/ 曝光量 / 累计曝光 |
| format_market 注释 | `clicked/exposed` / `format_plan` | 点击量 ÷ 曝光量 / 形态分发权重 |
| funnel 阶段名 | 曝光 Exposed / 点击 Clicked / 互动 Liked+Shared | 曝光 / 点击 / 完读 / 互动（点赞+分享） |

注：`RANK()`、`SUM() OVER (PARTITION BY …)`、`SQL`、`FPY`、`CTR`、`QSR` 按 §1 保留。

### 3.2 后端读者可见文案

| 位置 | 改前 | 改后 |
|---|---|---|
| `agents/base.py` | `fallback: {err}`、`{agent} 使用规则兜底` | 降级兜底（中文名）：… |
| `agents/editor.py` | 裁决 `pass/revise/reject` | 裁决「通过 / 需修改 / 不通过」 |
| `agents/topic_guard.py` | `TCS=0.333 不达标`、`转交 Editor` | 主题一致性得分 0.333 不达标、转交总编审核 |
| `agents/researcher.py` | 改写 N 个 query | 改写 N 个检索词 |
| `agents/format_adapter.py` | `派生 N 种形态：video_script、card` | 派生 N 种形态：短视频脚本、资讯摘要卡片 |
| `agents/distributor.py` | 主发 platform（format） | 主发中文平台名（中文形态名） |
| `agents/kb_curator.py` | `replaces 触发退役` | 被标注替换对象的旧条目一并提议退役 |
| `workflow/orchestrator.py` | 总编 reject / `AngleEditor…Researcher…` / bad_case `category="Q"` | 总编判定不通过 / 角度设计环节…证据检索环节… / `category="选题质量"` |
| `api/routers/contents.py` | 重写母稿（Writer → 事实核查 → 总编复核） | 重写母稿（写作 → 事实核查 → 总编审核） |
| `services/alignment.py` | 评委：EditorAgent（LLM-as-judge，五维 Rubric 1–5） | 机器评委：总编审核环节（大模型评审，五维评分 1–5） |

新增 `src/app/labels_cn.py`：中文名唯一口径（Agent / 裁决 / 形态 / 平台 / 任务类型 + 便捷函数），
后端所有面向读者的拼装文案从这里取词。

### 3.3 提示词模板（`src/app/prompts/templates/*.md`）

角色自称从代号改为中文角色名，**JSON 结构说明与字段名一律不动**（那是给模型的结构契约）：

- `你是 Writer，` → `你是「写作」环节，`（AngleEditor / Editor / Researcher / SignalScout / TrendAnalyst /
  AudienceInsight / FormatAdapter / FactChecker / FeedbackAnalyst / Distributor 同理）
- `你是 TrendForge 知识库策展人 KBCurator。` → `…知识库策展人。`
- `Rubric 五维评分（1-5）：` → `五维评分标准（1-5）：`
- `有不可修复问题 → reject` → `→ 判定不通过（reject）`（保留枚举以便与 verdict 对齐）
- `会被总编 reject` → `会被总编判定不通过`

模板文件与 `prompts.content` 库内版本同步改写，避免「文件是中文、版本历史是英文」。

### 3.4 控制台与落地页

`ui/console/assets/app.js`：
Prompt→提示词、Trace→执行轨迹、BadCase→失败案例库、Rubric→五维评分/五维评分标准、
KBCurator→知识库策展、FeedbackAnalyst→反馈分析、AngleEditor/FormatAdapter/Distributor/SignalScout→中文环节名、
`vs`→对比、`diff`→版本对比、`job`→任务编号、`score`→相关度、`tokens`→词元、CTA→行动号召、
合成 KB→合成知识库、`simulator.py`→仿真器、模板下拉不再裸露内部代号。

`ui/index.html`：Agent 清单只留中文名；Prompt/Rubric→提示词/评估标准；EditorAgent→机器评委；Demo→演示站。

### 3.5 数据库存量文案（两库同时改）

**关键点**：改代码只影响新产生的数据，页面上看到的绝大部分是历史落库的旧文案，必须回填。

| 表.列 | 改前 | 改后 | 行数 |
|---|---|---|---|
| `bad_cases.root_cause` | 总编 reject（首次尝试） | 总编判定不通过（首次尝试） | 2 |
| `bad_cases.fix_action` | AngleEditor…Researcher… | 角度设计环节…证据检索环节… | 2 |
| `bad_cases.category` | `Q` | 选题质量 | 3 |
| `task_spans.decision_reason` | 裁决 reject/revise、fallback: [writer]、N 个 query、TCS= | 裁决「不通过/需修改」、降级兜底（写作）、N 个检索词、主题一致性得分 | 144 / 111 |
| `task_spans.warnings` | `writer 使用规则兜底: [writer] JSON 解析失败: Expecting value…` | 降级兜底（写作）：返回内容非合法 JSON | 39 / 12 |
| `tasks.error` | 总编 reject：… | 总编判定不通过：… | 4 |
| `prompts.content` / `prompt_suggestions.new_prompt` | 你是 Writer、总编 reject、Rubric | 你是「写作」环节、总编判定不通过、五维评分标准 | 3 + 1 |
| `markets.culture_notes`（JP/GB） | sensational / understatement | 耸动 / 克制低调 | 2 |

> 坑：`task_spans.warnings` 是 JSON 数组且中文被 `\uXXXX` 转义，
> 直接对原始文本做中文替换会全部 miss，必须先 `json.loads` 解码 → 清洗 → 写回。

---

## 4. 验收

| 手段 | 结果 |
|---|---|
| `python -m compileall src/app` | 通过 |
| 关键文件 AST 语法校验（13 个） | 全部 OK |
| 两库 + `markets.json` 关键词扫描（`Rubric/KBCurator/EditorAgent/AngleEditor/总编 reject/BadCase/Prompt/query/fallback/sensational`） | **0 命中** |
| 真实后端抓取夹具（23 个端点）后扫描同一词表 | **0 命中** |
| `node tools/smoke_render.js` | **66 项全绿**（含 12 项新增中文断言） |

新增回归断言（锁住本轮改动，防止回退）：
失败案例库无 BadCase、内容页副标题无 Trace、图表口径无 Rubric、降级率列名、
提示词版本治理无 Prompt、知识库策展无 KBCurator、评估中心无 FeedbackAnalyst。

顺带修复 `tools/fetch_fixtures.py` 漏抓 `/contents?limit=5`（冒烟测试内容详情依赖它，
缺失会静默跳过该视图导致 2 项断言假失败）。

---

## 5. 有意保留的英文（不是漏改）

1. **SQL 原文**：分析中心每张图可展开的真实 SQL —— 这是「JD 明确要求 SQL」的能力证据，翻译它反而失真。
2. **大模型输出契约**：提示词里的 JSON 字段名与枚举（`scores.accuracy`、`verdict: pass|revise|reject`）——改了就改坏链路。
3. **平台/品牌/技术专名**：LINE、Naver、DeepSeek、BM25、CTR、QSR、Spearman。
4. **api.js 的 `TF_API_TOKEN` / `HTTP`**：环境变量名与协议名。

**设计判断**：前端保留内部代号（`signal_scout`）作为 API 原值与 `<option value>`，
展示层统一过 `lb()` 翻译——这样既不改后端契约与数据库，也保证读者看到的是中文。
「英文只出现在机器边界，中文负责一切人眼可见之处」。

---

## 6. 遗留 / 下一步

- 落地页与 README 的英文项目名、四段式框架名（SENSE/PRODUCE/AMPLIFY/EVALUATE）保留英文+中文双写，
  属品牌语汇；若访谈反馈仍嫌生硬，可改为纯中文。
- 分析中心图表 `columns` 已中文化，CSV 导出同步受益；如后续新增图表，务必同时给中文列名。
- 新增枚举值时，**先补 `labels_cn.py` 与前端 `LABEL` 表**，再写渲染逻辑。
