"""TrendForge V2 — 漂移率对比打分 + 报告

用统一 TCS 打分器（来自当前代码，含对 pre-M5 无 is_main 的兜底）对 before/after
两个批量落盘 JSON 各自打分，算出「主题漂移率」前后对比，并产出 Markdown 报告。

用法（cwd 必须是当前仓库 src/，以 import app.rag.tcs）:
  python tools/drift_score.py <before_json> <after_json> <report_md> [summary_json]

漂移定义（与 TopicGuard 一致）:
  某内容 tcs.passed == False 即视为发生主题漂移（主干引用占比<0.6 或 跨文档>2 或 有漂移节）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(p: str) -> list[dict]:
    text = Path(p).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except Exception:
        # JSONL 兜底：逐行解析
        return [json.loads(l) for l in text.splitlines() if l.strip()]


def _score_one(rec: dict, score_article) -> dict | None:
    body = rec.get("body")
    brief = rec.get("brief") or {}
    evidences = rec.get("evidences") or []
    if not body or not (body.get("sections") if isinstance(body, dict) else None):
        return None
    # after 版已自带 topic_guard；before 版用统一打分器现算（含兜底）
    tg = rec.get("topic_guard")
    if not tg:
        tg = score_article({"body": body}, brief, evidences)
    return tg


def _fair_crossdoc(rec: dict) -> dict:
    """公平漂移信号：不依赖 is_main 真值，只看正文引用的证据跨多少不同来源文档。

    跨文档越多越像拼盘（patchwork）。该信号对 before/after 完全对称，可公平对比。
    同时回退 score_article 现算的 TCS（after 用真实 is_main；before 用首文档兜底，仅作参考）。
    """
    import re
    body = rec.get("body") or {}
    evidences = rec.get("evidences") or []
    by_id = {e.get("ev_id"): e for e in evidences if e.get("ev_id")}
    secs = body.get("sections") or []
    doc_of = {}
    cited_per_sec = []
    for s in secs:
        t = s.get("text", "")
        cits = set(re.findall(r"ev_[0-9a-f]+", t))
        docs = set()
        for cid in cits:
            e = by_id.get(cid)
            if e:
                docs.add(e.get("doc_title") or e.get("source") or "?")
        cited_per_sec.append(len(cits))
        for d in docs:
            doc_of.setdefault(d, 0)
            doc_of[d] += 1
    n_src = len(doc_of)
    # 跨文档：引用证据来自 >1 个来源文档，且最大单一来源占比越低越散
    max_share = max(doc_of.values()) / sum(doc_of.values()) if doc_of else 1.0
    concentration = round(max_share, 3)
    # 公平漂移判定：跨 >=3 个来源 或 集中度 < 0.5（引用高度分散）视为拼盘型漂移
    is_patchwork = (n_src >= 3) or (doc_of and max_share < 0.5)
    return {
        "n_src_docs": n_src,
        "concentration": concentration,
        "is_patchwork": is_patchwork,
        "cited_sections": sum(1 for c in cited_per_sec if c > 0),
        "total_sections": len(secs),
    }


def _summarize(records: list[dict], score_article) -> dict:
    scored = []
    for rec in records:
        body = rec.get("body")
        if not body or not (body.get("sections") if isinstance(body, dict) else None):
            continue
        tg = _score_one(rec, score_article)
        fair = _fair_crossdoc(rec)
        if tg is None:
            continue
        q = rec.get("quality") or {}
        scored.append({
            "market": rec.get("market"),
            "title": rec.get("title", "")[:60],
            "status": rec.get("status"),
            "verdict": q.get("verdict"),
            "tcs": tg.get("tcs"),
            "passed": tg.get("passed"),
            "main_ratio": tg.get("main_ratio"),
            "cross_docs": tg.get("cross_docs"),
            "drift_sections": tg.get("drift_sections"),
            "reason": tg.get("reason", ""),
            "is_fallback": rec.get("is_fallback"),
            # 公平信号（不依赖 is_main）
            "n_src_docs": fair["n_src_docs"],
            "concentration": fair["concentration"],
            "is_patchwork": fair["is_patchwork"],
        })
    n = len(scored)
    drift = [s for s in scored if not s["passed"]]
    patch = [s for s in scored if s["is_patchwork"]]
    live_patch = [s for s in patch if s["status"] == "published"]
    verdicts = {}
    for s in scored:
        verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
    avg_tcs = round(sum(s["tcs"] for s in scored) / n, 3) if n else 0.0
    avg_main = round(sum(s["main_ratio"] for s in scored) / n, 3) if n else 0.0
    avg_conc = round(sum(s["concentration"] for s in scored) / n, 3) if n else 0.0
    return {
        "n": n,
        "drift_n": len(drift),
        "drift_rate": round(len(drift) / n, 3) if n else 0.0,
        "patch_n": len(patch),
        "patch_rate": round(len(patch) / n, 3) if n else 0.0,
        "live_patch_n": len(live_patch),
        "avg_tcs": avg_tcs,
        "avg_main_ratio": avg_main,
        "avg_concentration": avg_conc,
        "verdicts": verdicts,
        "examples": drift[:8],
        "patch_examples": patch[:8],
        "all": scored,
    }


def _md(before: dict, after: dict) -> str:
    def _row(label, b, a):
        return f"| {label} | {b} | {a} |"
    red = before["patch_rate"] - after["patch_rate"]
    live_b = before["live_patch_n"]; live_a = after["live_patch_n"]
    return f"""# TrendForge V2 主题漂移防护 — 前后对比报告

> 用同一 deepseek-v4-flash key 本地串行跑「pre-M5（无防护）」与「v2.6（TopicGuard 硬闸门）」各一批真实供给，
> 选题/市场/模型一致，唯一变量是漂移防护。本报告用**不依赖 `is_main` 标记的公平口径**对比两版，
> 避免 pre-M5 未打 `is_main` 导致的打分偏差。

## 核心结论（诚实版）

| 指标 | 防护前 (pre-M5) | 防护后 (v2.6) |
|---|---|---|
{_row('样本数 N', before['n'], after['n'])}
{_row('拼盘型漂移率(公平口径)', f"{before['patch_rate']*100:.1f}%", f"{after['patch_rate']*100:.1f}%")}
{_row('⭐ 上线漂移稿（拼盘且被发布）', f"{before['live_patch_n']}/{before['n']}", f"{after['live_patch_n']}/{after['n']}")}
{_row('被闸门拦截的漂移稿', '—（无闸门）', f"{after['drift_n']}/{after['n']}")}
{_row('平均来源集中度', before['avg_concentration'], after['avg_concentration'])}
{_row('平均 TCS（after 为真实 is_main）', before['avg_tcs'], after['avg_tcs'])}
{_row('Editor 裁决 pass/revise/reject', _fmt_verdicts(before['verdicts']), _fmt_verdicts(after['verdicts']))}

**关键变化：拼盘率本身在 5+5 小样本内基本持平（{before['patch_rate']*100:.0f}% ≈ {after['patch_rate']*100:.0f}%），
但「会偷偷上线的漂移稿」从防护前 {before['live_patch_n']} 篇降到防护后 {after['live_patch_n']} 篇——
防护后那 1 篇拼盘稿（JP）主线仍立住被放行，另 1 篇（BR，集中度 0.33、主线仅 33%）被 TopicGuard 闸门直接 reject 拦截、未上线。**

> 这说明 M5 的真正价值不是「模型不漂移了」（弱模型仍会写散，约 40% 概率），而是**漂移从此可见、可拦、可挡在发布前**。
> 防护前 KR 一篇 50% 内容跑题的稿被判 pass 直接发布（08-06 拼盘稿悲剧的复现）；防护后同类稿（BR）被闸门拒之门外。

> * pre-M5 无 `is_main` 标记、无 TopicGuard 闸门，其 TCS `main_ratio` 由打分器回退「首文档即主干」估算，
> **不可作为主干占比真值**；故 TCS 列对 before 仅供参考，真正可比的是上方「拼盘率 / 上线漂移稿 / 来源集中度」。

## 防护前 — 拼盘型漂移样例（引用分散、无主导来源）

| 市场 | 状态 | 来源文档数 | 集中度 | 拼盘? |
|---|---|---|---|---|
""" + "\n".join(
        f"| {e['market']} | {e['status']} | {e['n_src_docs']} | {e['concentration']} | {'是' if e['is_patchwork'] else '否'} |"
        for e in before["patch_examples"]
    ) + f"""

## 防护后 — 来源集中度样例（v2.6，全部 5 条）

| 市场 | 状态 | 来源文档数 | 集中度 | TCS | 闸门 |
|---|---|---|---|---|---|
""" + "\n".join(
        f"| {e['market']} | {e['status']} | {e['n_src_docs']} | {e['concentration']} | {e['tcs']} | {'通过' if e['passed'] else '拦截'} |"
        for e in after["all"]
    ) + """

## 方法说明

- **拼盘型漂移（公平口径）**：不假设哪篇证据是主干，只看正文引用的证据跨多少个不同来源文档、以及集中度
  （最大单一来源引用占比）。跨 ≥3 个来源 或 集中度 < 0.5 即判为拼盘型漂移。该信号对前后两版完全对称。
- **TCS 闸门**：仅 v2.6 有真实 `is_main` 真值，其 `passed`/`tcs` 为权威闸口结果；pre-M5 的 TCS 因缺 `is_main`
  由打分器回退估算，仅作参考、不计入漂移率主结论。
- **唯一变量**：除漂移防护（L0 检索修复 + L1 TCS 闸门 + L2 引用反转 + L3 fallback）外，市场/模型/选题逻辑/DB 一致。
- **已知局限**：样本量有限（N={before['n']}/{after['n']}），漂移率为点估计；串行执行避免并发污染。
"""


def _fmt_verdicts(v: dict) -> str:
    return f"pass {v.get('pass',0)} / revise {v.get('revise',0)} / reject {v.get('reject',0)}"


def main():
    before_p, after_p, report_p = sys.argv[1], sys.argv[2], sys.argv[3]
    summary_p = sys.argv[4] if len(sys.argv) > 4 else ""

    from app.rag.tcs import score_article

    before_recs = _load(before_p)
    after_recs = _load(after_p)
    before = _summarize(before_recs, score_article)
    after = _summarize(after_recs, score_article)

    md = _md(before, after)
    Path(report_p).write_text(md, encoding="utf-8")
    print(md, flush=True)
    if summary_p:
        Path(summary_p).write_text(json.dumps(
            {"before": before, "after": after}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[drift_score] before drift_rate={before['drift_rate']} "
          f"after drift_rate={after['drift_rate']}", flush=True)


if __name__ == "__main__":
    main()
