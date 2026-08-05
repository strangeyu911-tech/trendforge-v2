# TrendForge 修订机制设计方案（Revise / 按修改意见重写）

> 状态：设计方案 v1.0（待评审）
> 作者：小咪 ｜ 日期：2026-08-06
> 关联：闭环叙事 "Sense → Produce → Amplify → Evaluate"，质量门 Editor 裁决 `pass/revise/reject`

---

## 1. 背景与问题澄清

控制台里内容详情页会显示 Editor 的裁决（如「需修改」），但**当前没有任何入口能就地触发一次修订**。需要先澄清一个常见误解：

- **修改能力本身是存在的**，但只活在「生成时」的自动回退循环里：
  `orchestrator.py:63-69` 在 `review.verdict == "revise"` 时，自动重跑 `Writer(editor_feedback) → FactChecker → Editor`，最多 `max_review_rounds`（默认 2）轮。
- **一旦内容落库，这个循环就不可达了**。控制台 UI、API 都没有「对已有内容重新修订」的入口。于是 `revise` 成为控制台上的一个**死状态**——能看到、改不了。
- 那条 Apple 兜底内容停在 `revise`，是因为它是**兜底成稿**（KB 召回不足 → Writer 走 `fallback`），低证据喂不饱 Rubric，2 轮改完仍不过审，于是带 `revise` 落库。

**结论**：缺口在「产品表层的交互闭环」，不在「底层能力」。本项目名为「四段式闭环」，却把 Evaluate 的出口做成了断头路——这正是求职面试里最该补、也最容易讲出彩的一刀。

---

## 2. 目标与非目标

### 目标
1. 内容详情页在 `quality.verdict == "revise"` 时，提供「按修改意见重写」动作。
2. 该动作复用**已有的** Produce 段子链（Writer→FactChecker→Editor），不另起炉灶。
3. 修订后：正文、质量裁决、多形态派生、中文镜像全部一致刷新，控制台立即可见新结果。
4. 闭环在 UI 上闭合，可作为「human-in-the-loop / evaluate→act」叙事素材。

### 非目标（明确不做，避免 scope creep）
- ❌ 完整 HITL 工作流（多级审批、角色权限、修改意见的人工编辑输入框）。
- ❌ 版本树 / diff 对比 / 修订历史回溯（最多记录一轮元数据）。
- ❌ 重跑整个流水线（SignalScout→…→Researcher 不需要，证据已落库）。
- ❌ 重跑 Distributor（分发计划是平台/受众/时段的**策略**层，与正文措辞弱耦合，重跑只增成本与不稳定性）。
- ❌ 自动无限重试直到 `pass`（与生成时一致的轮数上限即可）。

---

## 3. 方案概览

```
控制台内容详情页
   │  verdict == "revise" 时显示按钮
   ▼
POST /api/contents/{id}/revise
   │
   ├─ 1. 并发护栏：status "revising"，拒绝重复触发
   ├─ 2. 从 Content 重建 data 字典（brief/evidences/article/fact_check/review）
   ├─ 3. 复用子链循环：
   │        editor_feedback = quality.revision_advice
   │        while verdict=="revise" and rounds<max:
   │            Writer(editor_feedback) → FactChecker → Editor
   ├─ 4. 刷新多形态：FormatAdapter(新 article)   # 保持 video_script/card 等与新正文一致
   ├─ 5. 落库更新：title/summary/body/quality/formats + 修订元数据
   ├─ 6. 失效中文镜像：translation={}  （下次查看按需重建）
   └─ 7. status 恢复 "published"，返回更新后的完整 content
   ▼
前端重新渲染详情 + 若 needs_zh 则重新拉取 /zh
```

---

## 4. 后端设计

### 4.1 新增端点
`POST /api/contents/{content_id}/revise`

- 鉴权/限流：复用现有内容路由风格，inline 等待（与现有 pipeline 路由一致，free 实例可接受）。
- 入参（可选）：`{ "use_feedback": true }`（默认 true；若 false 则忽略旧意见、纯重写）。
- 返回：更新后的完整 content JSON（供前端直接 re-render）。

### 4.2 重构：把现有回退循环抽成可复用函数
`src/app/workflow/orchestrator.py` 现有 63-69 行内联循环，抽成：

```python
async def run_revise_rounds(ctx, data) -> dict:
    """复用 Produce 段子链做修订；data 需含 brief/evidences/article/fact_check/review"""
    rounds = 0
    while data["review"]["verdict"] == "revise" and rounds < settings.max_review_rounds:
        rounds += 1
        data["editor_feedback"] = data["review"].get("revision_advice", "")
        data.update(await WriterAgent()._exec(ctx, data))
        data.update(await FactCheckerAgent()._exec(ctx, data))
        data.update(await EditorAgent()._exec(ctx, data))
    return data, rounds
```
生成时流水线直接调用它（消除重复）；修订端点也调用它。

### 4.3 修订端点核心逻辑
```python
content = await session.get(Content, content_id)
if content.status == "revising":
    raise HTTPException(409, "修订进行中")
if (content.quality or {}).get("verdict") != "revise":
    raise HTTPException(400, "仅 revise 内容可修订")

content.status = "revising"
await session.commit()

ctx = RunContext(...)  # 复用既有构造
data = {
    "brief": content.brief,
    "evidences": content.evidences,
    "article": {"title": content.title, "summary": content.summary, "body": content.body},
    "fact_check": (content.quality or {}).get("fact_check", {}),
    "review": {k: v for k, v in (content.quality or {}).items() if k != "fact_check"},
}
data, rounds = await run_revise_rounds(ctx, data)
data.update(await FormatAdapterAgent()._exec(ctx, data))   # 刷新多形态

content.title = data["article"]["title"]
content.summary = data["article"]["summary"]
content.body = data["article"]["body"]
content.formats = data.get("formats", content.formats)
content.quality = {"fact_check": data.get("fact_check", {}), **data.get("review", {})}
content.translation = {}                                  # 失效中文镜像
content.quality["_revise"] = {"rounds": rounds, "at": datetime.utcnow().isoformat()}
content.status = "published"
await session.commit()
return serialize(content)
```
> 注：`_revise` 元数据塞进 `quality` 子键，**不新增数据库列**，避免迁移（free 实例 SQLite 每次部署会重置，迁移更脆弱）。

### 4.4 并发护栏
- 进入即把 `status` 置为 `"revising"`，重复请求返回 409。
- 异常时 `status` 回滚为 `"published"`，不卡死。

### 4.5 中文镜像
- 修订后 `translation={}` 使其失效；前端查看时 `ensure_zh_mirror` 会因镜像不完整而重新生成（覆盖扩展后的 distribution + quality 文本）。无需手写刷新逻辑。

---

## 5. 前端设计（控制台）

文件：`ui/console/assets/app.js`

1. **按钮出现条件**：在内容详情的质量面板（`renderQuality` 或详情头部）当 `c.quality?.verdict === "revise"` 且 `c.status !== "revising"` 时，渲染：
   ```html
   <button id="btn-revise" data-id="${c.id}">按修改意见重写</button>
   ```
2. **点击处理**：
   - `btn` 置 disabled，文案 →「重写中…」。
   - `POST /api/contents/{id}/revise`。
   - 成功：重新 `fetchContent(id)` 渲染详情；若该内容 `needs_zh`，再 `POST /zh` 拉镜像。
   - 失败（如 409/400）：toast 提示，恢复按钮。
3. **自然消失**：修订后 `verdict` 变 `pass` → 按钮不再渲染，闭环在 UI 上可见地闭合。

---

## 6. 关键设计取舍（讲给面试官听的）

| 取舍 | 决策 | 理由 |
|---|---|---|
| 只重跑 Produce 子链，不重跑 SENSE/Researcher | ✅ 重跑子链 | `evidences` 已落库，重跑研究既无新信息又烧钱。 |
| 修订后是否刷新多形态 | ✅ 重跑 FormatAdapter | 形态（脚本/卡片/快讯）派生自正文，不刷新会和新正文不一致；FormatAdapter 便宜且确定性高。 |
| 是否重跑 Distributor | ❌ 不重跑 | 分发计划是策略层（平台/受众/时段），与正文措辞弱耦合；重跑增成本+随机性，收益低。 |
| 重试轮数 | 沿用 `max_review_rounds` | 与生成时一致，行为可预期；不无限重试。 |
| 是否加数据库列存修订历史 | ❌ 塞进 `quality._revise` | 免迁移，免费实例更稳。 |
| 是否做 HITL 审批流/diff | ❌ 不做 | 超出「补闭环」的最小必要，属 scope creep。 |

---

## 7. 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/app/workflow/orchestrator.py` | 抽出 `run_revise_rounds()`；原 63-69 行改调用它 |
| `src/app/api/routers/contents.py` | 新增 `POST /api/contents/{id}/revise` |
| `ui/console/assets/app.js` | 详情页按 `verdict` 渲染按钮 + 点击处理 + 重渲染 |
| `docs/revise_mechanism_design.md` | 本方案 |

预计工作量：**后端 ~0.5 天，前端 ~0.25 天**（核心逻辑 100% 复用现有 agent，无新模型/新 prompt）。

---

## 8. 验收标准

1. 取一条 `verdict=="revise"` 的内容（如当前 US 兜底 Apple 文），详情页出现「按修改意见重写」按钮。
2. 点击后：正文/质量/多形态被改写并落库；若改为 `pass`，按钮消失；若仍 `revise`，按钮保留且 `quality._revise.rounds` 记录轮数。
3. `needs_zh` 内容修订后，中文镜像自动刷新（受众/评语为新中文）。
4. 并发：修订中再次点击返回 409，不重复跑 LLM。
5. 不破坏现有生成流水线（抽函数后行为不变）。

---

## 9. 风险与配套建议

- **兜底内容反复 revise**：本方案解决「能改」，但没解决「兜底内容为什么总是不及格」。建议在修订机制之上，另立一个小改进：兜底成稿在落库时标记为 `draft`/`needs_human`（而非 `published`），从语义上区分「系统自信的成品」与「规则兜底的半成品」。这比单纯加按钮更能体现 PM 对**终态语义**的思考。
- **成本**：每次修订 = Writer+FactChecker+Editor+FormatAdapter 四次 LLM 调用。演示环境可接受；若上线需加软限流（如每内容 1 次/小时）。
- **可逆性**：当前不保留旧正文（覆盖写）。v1 可接受；若面试官追问，可答「v2 规划版本树」。

---

## 10. 实施顺序（落地时）

1. 抽 `run_revise_rounds()` 并让流水线调用（先保证零行为变化，跑通现有测试/一次生成验证）。
2. 加 `POST /revise` 端点 + 并发护栏 + 落库更新。
3. 前端按钮 + 重渲染。
4. 验证验收标准 1–5。
5. （可选配套）兜底内容 `draft` 语义标记。
