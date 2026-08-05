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


# 分发计划里平台/形态是系统枚举（靠前端标签映射，不回译）；受众/时段/理由是自由文本才回译
DIST_TEXT_FIELDS = ("audience", "timing", "reason")
# 质量里要回译的自由文本字段；verdict 是枚举（前端标签映射），scores/fact_check 是数字无需回译
QUALITY_TEXT_FIELDS = ("comments", "revision_advice", "compliance_hits")


def build_source(content: Content) -> dict:
    """抽取需要回译的部分（不含母稿正文——正文有独立的原文阅读场景，回译成本高收益低）"""
    brief = content.brief or {}
    dist = content.distribution or {}
    plan = dist.get("plan") or []
    dist_src = {"plan": [
        {k: p.get(k) for k in DIST_TEXT_FIELDS if p.get(k)}
        for p in plan
    ]} if plan else {}
    quality = content.quality or {}
    q_src = {k: quality[k] for k in QUALITY_TEXT_FIELDS if quality.get(k)}
    return {
        "title": content.title or "",
        "summary": content.summary or "",
        "brief": {k: brief[k] for k in BRIEF_FIELDS if brief.get(k)},
        "formats": content.formats or {},
        "distribution": dist_src,
        "quality": q_src,
    }


async def _translate_chunk(part: dict, market: str, language: str) -> tuple[dict, object]:
    """翻译单个分片。分片而非整包，是因为推理模型的 reasoning token 与正文共用
    max_tokens 预算——整包翻译时 reasoning 会把预算吃光，正文返回空串。"""
    import json

    from app.llm import LLMError, extract_json

    system, user = get_pm().render(
        "zh_mirror", market=market, language=language,
        payload=json.dumps(part, ensure_ascii=False, indent=1)[:4500],
    )
    resp = await get_llm().chat(system, user, json_mode=True, temperature=0.2, max_tokens=12000)
    if not (resp.text or "").strip():
        raise LLMError("模型返回空正文（reasoning token 可能占满了预算）")
    data = extract_json(resp.text)
    zh = data.get("zh") or data.get("translation") or data
    return _align(part, zh), resp


def _split(src: dict) -> list[tuple[str, dict]]:
    """切片：文案头+简报一片，每个形态各一片。单片小 → 输出稳定、失败可隔离"""
    parts: list[tuple[str, dict]] = []
    head = {k: src[k] for k in ("title", "summary", "brief") if src.get(k)}
    if head:
        parts.append(("head", head))
    for name, body in (src.get("formats") or {}).items():
        if body:
            parts.append((f"formats.{name}", {"formats": {name: body}}))
    if src.get("distribution"):
        parts.append(("distribution", {"distribution": src["distribution"]}))
    if src.get("quality"):
        parts.append(("quality", {"quality": src["quality"]}))
    return parts


async def translate_source(src: dict, market: str, language: str) -> tuple[dict, list]:
    """分片并发生成结构对齐的中文镜像，返回 (mirror, [llm_resp...])。

    只要有一片成功就返回结果；失败的片由 _align 回落成空串，
    前端对应字段不显示中文行，但原文照常展示。
    """
    import asyncio

    from app.llm import LLMError

    parts = _split(src)
    if not parts:
        raise LLMError("没有可回译的内容")

    results = await asyncio.gather(
        *(_translate_chunk(p, market, language) for _, p in parts),
        return_exceptions=True,
    )

    mirror = _align(src, {})  # 先铺一层与原文同构的空壳
    resps, errors, ok = [], [], 0
    for (label, _), r in zip(parts, results):
        if isinstance(r, BaseException):
            errors.append(f"{label}: {str(r)[:80]}")
            continue
        chunk, resp = r
        ok += 1
        resps.append(resp)
        for k, v in chunk.items():
            if k == "formats":
                mirror.setdefault("formats", {}).update(v)
            else:
                mirror[k] = v

    if ok == 0:
        raise LLMError("；".join(errors) or "全部分片翻译失败")
    if errors:
        mirror["_partial"] = errors
    return mirror, resps


async def ensure_zh_mirror(session, content: Content, *, refresh: bool = False) -> dict:
    """按需生成并缓存中文对照。任何失败都降级为 available=false，不影响原文展示。"""
    if (content.language or "").lower().startswith("zh"):
        return {"available": False, "skipped": True,
                "reason": "该内容目标市场即中文，无需回译", "translation": {}}

    cached = content.translation or {}
    # 旧缓存可能只含 brief/formats，缺分发计划/质量对照；要求齐全才命中缓存
    if cached.get("brief") and cached.get("distribution") is not None and cached.get("quality") is not None and not refresh:
        return {"available": True, "cached": True, "translation": cached}

    if not get_llm().available:
        return {"available": False, "cached": False,
                "reason": "未配置 LLM API Key，无法生成中文对照", "translation": cached}

    src = build_source(content)
    try:
        mirror, resps = await translate_source(src, content.market, content.language)
    except Exception as e:  # 翻译失败不能拖垮内容详情页
        return {"available": False, "cached": False,
                "reason": f"回译失败：{str(e)[:160]}", "translation": cached}

    partial = mirror.pop("_partial", None)
    mirror.update({
        "lang": "zh",
        "model": getattr(resps[0], "model", "") if resps else "",
        "cost_cny": round(sum(getattr(r, "cost_cny", 0.0) for r in resps), 6),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    content.translation = mirror
    await session.commit()
    out = {"available": True, "cached": False, "translation": mirror}
    if partial:
        out["reason"] = f"部分字段回译失败（{len(partial)} 处），已保留原文"
    return out
