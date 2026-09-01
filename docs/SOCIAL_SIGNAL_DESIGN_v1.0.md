# 社媒趋势信号接入设计 `SOCIAL_SIGNAL_DESIGN_v1.0`

> 回答一个问题：**"日本用户上周在 X 上讨论什么、你的系统怎么知道？"**
> 当前感知端（SignalScout）只有 HN / Dev.to / GDELT 三源——都是新闻/社区**文章**源，
> 没有任何社媒**讨论**信号。本文给出双信号融合架构、分市场接入清单、API/合规约束与分级路线。

---

## 1. 为什么必须有社媒信号（问题定义）

新闻源和社媒源捕捉的是两类不同性质的内容信号：

| 维度 | 新闻/社区源（现状） | 社媒趋势源（缺失） |
|---|---|---|
| 时效 | 小时级（发文滞后） | 分钟级（讨论先于报道） |
| 性质 | 事实与叙事 | 情绪、争议、真实提问 |
| 覆盖偏差 | 英文科技社区为主（HN/Dev.to = GLOBAL） | 本地语言、本地话题（正是本地化要害） |
| 对选题的作用 | "发生了什么" | "人们在关心/争论什么"——需求侧 |

M1 实测已暴露症状：`RUN_REPORT_v1.1` 记录"中文/葡语市场表现优于英语市场，推测与信号源（HN/Dev.to 英文技术流）密度过高有关"。缺社媒信号 = 需求侧判断（AudienceInsight）只能靠 LLM 从市场档案文字演绎，没有一手需求数据归纳。

## 2. 双信号融合架构

```
                    ┌─ 新闻/社区源（现有）：HN / Dev.to / GDELT ─┐
SignalScout ──── 并行拉取                                            合并去重
                    └─ 社媒趋势源（新增）：X / Reddit / Naver DataLab / YouTube ─┘
                                        │
                        RawSignal 扩展字段：signal_type: "news" | "social"
                                        │
              TrendAnalyst 融合判断：同一话题在两类源共振（新闻在报 + 社媒在吵）
              → 调升 strength；仅社媒热但无新闻可溯源 → 交给 AudienceInsight 做
              "需求假设"信号（可生成解读型选题，不可生成事实报道——无证据支撑）
```

**融合规则（防社媒假热点）**：
1. 社媒信号单独不直接进选题主链——必须与新闻源共振，或降级为 AudienceInsight 的需求假设输入；
2. 社媒互动量（转发/讨论数）与新闻互动量（points）分别归一化后参与 `sort_by_engagement`；
3. 社媒信号强制携带 `source_url` 指向可公开验证的讨论页，保证 Trace 可溯源性不破。

## 3. 分市场接入清单（API 可得性与约束，2026-09 评估）

| 市场 | 首选源 | 可得性 | 约束 |
|---|---|---|---|
| US / 全球 | X API v2（trends/search） | 官方付费 | Basic 档约 $200/月（价格随时调整）；免费档仅发帖无读取 |
| US / 全球 | Reddit（OAuth + 自定义 UA） | 官方免费（限额内） | 本项目已实测 datacenter IP 被拒（403），需住宅 IP 或 OAuth 走用户代理池；M1 时因此被砍 |
| US / 全球 | YouTube Data API v3 `mostPopular` | 官方免费（配额制） | 需 API Key；视频标题/标签作信号，不抓评论正文 |
| US / 全球 | Google Trends（按国家 interest） | 无官方 API | pytrends 非官方库，限流易碎；只作辅助不做主源 |
| JP | X Japan 趋势 + Yahoo!リアルタイム | 无官方公开 API | 趋势词页面可解析但脆弱，违反 ToS 风险需评估；优先靠 GDELT `sourcelang:japanese` + X 付费 API |
| KR | Naver DataLab 搜索趋势 API | **官方免费（Key）** | 네이버 급상승검색어（实时热搜）服务已于 2021 年终止，不能作为信号源；DataLab 提供搜索量相对趋势，合法稳定 |
| BR | X Brazil + YouTube BR | 同上 | Kwai 在下沉市场渗透高，但无公开 API |
| CN | 微博热搜 | 无官方 API | 第三方聚合接口不稳定且合规风险高，MVP 明确不接 |

## 4. 分级接入路线

| 阶段 | 接入项 | 成本 | 产出 |
|---|---|---|---|
| **P1（低风险先行）** | YouTube `mostPopular`（US/JP/KR/BR）+ Naver DataLab（KR） | 免费 Key，半天 | 每市场新增 1-2 个社媒趋势源；`signal_type=social` 进融合链路 |
| **P2（合规修复）** | Reddit OAuth 接入恢复（自定义 UA + 限额退避） | 免费，1 天 | 英文长尾讨论信号；验证 OAuth 后 datacenter 可达性 |
| **P3（付费评估）** | X API Basic 档 | ~$200/月 | 各市场分钟级趋势词；需 ROI 论证（SignalScout 单日调用频次 × 成本 vs 选题增量） |
| **明确不做** | 微博热搜爬取、无授权趋势页解析 | — | 合规风险 > 收益；写进"已评估未做" |

## 5. 与现有机制的接入点（改动面）

- `sources/base.py`：`RawSignal` 增加 `signal_type: str = "news"` 字段（向后兼容，`from_dict` 已有 setdefault 模式）；
- `sources/__init__.py`：`fetch_market_signals` 并行拉取第三类源，`diag` 增加 `social` 计数；
- `agents/signal_scout.py`：信号行模板把 `signal_type` 喂给 LLM（"社媒在讨论"与"媒体在报道"区分表述）；
- `agents/trend_analyst.py` + `prompts/templates/trend_analyst.md`：加入共振判断规则（§2 融合规则 1）；
- UI 信号卡：社媒源加类型徽标（沿用"仿真"角标的标注哲学——**来源性质必须可见**）；
- 降级矩阵不变：任一社媒源失败静默跳过，新闻源照常，主链路不裸崩。

## 6. 为什么 MVP 没接（已评估未做）

1. **实测受阻**：Reddit（计划内）在 datacenter IP 下 100% 复现 403（见 `ITERATION_PLAN` M1 实测调整），砍掉以保"生产环境真实可跑"；
2. **合规边界**：无官方 API 的趋势榜（微博热搜、Yahoo! Japan リアルタイム）解析属灰色地带，与项目"合规 UA、遵守 robots"的抓取原则冲突；
3. **付费门槛**：X API 读取档 $200/月，对 Demo 规模的选题增量无法证成 ROI；
4. **替代层已就位**：GDELT `sourcecountry + sourcelang` 双过滤（M1 后补齐）已能拿到本地语言新闻——社媒信号补的是"情绪与讨论"，不是"本地语言"本身。

> 面试表述口径：社媒信号是**已评估、有路线、被约束挡住**的项，不是没想过——约束是 API 付费墙与 datacenter IP 封禁，路线是 P1 免费源先行。

## 7. 边界声明

- 本文是设计文档，非已实现功能；§3 的 API 可得性结论基于 2026-09 评估，接入前需复测；
- 社媒信号的互动数据（转发/点赞）进入系统后与新闻源互动数据同权参与"仿真校准锚点"，其仿真标注策略与 `METRICS_FRAMEWORK` §4 一致。
