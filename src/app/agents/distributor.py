"""10. Distributor 分发策略：平台×形态×受众×时段 分发计划

工具增强型 Agent：在生成分发计划前，先真实调用外部工具获取
- 目标市场当前本地时间（HTTP 调 worldtimeapi）
- 各平台活跃高峰（平台峰值知识引擎）
工具结果注入上下文，LLM 必须基于真实数据决策；调用往返全程进决策日志与 Trace。

双轨：
- 若 LLM 支持 function calling（如 deepseek-chat），首轮让模型自主决定调哪些工具，执行后回灌收尾。
- 若模型不触发 tool_calls（推理模型常见）或首轮失败，程序化兜底真实调用全部工具，保证工具一定被真实触发。
"""
from __future__ import annotations

import json
import time

from app.agents.base import AgentError, BaseAgent, RunContext, clean_ev
from app.agents.tools import (
    TOOL_SCHEMAS,
    execute_tool_call,
    format_tool_context,
)
from app.llm import extract_json
from app.prompts.manager import get_pm


def _int(v, d: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


class DistributorAgent(BaseAgent):
    name = "distributor"
    prompt_name = "distributor"

    async def run(self, ctx: RunContext, inputs: dict) -> dict:
        article, brief, formats = inputs["article"], inputs["brief"], inputs["formats"]
        m = ctx.market
        platforms = list((m.platforms or {}).keys())

        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, language=m.language,
            platforms=json.dumps(m.platforms, ensure_ascii=False),
            title=clean_ev(article["title"]),
            audience=brief.get("audience", ""),
            available_formats=json.dumps(list(formats.keys()), ensure_ascii=False),
            tool_context="（工具尚未查询，请稍候）" if False else "",
        )

        # 1) 尝试 native function calling：让 LLM 自主决定调用哪些工具
        tool_calls: list[dict] = []
        time_result: dict | None = None
        peak_map: dict[str, dict] = {}

        native_tc = await self._try_native_tools(ctx, system, user)
        if native_tc:
            tool_calls = native_tc
            # 从 native tool_calls 结果里回收时间/平台数据
            for tc in native_tc:
                if tc["tool"] == "get_market_local_time":
                    time_result = tc.get("result")
                elif tc["tool"] == "get_platform_peak_hours":
                    peak_map[tc["args"].get("platform", "")] = tc.get("result", {})

        # 2) 程序化兜底：保证工具一定被真实调用（推理模型不触发 tool_calls 时尤其重要）
        #    只补调 LLM 没覆盖到的部分，避免重复调用。
        if time_result is None:
            t0 = time.time()
            time_result = await execute_tool_call("get_market_local_time", {"timezone": m.timezone})
            tool_calls.append({
                "tool": "get_market_local_time",
                "args": {"timezone": m.timezone},
                "result": time_result,
                "ms": int((time.time() - t0) * 1000),
            })
        for plat in platforms:
            if plat not in peak_map:
                t0 = time.time()
                res = await execute_tool_call("get_platform_peak_hours", {"platform": plat})
                peak_map[plat] = res
                tool_calls.append({
                    "tool": "get_platform_peak_hours",
                    "args": {"platform": plat},
                    "result": res,
                    "ms": int((time.time() - t0) * 1000),
                })

        # 3) 用工具真实结果注入上下文，生成最终分发 JSON
        tool_context = format_tool_context(time_result or {}, peak_map)
        system, user = get_pm().render(
            self.prompt_name,
            market=m.name, language=m.language,
            platforms=json.dumps(m.platforms, ensure_ascii=False),
            title=clean_ev(article["title"]),
            audience=brief.get("audience", ""),
            available_formats=json.dumps(list(formats.keys()), ensure_ascii=False),
            tool_context=tool_context,
        )
        resp = await ctx.llm.chat(system, user, json_mode=True)
        data = extract_json(resp.text)
        plan = data.get("plan") or []
        if not isinstance(plan, list) or not plan:
            raise AgentError(self.name, "分发计划为空")
        norm = [{
            "platform": str(p.get("platform", "")),
            "format": str(p.get("format", "article")),
            "audience": str(p.get("audience", brief.get("audience", ""))),
            "timing": str(p.get("timing", "")),
            "reason": str(p.get("reason", "")),
            "priority": _int(p.get("priority"), 2),
        } for p in plan]
        norm.sort(key=lambda p: p["priority"])
        return {
            "distribution": {"plan": norm},
            "_llm_resp": resp,
            "_tool_calls": tool_calls,
            "_decision": {
                "reason": f"调用 {len(tool_calls)} 个真实工具（本地时间/平台高峰）后，"
                          f"制定 {len(norm)} 条分发计划，主发 {norm[0]['platform']}（{norm[0]['format']}）",
                "details": {"primary": norm[0], "tool_calls": tool_calls},
            },
        }

    async def _try_native_tools(self, ctx: RunContext, system: str, user: str) -> list[dict]:
        """尝试让 LLM 自主决定调用工具（native function calling）。

        返回结构化 tool_calls 列表；若模型不支持/未触发/出错，返回空列表，由调用方走兜底。
        """
        try:
            resp = await ctx.llm.chat_with_tools(system, user, TOOL_SCHEMAS)
        except Exception:
            return []
        if not resp.tool_calls:
            return []

        executed: list[dict] = []
        tool_msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        raw_tc = []
        for tc in resp.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {}) or {}
            t0 = time.time()
            result = await execute_tool_call(name, args)
            ms = int((time.time() - t0) * 1000)
            executed.append({"tool": name, "args": args, "result": result, "ms": ms})
            raw_tc.append({
                "id": tc.get("id"), "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            })
            tool_msgs.append({"role": "assistant", "content": None, "tool_calls": raw_tc[-1:]})
            tool_msgs.append({
                "role": "tool", "tool_call_id": tc.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            })
        # 回灌工具结果，让模型收尾（不强制 json_mode，结果仍可 extract_json）
        try:
            await ctx.llm.chat_with_tools(
                system, user, TOOL_SCHEMAS, tool_messages=tool_msgs, json_mode=True
            )
        except Exception:
            pass
        return executed

    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict:
        m = ctx.market
        formats = list((inputs.get("formats") or {}).keys()) or ["article"]
        plan = []
        for i, (plat, spec) in enumerate((m.platforms or {}).items()):
            fmt = next((f for f in (spec.get("formats") or []) if f in formats), formats[0])
            plan.append({
                "platform": plat, "format": fmt,
                "audience": spec.get("audience", ""), "timing": spec.get("peak", "全天"),
                "reason": "市场档案默认平台偏好（兜底；未调用工具）", "priority": i + 1,
            })
        return {"distribution": {"plan": plan},
                "_decision": {"reason": f"兜底：按市场档案默认平台生成 {len(plan)} 条计划（工具调用失败）"}}
