"""中文回译镜像：把非中文市场的产出回译成中文，供中文运营做人工审核。

产品判断（为什么不放进供给链路）：
- 回译不是内容生产环节，是「人审辅助」。全球化供给里 90% 的内容不会被人逐条审，
  所以按需生成 + 缓存，比每条都跑一次翻译更省额度；
- 回译只做「对照」不做「改写」：结构、key、数组长度与原文严格对齐，
  保证前端能逐字段配对展示，也保证运营看到的中文和原文是一一对应的，不会被翻译润色掩盖问题。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.llm import get_llm
from app.models import Content
from app.prompts.manager import get_pm

# 只回译"人读得懂才有意义"的字段；style/format_plan 是枚举，回译反而丢失原义
BRIEF_FIELDS = ("topic", "angle", "hook", "audience", "why_now", "avoid", "keywords")


def _align(orig, zh):
    """按原文结构对齐译文：多余的丢弃、缺失的回退原文，保证前端可逐字段配对"""
    if isinstance(orig, str):
        return zh if isinstance(zh, str) and zh.strip() else ""
    if isinstance(orig, list):
        zh = zh if isinstance(zh, list) else []
        return [_align(o, zh[i] if i < len(zh) else None) for i, o in enumerate(orig)]
    if isinstance(orig, dict):
        zh = zh if isinstance(zh, dict) else {}
        return {k: _align(v, zh.get(k)) for k, v in orig.items()}
    return ""  # 数字/布尔无需翻译


def build_source(content: Content) -> dict:
    """抽取需要回译的部分（不含母稿正文——正文有独立的原文阅读场景，回译成本高收益低）"""
    brief = content.brief or {}
    return {
        "title": content.title or "",
        "summary": content.summary or "",
        "brief": {k: brief[k] for k in BRIEF_FIELDS if brief.get(k)},
        "formats": content.formats or {},
    }


async def translate_source(src: dict, market: str, language: str) -> tuple[dict, object]:
    """调用 LLM 生成结构对齐的中文镜像，返回 (mirror, llm_resp)"""
    import json

    from app.llm import extract_json

    system, user = get_pm().render(
        "zh_mirror", market=market, language=language,
        payload=json.dumps(src, ensure_ascii=False, indent=1)[:9000],
    )
    resp = await get_llm().chat(system, user, json_mode=True, temperature=0.2, max_tokens=8000)
    data = extract_json(resp.text)
    zh = data.get("zh") or data.get("translation") or data
    return _align(src, zh), resp


async def ensure_zh_mirror(session, content: Content, *, refresh: bool = False) -> dict:
    """按需生成并缓存中文对照。任何失败都降级为 available=false，不影响原文展示。"""
    if (content.language or "").lower().startswith("zh"):
        return {"available": False, "skipped": True,
                "reason": "该内容目标市场即中文，无需回译", "translation": {}}

    cached = content.translation or {}
    if cached.get("brief") and not refresh:
        return {"available": True, "cached": True, "translation": cached}

    if not get_llm().available:
        return {"available": False, "cached": False,
                "reason": "未配置 LLM API Key，无法生成中文对照", "translation": cached}

    src = build_source(content)
    try:
        mirror, resp = await translate_source(src, content.market, content.language)
    except Exception as e:  # 翻译失败不能拖垮内容详情页
        return {"available": False, "cached": False,
                "reason": f"回译失败：{str(e)[:160]}", "translation": cached}

    mirror.update({
        "lang": "zh",
        "model": getattr(resp, "model", ""),
        "cost_cny": round(getattr(resp, "cost_cny", 0.0), 6),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    content.translation = mirror
    await session.commit()
    return {"available": True, "cached": False, "translation": mirror}
