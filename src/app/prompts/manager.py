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
        # M3 闭环：adopted PromptRecord 覆盖层（name -> (system, user, version)）
        self._overrides: dict[str, tuple[str, str, str]] = {}

    def load(self, name: str) -> tuple[str, str]:
        if name in self._overrides:
            sys_, usr, _ = self._overrides[name]
            return sys_, usr
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

    # ---- M3 覆盖层 API ----
    @staticmethod
    def parse_template(content: str) -> tuple[str, str]:
        """把含 ---system---/---user--- 分隔的完整 prompt 文本拆成 (system, user)"""
        parts = re.split(r"^---(system|user)---\s*$", content, flags=re.M)
        system, user = "", content
        if len(parts) >= 5:
            system = parts[2].strip()
            user = parts[4].strip() if len(parts) > 4 else ""
        return system, user

    def set_override(self, name: str, content: str, version: str = "v?") -> None:
        self._overrides[name] = (*self.parse_template(content), version)

    def clear_override(self, name: str) -> None:
        self._overrides.pop(name, None)

    def clear_overrides(self) -> None:
        self._overrides.clear()

    def active_version(self, name: str) -> str:
        """当前生效版本号（供审计链 ctx.prompt_versions 记录）。未覆盖返回 'v1'(文件基线)。"""
        if name in self._overrides:
            return self._overrides[name][2]
        return "v1"


_pm: PromptManager | None = None


def get_pm() -> PromptManager:
    global _pm
    if _pm is None:
        _pm = PromptManager()
    return _pm
