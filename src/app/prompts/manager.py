"""Prompt 管理：模板文件化（prompts/templates/*.md），{{var}} 渲染

V1 问题修正：prompt 不再内联在 Agent 代码里，全部外置为可版本化模板。
模板格式：
    ---system---
    系统提示
    ---user---
    用户提示（含 {{var}} 占位）
"""
from __future__ import annotations

import re
from pathlib import Path

TPL_DIR = Path(__file__).resolve().parent / "templates"
_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class PromptManager:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, str]] = {}

    def load(self, name: str) -> tuple[str, str]:
        if name in self._cache:
            return self._cache[name]
        path = TPL_DIR / f"{name}.md"
        raw = path.read_text(encoding="utf-8")
        parts = re.split(r"^---(system|user)---\s*$", raw, flags=re.M)
        system, user = "", raw
        if len(parts) >= 5:
            system = parts[2].strip()
            user = parts[4].strip() if len(parts) > 4 else ""
        self._cache[name] = (system, user)
        return self._cache[name]

    def render(self, name: str, **kwargs) -> tuple[str, str]:
        system, user = self.load(name)

        def sub(m: re.Match) -> str:
            return str(kwargs.get(m.group(1), m.group(0)))

        return _VAR_RE.sub(sub, system), _VAR_RE.sub(sub, user)


_pm: PromptManager | None = None


def get_pm() -> PromptManager:
    global _pm
    if _pm is None:
        _pm = PromptManager()
    return _pm
