"""Distributor 工具：分发决策前真实调用的外部工具。

两个工具：
1. get_market_local_time —— 真实 HTTP 调 worldtimeapi 获取目标市场当前本地时间，
   用于把「9-11am」这类相对时段校正为市场真实本地钟点（避免 LLM 臆测时区）。
2. get_platform_peak_hours —— 平台活跃高峰知识引擎，返回某平台的用户活跃时间窗，
   用于决定分发时间窗（结构化决策知识，由工具提供给 Agent）。

两者都通过 execute_tool_call 真实执行，结果结构化返回（含 ok 标志与耗时），
由 Distributor 写入决策日志与 Trace。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone as dt_timezone

import httpx

# --- OpenAI function-calling schema（供 chat_with_tools / 工具清单使用）---
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_local_time",
            "description": "查询指定 IANA 时区（如 America/New_York、Asia/Tokyo）的当前本地时间，"
                           "用于把分发时段校正为市场真实本地钟点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA 时区名，例如 Asia/Tokyo、America/Sao_Paulo",
                    }
                },
                "required": ["timezone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_platform_peak_hours",
            "description": "查询某内容平台的用户活跃高峰时段，用于决定该渠道的分发时间窗。",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "平台名，如 X / Reddit / 微博 / Hatena Blog / Naver",
                    }
                },
                "required": ["platform"],
            },
        },
    },
]

# --- 平台活跃高峰知识引擎（结构化决策知识，由工具提供给 Agent）---
PEAK_RULES: dict[str, list[str]] = {
    "x": ["08:00-10:00", "12:00-13:00", "18:00-22:00"],
    "twitter": ["08:00-10:00", "12:00-13:00", "18:00-22:00"],
    "reddit": ["09:00-11:00", "19:00-23:00"],
    "hacker news": ["09:00-11:00", "14:00-16:00"],
    "hn": ["09:00-11:00", "14:00-16:00"],
    "dev.to": ["10:00-12:00", "20:00-22:00"],
    "youtube": ["12:00-14:00", "19:00-23:00"],
    "tiktok": ["07:00-09:00", "17:00-21:00"],
    "douyin": ["07:00-09:00", "17:00-21:00"],
    "weibo": ["08:00-09:00", "12:00-13:00", "21:00-23:00"],
    "wechat": ["07:30-09:00", "12:00-13:00", "20:00-22:00"],
    "xiaohongshu": ["08:00-10:00", "12:00-14:00", "19:00-22:00"],
    "hatena blog": ["08:00-09:00", "21:00-23:00"],
    "naver": ["08:00-10:00", "12:00-13:00", "20:00-22:00"],
    "line": ["07:30-09:00", "21:00-23:00"],
    "facebook": ["09:00-11:00", "15:00-17:00"],
    "meta": ["09:00-11:00", "15:00-17:00"],
    "linkedin": ["08:00-10:00", "17:00-19:00"],
    "default": ["08:00-10:00", "12:00-13:00", "19:00-22:00"],
}


async def get_market_local_time(timezone: str) -> dict:
    """真实 HTTP 调 worldtimeapi 获取目标市场当前本地时间。

    失败（超时 / 网络受限）时降级返回 UTC 当前时间并标注 ok=False，
    保证主链路不裸崩、且降级本身也被记入 Trace。
    """
    url = f"http://worldtimeapi.org/api/timezone/{timezone}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                d = r.json()
                return {
                    "ok": True,
                    "timezone": timezone,
                    "local_time": d.get("datetime"),
                    "utc_offset": d.get("utc_offset"),
                }
    except Exception:
        pass
    return {
        "ok": False,
        "fallback": "UTC",
        "local_time": datetime.now(dt_timezone.utc).isoformat(),
        "note": "外部时区服务不可用，已降级为 UTC 当前时间",
    }


def get_platform_peak_hours(platform: str) -> dict:
    """平台活跃高峰知识引擎（纯函数工具）。"""
    key = (platform or "").strip().lower()
    peaks = PEAK_RULES.get(key, PEAK_RULES["default"])
    return {"ok": True, "platform": platform, "peak_hours": peaks}


async def execute_tool_call(name: str, arguments: dict) -> dict:
    """真实执行一个工具调用，返回结构化结果。

    统一入口：Distributor 无论走 native function calling 还是程序化兜底，
    最终都通过本函数真正执行工具。
    """
    arguments = arguments or {}
    if name == "get_market_local_time":
        return await get_market_local_time(str(arguments.get("timezone", "")))
    if name == "get_platform_peak_hours":
        return get_platform_peak_hours(str(arguments.get("platform", "")))
    return {"ok": False, "error": f"未知工具: {name}"}


def format_tool_context(time_result: dict, peak_map: dict[str, dict]) -> str:
    """把工具真实返回格式化为注入 Prompt 的上下文文本。"""
    lines = ["[工具已查询到的真实数据]"]
    if time_result.get("ok"):
        lines.append(
            f"- 市场本地时间：{time_result.get('timezone')} 当前 {time_result.get('local_time')}"
            f"（UTC 偏移 {time_result.get('utc_offset')}）"
        )
    else:
        lines.append(
            f"- 市场本地时间：外部服务不可用（降级 {time_result.get('fallback')}）：{time_result.get('local_time')}"
        )
    lines.append("- 各平台活跃高峰（来自平台峰值知识引擎）：")
    for plat, res in peak_map.items():
        peaks = res.get("peak_hours", [])
        lines.append(f"  · {plat}: {', '.join(peaks)}")
    lines.append("请基于以上真实数据给出分发时段，不要臆测时区或平台活跃时间。")
    return "\n".join(lines)
