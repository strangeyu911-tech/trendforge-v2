"""中文文案字典（后端唯一口径）。

背景：Trace Tab、进度文案、失败案例库会把后端的机器标识（agent 名、裁决枚举、
形态枚举、平台枚举）直接渲染给运营看，过去是规则地一处处手写中文，漏一处就
泄一个英文 token（feedback_analyst / task_spans / pass …）。

这里把「机器标识 → 读者可读中文」收敛成一张表，凡是面向读者的拼装文案
（decision_reason / warnings / progress / bad_case 根因与修复动作）一律过一遍。

注意：不翻译的地方
  · 专有名词：DeepSeek / GitHub / Hacker News / Dev.to / GDELT / LINE / X / 平台名；
  · 版本号、市场代码（US / JP / BR）——它们是标识符，不是英文单词。

前端 ui/console/assets/app.js 里的 LABEL 表与本文件是同一口径的镜像，
改中文名时两边必须同步（否则同一 Agent 会出现两套中文名）。
"""
from __future__ import annotations

# Agent 代号（task_spans.agent / prompt 模板名）→ 中文名
AGENT_CN: dict[str, str] = {
    # 主管线 11 个
    "signal_scout": "信号捕捉",
    "trend_analyst": "趋势研判",
    "audience_insight": "受众洞察",
    "angle_editor": "角度设计",
    "researcher": "证据检索",
    "writer": "写作",
    "topic_guard": "选题守卫",
    "fact_checker": "事实核查",
    "editor": "总编审核",
    "format_adapter": "形态适配",
    "distributor": "分发策略",
    # 治理 / 分析链路
    "kb_curator": "知识库治理",
    "feedback_analyst": "反馈分析",
    "zh_mirror": "中文对照",
    # 守卫类（质量 Tab / 证据链会暴露）
    "evidence_guard": "证据守卫",
    "language_guard": "语言守卫",
}

# 质量裁决枚举 → 中文
VERDICT_CN: dict[str, str] = {
    "pass": "通过", "publish": "可发布",
    "revise": "需修改", "reject": "不通过",
}

# 内容形态枚举 → 中文
FORMAT_CN: dict[str, str] = {
    "article": "母稿", "video_script": "短视频脚本", "card": "资讯摘要卡片",
    "brief_news": "快讯", "comment": "评论区引导",
}

# 分发平台枚举 → 中文（专有平台名保留原名）
PLATFORM_CN: dict[str, str] = {
    "x": "X", "linkedin": "LinkedIn", "youtube_shorts": "YouTube 短视频",
    "youtube": "YouTube", "line": "LINE", "yahoo_news": "雅虎新闻", "naver": "Naver",
    "instagram": "Instagram", "whatsapp": "WhatsApp", "kwai": "快手",
    "wechat": "微信公众号", "weibo": "微博", "douyin": "抖音", "substack": "Substack",
    "reddit": "Reddit", "tiktok": "TikTok",
}

# 任务类型枚举 → 中文
KIND_CN: dict[str, str] = {
    "pipeline": "内容供给", "ab": "A/B 对比", "feedback": "反馈分析",
    "revise": "按意见重写", "kb_curate": "知识库策展",
}


def agent_cn(code: str | None) -> str:
    """Agent 代号 → 中文名；未登记的代号原样返回（不吞信息）。"""
    k = str(code or "")
    return AGENT_CN.get(k, k)


def verdict_cn(v: str | None) -> str:
    k = str(v or "")
    return VERDICT_CN.get(k, k)


def format_cn(f: str | None) -> str:
    k = str(f or "")
    return FORMAT_CN.get(k, k)


def platform_cn(p: str | None) -> str:
    k = str(p or "")
    return PLATFORM_CN.get(k, k)


def kind_cn(k: str | None) -> str:
    key = str(k or "")
    return KIND_CN.get(key, key)


def formats_cn(keys) -> str:
    """[article, video_script] → 母稿、短视频脚本"""
    return "、".join(format_cn(k) for k in keys)
