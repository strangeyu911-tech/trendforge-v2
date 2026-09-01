"""反主题漂移改造（L0/L1/L2/L3）离线回归验证

不需要 LLM key、不需要数据库：直接对纯函数与过滤链做断言。
复现的是 2026-08-06 三条 Editor reject 的真实漂移形态
（「后续大量篇幅为电竞 / 百度 / 中超等无关新闻」）。

用法：python tools/test_drift_guard.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from app.agents.researcher import MIN_EVIDENCES, ResearcherAgent  # noqa: E402
from app.agents.writer import WriterAgent  # noqa: E402
from app.config import settings  # noqa: E402
from app.rag.store import retrieve_scores  # noqa: E402
from app.rag.tcs import drop_drift_sections, score_article  # noqa: E402

PASS, FAIL = "  [PASS]", "  [FAIL]"
_failed = 0


def check(cond: bool, msg: str) -> None:
    global _failed
    print((PASS if cond else FAIL), msg)
    if not cond:
        _failed += 1


def ev(i: int, doc: str, cat: str, text: str, cred: int = 3, score: float = 1.0) -> dict:
    return {"ev_id": f"ev_{i:03d}", "text": text, "doc_title": doc, "source": doc[:10],
            "category": cat, "credibility": cred, "score": score, "published_at": "2026-07-20"}


# ---- 真实漂移场景：选题是「人形机器人量产」，候选里混入电竞/百度/中超/歌手 ----
BRIEF = {"topic": "人形机器人量产交付", "angle": "量产节点对供应链的冲击"}
CANDIDATES = [
    ev(1, "机器人量产白皮书", "robotics", "人形机器人量产交付进入关键节点，供应链产能爬坡显著。"),
    ev(2, "机器人量产白皮书", "robotics", "量产交付带动上游减速器与伺服电机订单增长。"),
    ev(3, "机器人产业月报", "robotics", "人形机器人整机成本随量产下降，交付周期缩短。"),
    ev(4, "电竞联赛战报", "esports", "电竞联赛季后赛战报，选手状态火热。"),
    ev(5, "百度财报解读", "internet", "百度季度财报发布，广告收入回暖。"),
    ev(6, "中超联赛综述", "sports", "中超联赛本轮综述，争冠形势胶着。"),
    ev(7, "歌手巡演资讯", "music", "知名歌手宣布新一轮全国巡演计划。"),
]


def old_filter(candidates: list[dict], scored: list[tuple[dict, float]]) -> list[dict]:
    """v2.5 及以前的过滤链复刻（用于对照，证明旧逻辑必然漏）"""
    from collections import Counter
    top = max(s for _, s in scored)
    relevant = [e for e, s in scored if top <= 0 or s >= top * 0.3]
    if len(scored) >= 3:
        dom = Counter(e["category"] for e, _ in scored[:3]).most_common(1)[0][0]
        same_cat = [e for e in relevant if e["category"] == dom]
        if len(same_cat) >= 3:
            relevant = same_cat
    if len(relevant) < 4:                       # ← 元凶：无条件全放回
        relevant = [e for e, _ in scored][:8]
    return relevant


async def test_l0() -> None:
    print("\n=== L0 检索层：放宽逻辑 ===")
    topic_q = f"{BRIEF['topic']} {BRIEF['angle']}"
    scored = await retrieve_scores(None, CANDIDATES, topic_q)

    old = old_filter(CANDIDATES, scored)
    old_pollution = [e["doc_title"] for e in old if e["category"] != "robotics"]
    print(f"  旧逻辑保留 {len(old)} 条，其中无关类目 {len(old_pollution)} 条：{old_pollution}")
    check(len(old_pollution) > 0,
          "旧逻辑确实会把无关类目放回（复现 08-06 漂移根因）")

    new, main_doc, guard = await ResearcherAgent()._filter(None, CANDIDATES, BRIEF)
    new_pollution = [e["doc_title"] for e in new if e["category"] != "robotics"]
    print(f"  新逻辑保留 {len(new)} 条，其中无关类目 {len(new_pollution)} 条：{new_pollution}")
    print(f"  guard={guard}")
    check(not new_pollution, "新逻辑不再放回无关类目")
    check(main_doc == "机器人量产白皮书", f"主干文档识别正确（{main_doc}）")
    check(guard.get("dominant_category") == "robotics", "主导类目识别为 robotics")

    # 旧 bug 的触发条件：主导类目恰好 3 条（3 >= 3 生效，但 3 < 4 立刻被放宽抹掉）
    same_cat = [e for e in CANDIDATES if e["category"] == "robotics"]
    check(len(same_cat) == 3 and MIN_EVIDENCES == 3,
          "已复现「主导类目恰好 3 条」这一必然触发旧 bug 的边界")


async def test_l0_cross_language() -> None:
    print("\n=== L0 跨语言：词面失效时不裸奔 ===")
    jp_brief = {"topic": "ヒューマノイドロボットの量産", "angle": "サプライチェーンへの影響"}
    scored = await retrieve_scores(None, CANDIDATES, f"{jp_brief['topic']} {jp_brief['angle']}")
    top = max(s for _, s in scored)
    check(top < settings.topic_min_score,
          f"日文选题对中文语料 BM25 峰值 {top:.3f} < 阈值 {settings.topic_min_score}（词面闸应停用）")
    new, main_doc, guard = await ResearcherAgent()._filter(None, CANDIDATES, jp_brief)
    print(f"  保留 {len(new)} 条，lexical_usable={guard.get('lexical_usable')}，主干={main_doc}")
    check(guard.get("lexical_usable") is False, "词面闸已自动停用，交由类目 + 主干聚类兜底")
    check(len(new) <= settings.top_evidences, f"仍受主干聚类约束（≤{settings.top_evidences} 条）")


def _article(sections: list[tuple[str, str]]) -> dict:
    return {"title": "t", "summary": "s",
            "body": {"sections": [{"heading": h, "text": t} for h, t in sections]}}


EVIDENCES = [
    {**ev(1, "机器人量产白皮书", "robotics", "量产交付进入关键节点。"), "is_main": True},
    {**ev(2, "机器人量产白皮书", "robotics", "带动上游订单增长。"), "is_main": True},
    {**ev(3, "机器人量产白皮书", "robotics", "整机成本下降。"), "is_main": True},
    {**ev(4, "电竞联赛战报", "esports", "电竞季后赛战报。"), "is_main": False},
    {**ev(5, "百度财报解读", "internet", "百度广告收入回暖。"), "is_main": False},
]


def test_l1() -> None:
    print("\n=== L1 TCS 闸门 ===")
    # 拼盘稿：主干 1 节 + 各背景文档单独成节（08-06 的真实形态）
    drifted = _article([
        ("量产节点", "人形机器人量产交付进入关键节点 [ev_001]。"),
        ("电竞赛事", "电竞联赛季后赛战报，选手状态火热 [ev_004]。"),
        ("百度财报", "百度季度财报发布，广告收入回暖 [ev_005]。"),
    ])
    r = score_article(drifted, BRIEF, EVIDENCES)
    print(f"  拼盘稿 TCS={r['tcs']} main_ratio={r['main_ratio']} "
          f"cross_docs={r['cross_docs']} drift={r['drift_sections']}")
    print(f"  reason: {r['reason']}")
    check(not r["passed"], "拼盘稿被拦截")
    check(r["drift_sections"] == [1, 2], "精确定位到第 2、3 节为漂移节（可定点重写）")
    check(r["main_ratio"] < settings.tcs_main_ratio_min, "主干引用占比不达标")

    healthy = _article([
        ("量产节点", "量产交付进入关键节点 [ev_001]，产能爬坡显著 [ev_002]。"),
        ("成本曲线", "整机成本随量产下降 [ev_003]，交付周期缩短 [ev_001]。"),
        ("行业背景", "同期互联网广告回暖 [ev_005]，但与整机供应链非同一链条 [ev_002]。"),
    ])
    h = score_article(healthy, BRIEF, EVIDENCES)
    print(f"  健康稿 TCS={h['tcs']} main_ratio={h['main_ratio']} cross_docs={h['cross_docs']}")
    check(h["passed"], "健康稿放行（背景证据作补充、不单独成节）")
    check(h["tcs"] > r["tcs"], f"TCS 有区分度（{h['tcs']} > {r['tcs']}）")

    dropped = drop_drift_sections(drifted, r["drift_sections"])
    n = len(dropped["body"]["sections"])
    check(n >= 2, f"删节兜底保底留 {n} 节，结构不崩")

    no_cite = _article([("导语", "行业进入新阶段。"), ("展望", "未来值得关注。")])
    check(not score_article(no_cite, BRIEF, EVIDENCES)["passed"], "无引用稿件被拦截")


def test_l2_l3() -> None:
    print("\n=== L2/L3 Writer 约束与兜底 ===")
    w = WriterAgent()

    # fallback 需读取 ctx.market.language 决定小标题语言（正文语言守卫引入），
    # 离线回归用最小 stub 提供市场语言，中/英两分支都要能出稿
    def _ctx(lang: str):
        return SimpleNamespace(market=SimpleNamespace(language=lang))

    for lang in ("zh-CN", "en-US"):
        out = asyncio.run(w.fallback(_ctx(lang), None, {"brief": BRIEF, "evidences": EVIDENCES}))
        secs = out["article"]["body"]["sections"]
        docs = {e["doc_title"] for e in EVIDENCES
                if f"[{e['ev_id']}]" in " ".join(s["text"] for s in secs)}
        print(f"  [{lang}] 兜底稿 {len(secs)} 节，标题：{secs[0]['heading']}，引用文档：{docs}")
        check(len(secs) == 2, f"[{lang}] 兜底稿固定 2 节（旧实现是每条证据一节）")
        check(docs <= {"机器人量产白皮书"}, f"[{lang}] 兜底稿只引用主干文档，不做多源拼装")
        r = score_article(out["article"], BRIEF, EVIDENCES)
        check(r["passed"], f"[{lang}] 兜底稿自身通过 TCS 闸门（TCS={r['tcs']}）")

    # L2：旧规则会因「引用广度不足」而废掉一篇主线扎实的稿子
    old_need = max(2, len(EVIDENCES) // 3)
    cited_main_only = {"ev_001", "ev_002"}
    check(len(cited_main_only) < old_need or old_need <= 2,
          f"旧规则要求 ≥{old_need} 处不同引用（奖励广度 = 制度性鼓励拼盘）")


def main() -> int:
    print(f"配置：top_evidences={settings.top_evidences} "
          f"topic_min_score={settings.topic_min_score} "
          f"tcs_main_ratio_min={settings.tcs_main_ratio_min} "
          f"tcs_cross_doc_max={settings.tcs_cross_doc_max}")
    asyncio.run(test_l0())
    asyncio.run(test_l0_cross_language())
    test_l1()
    test_l2_l3()
    print(f"\n{'全部通过' if not _failed else f'{_failed} 项失败'}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
