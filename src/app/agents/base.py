"""Agent 基类：RunContext / Span / 决策日志 / 统一降级 / ev 引用清洗

V1 踩坑复用：任何消费母稿 body 的派生内容（多形态、摘要），喂 LLM 前与输出后
都必须剔除 [ev_xxx] 溯源标记，对外内容绝不允许出现该标记。
"""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import OpenAICompatibleLLM
from app.models import Market, Task, TaskSpan

EV_RE = re.compile(r"\s*\[ev_\d+\]")


def clean_ev(text: Any) -> Any:
    """递归剔除字符串中的 [ev_xxx] 引用标记（对外/派生内容专用）"""
    if isinstance(text, str):
        return EV_RE.sub("", text)
    if isinstance(text, list):
        return [clean_ev(x) for x in text]
    if isinstance(text, dict):
        return {k: clean_ev(v) for k, v in text.items()}
    return text


@dataclass
class Span:
    agent: str
    status: str = "ok"  # ok|degraded|failed
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    decision_reason: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RunContext:
    task_id: str
    session: AsyncSession
    llm: OpenAICompatibleLLM
    task: Task
    market: Market
    spans: list[Span] = field(default_factory=list)
    decision_log: dict[str, dict] = field(default_factory=dict)
    prompt_versions: dict[str, str] = field(default_factory=dict)
    review_rounds: int = 0

    @property
    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.spans)

    @property
    def total_cost_cny(self) -> float:
        return round(sum(s.cost_cny for s in self.spans), 4)

    def log_decision(self, agent: str, reason: str, **details) -> None:
        self.decision_log[agent] = {"reason": reason, "details": details}

    async def persist(self) -> None:
        for s in self.spans:
            self.session.add(TaskSpan(
                task_id=self.task_id, agent=s.agent, status=s.status, model=s.model,
                tokens_in=s.tokens_in, tokens_out=s.tokens_out, cost_cny=s.cost_cny,
                duration_ms=s.duration_ms, warnings=s.warnings,
                decision_reason=s.decision_reason, started_at=s.started_at,
            ))
        self.task.total_duration_ms = self.total_duration_ms
        self.task.total_cost_cny = self.total_cost_cny
        self.task.review_rounds = self.review_rounds
        self.task.prompt_versions = self.prompt_versions
        self.task.decision_log = self.decision_log


class AgentError(Exception):
    def __init__(self, agent: str, message: str):
        self.agent = agent
        super().__init__(f"[{agent}] {message}")


class BaseAgent(ABC):
    name: str = "base"
    prompt_name: str = ""  # prompts/templates/<name>.md

    @abstractmethod
    async def run(self, ctx: RunContext, inputs: dict) -> dict: ...

    @abstractmethod
    async def fallback(self, ctx: RunContext, error: AgentError, inputs: dict) -> dict: ...

    async def _exec(self, ctx: RunContext, inputs: dict) -> dict:
        """执行 run：自动记录 Span + 决策日志 + 异常降级（主链路永不裸崩）"""
        ctx.task.progress = self.name
        t0 = time.time()
        span = Span(agent=self.name, started_at=datetime.fromtimestamp(t0))
        try:
            result = await self.run(ctx, inputs)
            llm_resp = result.pop("_llm_resp", None) if isinstance(result, dict) else None
            if llm_resp:
                span.model = llm_resp.model
                span.tokens_in = llm_resp.tokens_in
                span.tokens_out = llm_resp.tokens_out
                span.cost_cny = llm_resp.cost_cny
            if isinstance(result, dict) and result.get("_decision"):
                dec = result["_decision"]
                ctx.log_decision(self.name, dec.get("reason", ""), **dec.get("details", {}))
                span.decision_reason = dec.get("reason", "")
            if isinstance(result, dict) and result.get("_warnings"):
                span.warnings = result.pop("_warnings")
                span.status = "degraded"
            if self.prompt_name:
                ctx.prompt_versions[self.name] = f"{self.prompt_name}@v1"
        except Exception as e:
            err = e if isinstance(e, AgentError) else AgentError(self.name, str(e))
            span.status = "degraded"
            span.warnings = [f"{self.name} 使用规则兜底: {str(err)[:120]}"]
            span.decision_reason = f"fallback: {err}"
            ctx.log_decision(self.name, f"降级兜底: {err}")
            result = await self.fallback(ctx, err, inputs)
        span.duration_ms = int((time.time() - t0) * 1000)
        ctx.spans.append(span)
        return result
