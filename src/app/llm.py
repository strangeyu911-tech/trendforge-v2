"""LLM 调用层：OpenAI 兼容协议（默认 DeepSeek），指数退避重试 + JSON 容错解析 + 成本统计"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0


class LLMError(Exception):
    pass


class OpenAICompatibleLLM:
    """单一 Provider：base_url/api_key/model 全部走 settings，可指向任何 OpenAI 兼容服务"""

    def __init__(self) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def chat(self, system: str, user: str, *, temperature: float = 0.7,
                   max_tokens: int = 8000, json_mode: bool = False) -> LLMResponse:
        if not self.api_key:
            raise LLMError("LLM API key 未配置")
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        for attempt in range(settings.llm_max_retries):
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    # 4xx（非 429）不重试
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                choice = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage") or {}
                tin = int(usage.get("prompt_tokens", 0))
                tout = int(usage.get("completion_tokens", 0))
                cost = round(tin * settings.llm_price_in / 1e6 + tout * settings.llm_price_out / 1e6, 6)
                return LLMResponse(text=choice.strip(), model=self.model,
                                   tokens_in=tin, tokens_out=tout, cost_cny=cost)
            except LLMError as e:
                last_err = e
                if "HTTP 4" in str(e) and "429" not in str(e):
                    raise
            except Exception as e:  # 网络错误等
                last_err = e
            if attempt < settings.llm_max_retries - 1:
                await asyncio.sleep(min(2 ** attempt * 2, 20))
        raise LLMError(f"LLM 调用失败（重试 {settings.llm_max_retries} 次）: {last_err}")


_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def _salvage_truncated(t: str):
    """修复被 max_tokens 截断的 JSON：砍掉末尾不完整的值，补齐括号"""
    start = -1
    for i, ch in enumerate(t):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return None
    frag = t[start:]
    # 从尾部逐段截断，每次尝试补齐开着的引号与括号
    for cut in range(len(frag), max(len(frag) - 1500, 0), -1):
        part = frag[:cut]
        # 去掉末尾不完整的字符串
        if part.count('"') % 2 == 1:
            part += '"'
        # 去掉悬空的 key/冒号/逗号
        part = part.rstrip()
        while part and part[-1] in ',:"':
            part = part[:-1].rstrip()
        if not part:
            continue
        opens = []
        in_str = False
        esc = False
        for ch in part:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch in "{[":
                    opens.append(ch)
                elif ch in "}]":
                    if opens:
                        opens.pop()
        closing = "".join("}" if o == "{" else "]" for o in reversed(opens))
        try:
            return json.loads(part + closing)
        except Exception:
            continue
    return None


def extract_json(text: str):
    """容错解析 LLM 输出的 JSON：去围栏 → 截取首个括号 → 截断修复"""
    t = text.strip()
    m = _JSON_BLOCK.search(t)
    if m:
        t = m.group(1).strip()
    for i, ch in enumerate(t):
        if ch in "{[":
            for j in range(len(t), i, -1):
                try:
                    return json.loads(t[i:j])
                except Exception:
                    continue
            break
    salvaged = _salvage_truncated(t)
    if salvaged is not None:
        return salvaged
    try:
        return json.loads(t)
    except Exception as e:
        raise LLMError(f"JSON 解析失败: {e}; 原文前200字: {text[:200]}")


_llm: OpenAICompatibleLLM | None = None


def get_llm() -> OpenAICompatibleLLM:
    global _llm
    if _llm is None:
        _llm = OpenAICompatibleLLM()
    return _llm
