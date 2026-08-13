"""Agent 工具层：Distributor 在决策前可真实调用的外部工具集合。

设计意图（对应 JD「工具调用设计」）：
- 工具是 Agent 能力的真实外延，不是 Prompt 里的文字，而是会被真实执行的调用。
- 每个工具有 OpenAI function-calling schema（TOOL_SCHEMAS）+ 真实执行函数（execute_tool_call）。
- 调用入参 / 出参 / 耗时全部进入决策日志与 Trace，面试官在控制台可直接看到。
"""
from app.agents.tools.distribution_tools import (
    TOOL_SCHEMAS,
    execute_tool_call,
    get_market_local_time,
    get_platform_peak_hours,
    format_tool_context,
)

__all__ = [
    "TOOL_SCHEMAS",
    "execute_tool_call",
    "get_market_local_time",
    "get_platform_peak_hours",
    "format_tool_context",
]
