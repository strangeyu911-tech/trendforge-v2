# TrendForge V2 内容形态设计 `CONTENT_FORMAT_DESIGN_v1.0`

> 一份解释"为什么是这几种形态、每种给谁看、怎么跟市场档案咬合"的 PM 设计文档。
> 核心命题：**一次生产、多形态分发**——母稿（article）是深度载体，4 种派生形态覆盖从"深度阅读"到"碎片互动"的完整消费光谱，把单条内容的边际分发成本压到接近零。

---

## 1. 设计哲学：一稿多发（Write-once, Distribute-many）

V1 只产出图文长文，意味着同一条热点只能投一个形态、一个平台。V2 把"生产"和"形态"解耦：

```
            Researcher + Writer + FactChecker + Editor
                          │
                      母稿 article（深度长文，含 [ev] 事实引用）
                          │  FormatAdapter 一次派生
        ┌─────────┬───────┼──────────┬──────────┐
   video_script   card   brief_news   comment   （+ article 自身直发）
   短视频脚本    摘要卡    快讯        评论引导
```

- **事实一致性由 `article` 保证**：所有派生形态都从同一母稿切，且母稿的 `[ev_xxx]` 引用被事实核查过，派生不会凭空加料（FormatAdapter 三道 `clean_ev` 防线）。
- **边际成本≈0**：母稿已是最大成本（Writer+FactChecker+Editor）。4 种派生的 LLM 成本远低于重写 4 篇，单条"总拥有成本"被显著摊薄——这是成本效率层（见 `METRICS_FRAMEWORK_v1.0` §2.4）的直接来源。

---

## 2. 为什么是这 4 种派生形态（不是更多，也不是更少）

选择标准：**覆盖一条热点的完整消费光谱，且每种都有明确的平台原生归属**。少于 4 种会漏掉一类消费场景；多于 4 种会稀释精力且多数平台形态可归并。

| 光谱位置 | 形态 | 解决什么 | 若缺它会怎样 |
|---|---|---|---|
| 深度 | `article`（母稿） | 需要论证、数据、上下文的读者 | —（是基座） |
| 视觉碎片 | `video_script` | 短视频平台的高频被动消费 | 丢掉抖音/TikTok/Reels 流量 |
| 信息流扫读 | `card` | X/Line/WhatsApp 的快节奏刷屏 | 长文在信息流被划走 |
| 即时推送 | `brief_news` | 快讯流 / -breaking 场景 | 失去"第一落点"速度感 |
| 社交互动 | `comment` | 评论区讨论、UGC 撬动 | 内容看完即走，无留存钩子 |

> 关键判断：**不单独做"长视频/播客/图文海报"**。长视频与播客是视频脚本的渲染延伸（缺渲染层，见 §6）；海报是 card 的视觉变体。把它们并列为独立形态会撑大生产面却不增覆盖，是有意不做。

---

## 3. 每种形态详解

### 3.1 article — 母稿 / 深度长文
- **消费场景**：深度阅读——微信公众号、LinkedIn 长文、Naver 新闻、Yahoo 新闻、Newsletter。
- **信息密度**：★★★★★ 最高。承载论证链、数据、多证据。
- **钩子位置**：标题 + 首段"反直觉结论/冲突"。读者是为"读懂一件事"而来。
- **结构**：`summary`（导语） + `body.sections[]`（带小标题的段落，每段可挂 `[ev]`）。
- **适配平台**（来自 `markets.json`）：`wechat`(CN) / `linkedin`(US) / `naver`(KR) / `yahoo_news`(JP)。

### 3.2 video_script — 短视频脚本
- **消费场景**：碎片化被动消费——抖音、YouTube Shorts、Instagram Reels、Kwai。
- **信息密度**：★☆☆☆☆ 最低，但传播面最广。
- **钩子位置**：**开头 3 秒**（hook 必须在前 3 秒抛出冲突/反常识），否则划走。
- **结构**（FormatAdapter 输出）：`hook` / `shots[]`(分镜+口播) / `subtitles` / `cta` / `hashtags`。
- **适配平台**：`douyin`(CN) / `youtube_shorts`(US) / `instagram`(KR/BR) / `kwai`(BR)。

### 3.3 card — 资讯摘要卡片
- **消费场景**：信息流快速扫读——X、Line、WhatsApp、Weibo 转推。
- **信息密度**：★★★☆☆ 中。3–5 条要点 + 关键数据，一眼看懂。
- **钩子位置**：**第一条要点 / 最大数字**（卡片首行决定要不要点开）。
- **结构**：`title` + `points[3-5]` + `key_data`。
- **适配平台**：`x`(US/JP) / `line`(JP) / `whatsapp`(BR) / `weibo`(CN) / `naver`(KR)。

### 3.4 brief_news — 快讯
- **消费场景**：即时推送 / 快讯流——X push、Weibo 突发、WhatsApp 转发。
- **信息密度**：★☆☆☆☆ 极低，强调"速度 + 一句说清"。
- **钩子位置**：**一句话标题本身**（100 字内说完，背景一句话兜底）。
- **结构**：`headline` + `body`(≤120字) + `background`(一句)。
- **适配平台**：`x` / `weibo` / `whatsapp`。

### 3.5 comment — 评论区引导
- **消费场景**：社交讨论撬动——X 评论区、Reddit、Yahoo 新闻评论区。
- **信息密度**：★★☆☆☆ 低，但**互动导向**最强。
- **钩子位置**：**争议性 / 开放性提问**（引发站队与补充）。
- **结构**：`question` + `angles[2]`（两个讨论角度）。
- **适配平台**：`x`(JP) / `yahoo_news`(JP) / `weibo`(CN)。

---

## 4. 形态 × 市场档案交叉矩阵

矩阵来自 `markets.json` 各市场 `platforms[].formats`——**形态不是拍脑袋定的，是市场平台生态倒推的**。

| 形态 \ 市场 | 🇺🇸 US | 🇯🇵 JP | 🇰🇷 KR | 🇧🇷 BR | 🇨🇳 CN |
|---|---|---|---|---|---|
| **article** | LinkedIn | Yahoo News | Naver | — | WeChat |
| **video_script** | YouTube Shorts | — | Instagram, YouTube | Instagram, Kwai | Douyin |
| **card** | X | Line | Naver, Instagram | WhatsApp, Instagram | WeChat, Weibo |
| **brief_news** | X | Line | — | WhatsApp | Weibo |
| **comment** | — | X, Yahoo News | — | — | Weibo |

**读矩阵得到的产品决策**：
- **US / JP** 偏"信息流 + 长文"（X/Line + LinkedIn/Yahoo），`card` 与 `article` 是主力。
- **KR / BR** 偏"视觉 + 即时"（Instagram/Kwai/Douyin），`video_script` 权重最高。
- **CN** 全形态通吃（微信深度 + 微博快讯/互动 + 抖音视频），是形态覆盖最完整的市场。
- **comment 仅 JP/CN 启用**：因这两市场评论区文化活跃（Yahoo 评论区 / 微博），其他市场评论引导 ROI 低，有意收敛。

> 注：`article` 是母稿，本身也作为直发形态出现在部分市场；其余 4 种为 FormatAdapter 的派生输出（`config.formats = ("video_script","card","brief_news","comment")`）。

---

## 5. 形态选择逻辑（系统怎么决定派哪些）

1. **AngleEditor** 产出选题简报时，已带 `format_plan`（依据 `Market.platforms` 推导该市场该平台该用的形态组合）。
2. **FormatAdapter** 优先用 `brief.format_plan`；为空时回退 `settings.formats`（全 4 种）。
3. **Distributor** 把派生形态映射到具体平台的 peak 时段（`markets.json` 每平台的 `peak`）生成分发计划——形态与"何时发到哪"是一起决策的，不是事后硬塞。
4. **降级**：任何 Agent 失败走规则兜底（如 FormatAdapter 兜底截取母稿生成 card/brief_news/comment 三形态），保证"至少能发"，主链路永不裸奔。

---

## 6. 边界与诚实标注

- **多模态渲染未做**：`video_script` 是"脚本"，不是成片；`card` 是结构化数据，不是设计稿。项目无多模态渲染 API（用户已确认），**渲染层是明确边界**。但"形态派生的产品设计与技术接口已就位"——拿到渲染 API 即可接，不是重新设计。
- **形态不替代本地化**：派生在母稿语言内完成；跨语言靠 `zh_mirror`（中文运营审核镜像）与未来翻译层，不在本设计范围。
- **形态数可配置**：`config.formats` 一处改即增删派生形态，无需动链路——这是"拓扑固定、行为可配"原则在形态层的体现。

---

## 7. 与指标体系的关系

- 消费表现层（§2.3）的"分市场 × 形态下钻"直接吃本矩阵：哪个形态在哪个市场 CTR/完读率最高，反向指导 `format_plan` 权重。
- 成本效率层（§2.4）的"一次生产多形态"摊薄逻辑，是本文档 §1 的工程兑现。
- M3 闭环可基于形态表现数据，让 `FeedbackAnalyst` 提议"KR 市场增配 video_script 权重"——形态策略本身成为可迭代对象。
