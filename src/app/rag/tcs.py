"""TCS（Topic Consistency Score）主题一致性度量

为什么需要它 —— 2026-08-06 真实运行证据：
5 条 Editor reject 中有 3 条发生在 Researcher 反漂移防护上线**之后**，症状完全一致
——「后续大量篇幅为电竞 / 百度 / 中超等无关新闻报道」，即拼盘稿。根因不在于没防，
而在于唯一的漂移判据是 Editor（链路末端的 LLM 主观判断）：
  · 发现时整条已烧掉约 ¥0.28 与 11 分钟，只能整篇废掉，无法定点修复
  · 判据是自然语言评语，不可复现、不可度量、无法进指标体系

TCS 把「主题漂移」从主观判断变成可计算量。核心信号是稿件里**已经存在的
[ev_xxx] 引用结构**，不需要额外的模型调用：

  main_ratio   主干文档引用数 / 总引用数   —— 主线是否成立
  cross_docs   被引用的背景文档篇数        —— 是否退化成拼盘
  lexical      小节与「选题+角度」的 BM25 归一化词面分（跨语言时自动停用）

三者全部本地计算：**零 token 成本、可解释、语言无关**（引用结构不依赖语种，
这点很关键——JP/KR/BR 稿件与英文 KB 之间词面匹配本就失效）。
"""
from __future__ import annotations

import re

from app.config import settings
from app.rag.bm25 import BM25

_EV_RE = re.compile(r"\[(ev_\d+)\]")
# 词面闸可用性门槛：正文对「选题」的 BM25 峰值低于此值说明词面匹配整体失效
# （典型场景：日文稿 vs 英文选题词），此时只用引用结构判定，避免误杀。
LEXICAL_USABLE_MIN = 1.0
LEXICAL_SECTION_MIN = 0.15  # 归一化后单节词面分下限


def _cites(text: str) -> list[str]:
    return _EV_RE.findall(text or "")


def _main_ev_ids(evidences: list[dict]) -> set[str]:
    ids = {e["ev_id"] for e in evidences if e.get("is_main") and e.get("ev_id")}
    if ids or not evidences:
        return ids
    # 兼容未标 is_main 的历史数据：以首条证据所属文档为主干
    main_doc = evidences[0].get("doc_title")
    return {e["ev_id"] for e in evidences
            if e.get("doc_title") == main_doc and e.get("ev_id")}


def score_article(article: dict, brief: dict, evidences: list[dict]) -> dict:
    """给母稿打主题一致性分。返回可直接落库的报告 dict。"""
    sections = ((article or {}).get("body") or {}).get("sections") or []
    evidences = evidences or []
    ev_by_id = {e.get("ev_id"): e for e in evidences}
    main_ids = _main_ev_ids(evidences)

    # 词面一致性（辅助闸）：以小节集合为语料，归一化到 0-1 便于跨稿件比较
    topic_q = f"{(brief or {}).get('topic', '')} {(brief or {}).get('angle', '')}".strip()
    texts = [_EV_RE.sub("", s.get("text", "")) for s in sections]
    lex: list[float] = [0.0] * len(sections)
    lexical_usable = False
    if texts and topic_q:
        raw = BM25().fit(texts).scores(topic_q)
        peak = max(raw) if raw else 0.0
        lexical_usable = peak >= LEXICAL_USABLE_MIN
        if peak > 0:
            lex = [round(r / peak, 3) for r in raw]

    total_c = main_c = 0
    other_docs: set[str] = set()
    rows: list[dict] = []
    for i, s in enumerate(sections):
        cited = _cites(s.get("text", ""))
        mine = [c for c in cited if c in main_ids]
        total_c += len(cited)
        main_c += len(mine)
        for c in cited:
            if c in main_ids:
                continue
            doc = (ev_by_id.get(c) or {}).get("doc_title")
            if doc:
                other_docs.add(doc)
        # 判漂移：整节只引用背景文档 = 典型「每条证据写一节」的拼盘节；
        # 词面闸可用时，额外拦截「引用了主干但内容已跑偏」的情况。
        drift = bool(cited) and not mine
        if lexical_usable and cited and lex[i] < LEXICAL_SECTION_MIN:
            drift = True
        rows.append({
            "idx": i, "heading": s.get("heading", ""), "cites": len(cited),
            "main_cites": len(mine), "lexical": lex[i], "drift": drift,
        })

    main_ratio = round(main_c / total_c, 3) if total_c else 0.0
    cross_docs = len(other_docs)
    drift_idx = [r["idx"] for r in rows if r["drift"]]
    cap = settings.tcs_cross_doc_max + 1
    tcs = round(0.6 * main_ratio + 0.4 * max(0.0, 1 - min(cross_docs, cap) / cap), 3)
    passed = (
        total_c > 0
        and main_ratio >= settings.tcs_main_ratio_min
        and cross_docs <= settings.tcs_cross_doc_max
        and not drift_idx
    )
    return {
        "tcs": tcs, "passed": passed,
        "main_ratio": main_ratio, "cross_docs": cross_docs,
        "citations": total_c, "main_citations": main_c,
        "lexical_usable": lexical_usable,
        "drift_sections": drift_idx,
        "sections": rows,
        "reason": _reason(passed, main_ratio, cross_docs, drift_idx, total_c),
    }


def _reason(passed: bool, main_ratio: float, cross_docs: int,
            drift_idx: list[int], total_c: int) -> str:
    if passed:
        return f"主线成立（主干引用占比 {main_ratio:.0%}，背景文档 {cross_docs} 篇）"
    bad = []
    if total_c == 0:
        bad.append("全文无证据引用")
    if total_c and main_ratio < settings.tcs_main_ratio_min:
        bad.append(f"主干引用占比 {main_ratio:.0%} < {settings.tcs_main_ratio_min:.0%}")
    if cross_docs > settings.tcs_cross_doc_max:
        bad.append(f"跨 {cross_docs} 篇背景文档（上限 {settings.tcs_cross_doc_max}）")
    if drift_idx:
        bad.append(f"第 {[i + 1 for i in drift_idx]} 节脱离主线")
    return "；".join(bad)


def drop_drift_sections(article: dict, drift_idx: list[int], min_keep: int = 2) -> dict:
    """删节兜底：重写仍不达标时直接摘除漂移小节。

    宁可稿件短、信息量小，也不交出拼盘稿——拼盘正是要防的漂移形态本身。
    保底留 min_keep 节（保留主干引用最多的），避免结构崩塌。
    """
    sections = ((article or {}).get("body") or {}).get("sections") or []
    keep = [s for i, s in enumerate(sections) if i not in set(drift_idx)]
    if len(keep) < min_keep:
        extra = [sections[i] for i in drift_idx][: min_keep - len(keep)]
        keep = keep + extra
    out = dict(article or {})
    out["body"] = {**(article or {}).get("body", {}), "sections": keep}
    return out
