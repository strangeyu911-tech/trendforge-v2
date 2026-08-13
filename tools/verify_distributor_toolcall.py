"""离线验证 Distributor 的工具调用链路（无需 API key）。

用 FakeLLM 替换真实 LLM，验证两件事：
A. 程序化兜底路径：模型不触发 tool_calls 时，Distributor 仍真实调用全部工具，
   工具结果注入上下文，最终生成 plan；tool_calls 进入决策日志与 Span。
B. 原生 function calling 路径：模型返回 tool_calls 时，Distributor 执行工具并回灌。

运行：在 src/ 目录下用 python 执行本脚本（会自动把 src 加入 sys.path）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from app.agents.distributor import DistributorAgent
from app.agents.base import RunContext
from app.agents.tools.distribution_tools import (
    get_market_local_time,
    get_platform_peak_hours,
)
from app.llm import LLMResponse


# ---------- Fake 基础设施 ----------
class FakeSession:
    async def commit(self):
        pass

    async def add(self, *a, **k):
        pass


class FakeTask:
    progress = ""
    total_duration_ms = 0
    total_cost_cny = 0.0
    review_rounds = 0
    prompt_versions = {}
    decision_log = {}


class FakeMarket:
    def __init__(self):
        self.code = "JP"
        self.name = "日本"
        self.language = "ja"
        self.timezone = "Asia/Tokyo"
        self.platforms = {
            "X": {"formats": ["article", "comment"], "audience": "科技爱好者", "peak": "19:00-22:00"},
            "Hatena Blog": {"formats": ["article"], "audience": "深度读者", "peak": "21:00-23:00"},
        }


def make_ctx(llm) -> RunContext:
    return RunContext(
        task_id="verify-001",
        session=FakeSession(),
        llm=llm,
        task=FakeTask(),
        market=FakeMarket(),
        spans=[],
        decision_log={},
        prompt_versions={},
        review_rounds=0,
    )


# ---------- 两种 FakeLLM ----------
PLAN_JSON = json.dumps({
    "plan": [
        {"platform": "X", "format": "article", "audience": "科技爱好者",
         "timing": "基于东京本地时间 19:00-22:00", "reason": "工具显示 X 高峰 18-22 点", "priority": 1},
        {"platform": "Hatena Blog", "format": "article", "audience": "深度读者",
         "timing": "基于东京本地时间 21:00-23:00", "reason": "工具显示 Hatena 高峰 21-23 点", "priority": 2},
    ]
}, ensure_ascii=False)


class ProgrammaticFallbackLLM:
    """模拟推理模型：不支持 tool_calls，chat_with_tools 返回空。"""
    available = True

    async def chat(self, system, user, *, temperature=0.7, max_tokens=8000, json_mode=False):
        return LLMResponse(text=PLAN_JSON, model="fake-reasoner")

    async def chat_with_tools(self, system, user, tools, *, tool_messages=None,
                              temperature=0.7, max_tokens=8000, json_mode=False):
        return LLMResponse(text="", model="fake-reasoner", tool_calls=[], finish_reason="stop")


class NativeToolCallLLM:
    """模拟支持 function calling 的模型：首轮返回 tool_calls，次轮返回 JSON。"""
    available = True
    _calls = 0

    async def chat(self, system, user, *, temperature=0.7, max_tokens=8000, json_mode=False):
        return LLMResponse(text=PLAN_JSON, model="fake-chat")

    async def chat_with_tools(self, system, user, tools, *, tool_messages=None,
                              temperature=0.7, max_tokens=8000, json_mode=False):
        self._calls += 1
        if tool_messages is None:
            # 首轮：模型决定调用两个工具
            return LLMResponse(
                text="", model="fake-chat",
                tool_calls=[
                    {"id": "c1", "name": "get_market_local_time", "arguments": {"timezone": "Asia/Tokyo"}},
                    {"id": "c2", "name": "get_platform_peak_hours", "arguments": {"platform": "X"}},
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(text=PLAN_JSON, model="fake-chat", tool_calls=[], finish_reason="stop")


async def run_one(label, llm):
    ctx = make_ctx(llm)
    result = await DistributorAgent()._exec(ctx, {
        "article": {"title": "日本 AI 监管新动向"},
        "brief": {"audience": "科技爱好者"},
        "formats": {"article": {}, "comment": {}},
    })
    tc = ctx.decision_log.get("distributor", {}).get("details", {}).get("tool_calls", [])
    span_tc = [s.tool_calls for s in ctx.spans if s.agent == "distributor"]
    plan = result.get("distribution", {}).get("plan", [])
    print(f"\n=== {label} ===")
    print(f"  工具调用次数: {len(tc)}")
    for t in tc:
        print(f"   - {t['tool']}({t['args']}) -> ok={t['result'].get('ok')}  ({t['ms']}ms)")
    print(f"  Span.tool_calls 已记录: {bool(span_tc and span_tc[0])}")
    print(f"  生成 plan 条数: {len(plan)}  主发: {plan[0]['platform'] if plan else '无'}")
    assert tc, f"[{label}] 工具未被调用！"
    assert plan, f"[{label}] plan 为空！"
    assert span_tc and span_tc[0], f"[{label}] Span 未记录 tool_calls！"
    # 程序化兜底/原生路径都至少调用了本地时间工具
    assert any(t["tool"] == "get_market_local_time" for t in tc), f"[{label}] 缺少本地时间工具"
    return tc


async def main():
    # A. 程序化兜底路径（推理模型）
    tc_a = await run_one("A. 程序化兜底（推理模型不触发 tool_calls）", ProgrammaticFallbackLLM())
    assert any(t["tool"] == "get_platform_peak_hours" for t in tc_a), "A 缺少平台高峰工具"

    # B. 原生 function calling 路径
    tc_b = await run_one("B. 原生 function calling（模型自主决定调工具）", NativeToolCallLLM())
    assert any(t["tool"] == "get_platform_peak_hours" and t["args"].get("platform") == "X" for t in tc_b), \
        "B 原生路径未执行平台高峰工具"

    # 直接验证真实工具函数（含 HTTP 真实调用 / 降级）
    t_res = await get_market_local_time("Asia/Tokyo")
    p_res = get_platform_peak_hours("X")
    print(f"\n=== 真实工具函数直测 ===")
    print(f"  get_market_local_time -> ok={t_res.get('ok')}  local={t_res.get('local_time')}")
    print(f"  get_platform_peak_hours(X) -> {p_res.get('peak_hours')}")

    print("\n✅ 全部断言通过：Distributor 工具调用链路（双轨）验证通过。")


if __name__ == "__main__":
    asyncio.run(main())
