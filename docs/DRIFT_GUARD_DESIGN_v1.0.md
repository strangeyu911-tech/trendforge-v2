# TrendForge V2 主题漂移防护设计 `DRIFT_GUARD_DESIGN_v1.0`

> M5 专项：解决"拼盘稿"问题——主线内容被无关新闻污染。
> 配套代码：L0 `agents/researcher.py` + `config.py`；L1 `rag/tcs.py` + `agents/topic_guard.py` + `prompts/templates/topic_guard.md` + `workflow/orchestrator.py`；L2/L3 `agents/writer.py` + `prompts/templates/writer.md`。回归测试：`tools/test_drift_guard.py`（15/15 通过）。

---

## 1. 现象与问题

08-06 实测中，一篇以"机器人量产"为主线的稿件，正文中混入了**电竞转会、百度财报、中超赛果、歌手新歌**等四条完全无关的素材。读者视角：这篇稿子在讲什么？——答不上来。我们把这类稿称为**拼盘稿（patchwork article）**。

拼盘稿的危害不是"文笔差"，而是**破坏供给引擎的存在意义**：内容供给的前提是"每篇内容在目标市场讲清楚一件事"。主题漂移让 QSR（有效内容供给率）的统计失真——稿子发布了，但没人认。

---

## 2. 根因分析：四个闸门全在漏

漂移不是单点 bug，而是**四个环节各自漏一点，叠加成系统性渗漏**：

| 闸门 | 原设计意图 | 实际泄漏点 |
|---|---|---|
| **检索层（Researcher）** | 类目一致性过滤 + 不足时放宽 | 放宽分支 `if len(relevant) < 4: relevant = [...][:8]` **无条件覆盖**了类目过滤成果；且 `same_cat >= 3` 触发阈值与 `< 4` 放宽恰在 3 条同类目证据处 100% 抵消——**过滤等于没做** |
| **引用层（Writer 约束）** | "至少引用 2 处不同来源" | 奖励**广度**，制度性鼓励把电竞/百度/中超都拽进来充数 |
| **结构层（Writer 模板）** | 每条证据一段 | "每条证据一节"天然导出具无关性的拼盘结构 |
| **质量层（Editor Rubric）** | Rubric 五维评分 | 五维管"写得好不好"，**不管"有没有跑题"**——拼盘稿五维分可能很高 |

关键认知：**Rubric 补不了主题漂移**。必须在 Writer 之后、FactChecker 之前插一道**主题纯度闸门**，且这道闸门必须**零成本**（不能每条内容多花一次 LLM 调用），**语言无关**（JP/KR/BR 市场词面对不齐 EN-KB 时不能误杀）。

---

## 3. 解决方案：四层防护 L0 → L3

```
L0 检索层：只召回"相关"的证据，且标记主干/背景
        ↓
L2 Writer：引用约束反转（60% 以上引用必须来自主干），L3 fallback 去拼盘
        ↓
L1 TopicGuard：TCS 硬闸门，不达标定点重写 / 删节兜底
        ↓
   FactChecker → Editor（Rubric 五维）
```

> 命名顺序 L0→L3 是"离源头由近及远"的修复顺序；L1 是核心闸门，故先讲 L1。

### 3.1 L0 — 检索层修复（`researcher.py` + `config.py`）

- `_search` 拆分为「召回 candidates」与「过滤 `_filter`」两步，过滤结果**不被放宽分支覆盖**。
- 放宽逻辑改为**仅在证据不足时回补同类目候选**：`if len(relevant) < MIN_EVIDENCES(3): pool = 同类目候选; relevant = pool[:3] or relevant`。
- 双阈值相关性：`lexical_usable = top >= topic_min_score(1.0)`；可用时设地板 `floor = max(top*0.3, topic_min_score*0.5)`，低于地板直接丢弃。
- 主干文档聚类：主干文档 ≤3 篇、背景文档 ≤2 篇；每条证据打 `is_main = (doc_title == main_doc)`。
- 配置新增：`top_evidences 10→5`、`topic_min_score=1.0`、`tcs_main_ratio_min=0.6`、`tcs_cross_doc_max=2`。

### 3.2 L1 — TCS 主题一致性硬闸门（`rag/tcs.py` + `topic_guard.py`）

**TCS 公式（零 token、语言无关，只依赖 `[ev_xxx]` 引用结构）**：

```
TCS = 0.6 × main_ratio + 0.4 × max(0, 1 − min(cross_docs, cap) / cap)
cap = tcs_cross_doc_max + 1 = 3
```

- `main_ratio`：引用中来自主干证据的占比。
- `cross_docs`：正文引用的不同文档数（背景证据可作补充，但不许单独成节）。

**放行条件（三条件全满足）**：

```
main_ratio ≥ 0.6  且  cross_docs ≤ 2  且  无漂移节
```

**漂移节判定（逐节）**：

```
某节 "有引用却无一条来自主干"  → 漂移
或  lexical_usable 为真 且  该节词面相关度 < 0.15  → 漂移
```

**TopicGuard 处置流程**：

1. 算 TCS；
2. 若 `passed` → 直接放行，**零额外 LLM 成本**；
3. 若不达标 → **定点重写**仅漂移节（只把主干证据喂给 LLM，按节索引 `idx` 改），复评；
4. 复评更差 → 回退重写前版本；
5. 仍不达标 → **删节兜底**：摘除漂移节，结构**保底留 2 节**（`drop_drift_sections(min_keep=2)`），**诚实记录实际摘除数**（受结构下限保护时显式标注"未能摘除"，不谎报）。

> 设计权衡：`cap=3`、`cross_docs≤2` 允许 1–2 篇背景证据做"为什么值得关注"的补充，但禁止无关新闻单独成节。2 节结构下限保证即便全文漂移也不产出空稿。

### 3.3 L2 — 引用约束反转（`writer.py` + `writer.md`）

旧约束：`if len(cited) < max(2, len(evidences)//3): raise`（**奖励广度**）。
新约束：

```
main_ids = 主干证据 id 集合
main_cited = [c for c in cited if c in main_ids]
if not cited: raise
if len(main_cited) < min(2, len(main_ids) or 2): raise
```

即：**引用的 60% 以上必须来自主干证据**，缺引用直接失败走 fallback。"铁律 #2"改成引用主干、`#3` 改成"不追求引用条数多，只追求主线站得住"。

### 3.4 L3 — fallback 去拼盘（`writer.py`）

旧 fallback：基于大纲模板"每条证据一节"拼装 → 制度性拼盘。
新 fallback：固定 **2 节**（事件概览 + 为何值得关注），**只引用主干文档**，不跨源拼装。回归测试验证该兜底稿自身 TCS=1.0，能通过闸门。

---

## 4. 指标落库与度量

每次供给，`content.quality.topic_guard` 写入：

```json
{
  "tcs": 0.766,
  "passed": true,
  "main_ratio": 0.833,
  "cross_docs": 1,
  "drift_sections": [],
  "lexical_usable": true,
  "reason": "..."
}
```

度量口径见 [METRICS_FRAMEWORK_v1.0.md §2.2.1](METRICS_FRAMEWORK_v1.0.md)：闸门通过率 `tcs_pass_rate`、平均 `avg_tcs`。

---

## 5. 已知局限（诚实标注）

1. **2 节结构下限**：极端情况下（全文漂移）只能保证不产出空稿，无法凭空补全主线——此时仍依赖 Editor 裁决 `revise` 或 `reject`。
2. **词面相关度地板依赖 BM25**：`lexical_usable` 仅在检索层认为 top 证据可用时启用；跨语言强失配时该信号自动让位给引用结构判定，不会误杀。
3. **TCS 不替代 Rubric**：TCS 管"主题纯度"，Rubric 管"写得好不好"，二者互补，任一不达标都进修订/否决。
4. **回归测试是构造场景**：`tools/test_drift_guard.py` 用 08-06 真实漂移结构构造，验证闸门/兜底行为正确，但真实漂移率下降幅度需跑端到端批量（建议 mix 真实 key 跑 before/after 对比）才能给出量化数字。

---

## 6. 验证

`tools/test_drift_guard.py` 15/15 通过，覆盖：

- L0 旧过滤器（证明泄漏）vs 新 `_filter`（证明无污染）；
- 跨语言时词面闸门自动禁用不影响引用结构判定；
- TCS 放行（健康稿）/ 拦截（拼盘稿）/ 定点定位漂移节 / 删节保底 2 节；
- L2 旧规则（奖励广度）vs 新规则；L3 兜底稿固定 2 节、只引主干、自身 TCS=1.0。
