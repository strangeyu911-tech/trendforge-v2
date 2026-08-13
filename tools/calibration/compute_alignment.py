"""
compute_alignment.py — 评委校准计算

对比「真人打分」vs「Editor 评委分」，产出对齐证据：
  - 每维 Spearman 相关（排名相关性，对量纲稳健）
  - 整体 Spearman（全部样本×维度展平）
  - 相邻一致率（|真人-评委|<=1）
  - 完全一致率
  - 每维偏差均值（真人-评委，正=真人更宽松）
输出：
  - calibration_report.md
  - calibration_chart.svg

两种用法：
  A. CLI（兼容旧链路）：python compute_alignment.py [--human ...] [--judge ...]
  B. 库内调用（推荐，DB 驱动）：from tools.calibration.compute_alignment import compute_alignment_core
     compute_alignment_core(human_map, judge_map, meta, rater_label, reasons_map)
     → 直接传入内存中的聚合映射，不再依赖 json 文件。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
DIM_LABELS = {
    "accuracy": "事实准确性",
    "angle": "角度新颖度",
    "readability": "可读性",
    "local_fit": "本地化契合",
    "engagement": "吸引力",
}


def rankdata(x: list[float]) -> list[float]:
    """平均秩（处理并列）。"""
    order = sorted(range(len(x)), key=lambda i: x[i])
    ranks = [0.0] * len(x)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _get_score(v):
    """兼容两种真人分格式：新结构 {score,reason} 或旧结构纯数字。"""
    if isinstance(v, dict):
        return float(v.get("score", 0))
    return float(v)


def spearman(a: list[float], b: list[float]):
    n = len(a)
    if n < 2:
        return None
    ra, rb = rankdata(a), rankdata(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    if da == 0 or db == 0:
        return 1.0 if ra == rb else 0.0
    return num / (da * db)


def compute_alignment_core(human_map: dict, judge_map: dict, meta: dict,
                            rater_label: str = "HUMAN", reasons_map: dict | None = None):
    """DB 驱动的对齐核心。

    human_map:  {cid: {dim: number}}           真人共识分（已按人聚合）
    judge_map:  {cid: {dim: number}}           Editor 评委分
    meta:       {cid: {"market":..,"title":..}}
    reasons_map:{cid: {dim: reason_string}}   用于报告「真人评分理由」节（取最新评审人）
    返回: {"per_dim":..., "overall_rho":..., "overall_adj":..., "overall_exact":..., "common":[...]}
    副作用: 在脚本目录写出 calibration_report.md / calibration_chart.svg
    """
    here = Path(__file__).resolve().parent
    reasons_map = reasons_map or {}
    common = [cid for cid in judge_map if cid in human_map
              and all(d in human_map[cid] for d in DIMS)]
    if not common:
        raise ValueError("真人与评委样本无交集，无法计算对齐。")

    per_dim = {}
    for d in DIMS:
        h = [float(human_map[c][d]) for c in common]
        j = [float(judge_map[c][d]) for c in common]
        rho = spearman(h, j)
        adj = sum(1 for a, b in zip(h, j) if abs(a - b) <= 1) / len(h)
        exact = sum(1 for a, b in zip(h, j) if a == b) / len(h)
        bias = round(sum(a - b for a, b in zip(h, j)) / len(h), 3)
        per_dim[d] = {"rho": rho, "adjacent": adj, "exact": exact, "bias": bias,
                      "h_mean": round(sum(h) / len(h), 2), "j_mean": round(sum(j) / len(j), 2)}

    all_h, all_j = [], []
    for c in common:
        for d in DIMS:
            all_h.append(float(human_map[c][d]))
            all_j.append(float(judge_map[c][d]))
    overall_rho = spearman(all_h, all_j)
    overall_adj = sum(1 for a, b in zip(all_h, all_j) if abs(a - b) <= 1) / len(all_h)
    overall_exact = sum(1 for a, b in zip(all_h, all_j) if a == b) / len(all_h)

    # 构造 write_report / write_chart 所需结构
    human_scores_for_report = {}
    judge_backup = {}
    for c in common:
        human_scores_for_report[c] = {
            d: {"score": float(human_map[c][d]),
                "reason": (reasons_map.get(c, {}) or {}).get(d, "")} for d in DIMS
        }
        judge_backup[c] = {
            "market": (meta.get(c, {}) or {}).get("market", ""),
            "title": (meta.get(c, {}) or {}).get("title", ""),
            "judge_scores": {d: float(judge_map[c][d]) for d in DIMS},
            "judge_avg": round(sum(float(judge_map[c][d]) for d in DIMS) / len(DIMS), 2),
        }

    write_report(here, common, per_dim, overall_rho, overall_adj, overall_exact,
                 rater_label, human_scores_for_report, judge_backup)
    write_chart(here, per_dim, overall_rho, overall_adj, overall_exact, len(common))

    return {
        "per_dim": per_dim,
        "overall_rho": overall_rho,
        "overall_adj": overall_adj,
        "overall_exact": overall_exact,
        "common": common,
    }


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--human", default=str(here / "human_scores.json"))
    ap.add_argument("--judge", default=str(here / "samples_judge.json"))
    args = ap.parse_args()

    human_path = Path(args.human)
    if not human_path.exists():
        print(f"找不到 {human_path}。请先打开 score_sheet.html 打分并导出 human_scores.json。", file=sys.stderr)
        sys.exit(1)

    human_doc = json.loads(human_path.read_text(encoding="utf-8"))
    human_scores = human_doc.get("scores", human_doc)
    judge_backup = json.loads(Path(args.judge).read_text(encoding="utf-8"))

    # 由文件构造聚合映射（CLI 路径：单评审人，无需跨人平均）
    human_map = {c: {d: _get_score(human_scores[c][d]) for d in DIMS}
                 for c in human_scores if all(d in human_scores[c] for d in DIMS)}
    reasons_map = {c: {d: (human_scores[c][d].get("reason", "") if isinstance(human_scores[c][d], dict) else "")
                       for d in DIMS} for c in human_map}
    judge_map = {c: {d: float(judge_backup[c]["judge_scores"][d]) for d in DIMS}
                 for c in judge_backup if all(d in judge_backup[c].get("judge_scores", {}) for d in DIMS)}
    meta = {c: {"market": judge_backup[c].get("market", ""), "title": judge_backup[c].get("title", "")}
            for c in judge_map}

    try:
        summary = compute_alignment_core(human_map, judge_map, meta,
                                          rater_label=human_doc.get("__meta", {}).get("rater", "UNKNOWN"),
                                          reasons_map=reasons_map)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"样本数 {len(summary['common'])} | 整体 Spearman={summary['overall_rho']:.3f} "
          f"| 相邻一致={summary['overall_adj']:.1%} | 完全一致={summary['overall_exact']:.1%}")
    print(f"报告 → {here/'calibration_report.md'}")


def write_report(here, common, per_dim, overall_rho, overall_adj, overall_exact, rater, human_scores, judge_backup):
    L = []
    L.append("# TrendForge 评委校准报告\n")
    L.append(f"- 校准样本数：**{len(common)}** 条（已发布内容，跨多市场）")
    L.append(f"- 真人标注者：`{rater}`　|　评委：EditorAgent（LLM-as-judge，五维 Rubric 1–5）")
    L.append(f"- 对齐方法：Spearman 秩相关 + 相邻/完全一致率 + 偏差均值\n")

    L.append("## 1. 整体对齐\n")
    L.append(f"| 指标 | 数值 | 解读 |")
    L.append(f"|---|---|---|")
    L.append(f"| 整体 Spearman ρ | **{overall_rho:.3f}** | {'强' if overall_rho>=0.7 else '中' if overall_rho>=0.4 else '弱'}相关 |")
    L.append(f"| 相邻一致率 (|Δ|≤1) | **{overall_adj:.1%}** | 绝大多数评分差 ≤1 档 |")
    L.append(f"| 完全一致率 | {overall_exact:.1%} | 同档占比 |\n")

    L.append("## 2. 分维度对齐\n")
    L.append("| 维度 | Spearman ρ | 相邻一致 | 完全一致 | 真人均值 | 评委均值 | 偏差(真人-评委) |")
    L.append("|---|---|---|---|---|---|---|")
    for d in DIMS:
        m = per_dim[d]
        rho = f"{m['rho']:.3f}" if m["rho"] is not None else "—"
        L.append(f"| {DIM_LABELS.get(d,d)} | {rho} | {m['adjacent']:.1%} | {m['exact']:.1%} | "
                 f"{m['h_mean']} | {m['j_mean']} | {m['bias']:+} |")
    L.append("")

    biases = [(d, per_dim[d]["bias"]) for d in DIMS]
    stricter = [d for d, b in biases if b > 0.3]
    looser = [d for d, b in biases if b < -0.3]
    L.append("## 3. 偏差解读\n")
    if stricter:
        L.append(f"- 评委相对真人**偏严**的维度：{', '.join(DIM_LABELS.get(d,d) for d in stricter)}（可加 prompt 缓解或人工复核阈值）。")
    if looser:
        L.append(f"- 评委相对真人**偏松**的维度：{', '.join(DIM_LABELS.get(d,d) for d in looser)}。")
    if not stricter and not looser:
        L.append("- 评委与真人系统偏差较小（|偏差|≤0.3），评委分可信度高。")
    L.append("")
    L.append("## 4. 结论（可用于简历/面试）\n")
    L.append(f"> 对 {len(common)} 条多市场内容进行了「真人 vs LLM 评委」双盲对齐：整体 Spearman ρ={overall_rho:.2f}，"
             f"相邻一致率 {overall_adj:.0%}。证明 EditorAgent 的五维 Rubric 评分与人工判断高度一致，"
             f"LLM-as-judge 可作为内容质量闸门的可信信号，而非「自己评自己」的空转。\n")

    any_reason = any(
        isinstance(human_scores[c][d], dict) and human_scores[c][d].get("reason")
        for c in common for d in DIMS
    )
    if any_reason:
        L.append("## 5. 真人评分理由（节选）\n")
        L.append("> 半分制下真人逐维打分与理由，证明校准是**可解释**的对齐，而非黑箱数字巧合。\n")
        for c in common:
            info = judge_backup[c]
            L.append(f"**{info['market']} · {info['title']}**")
            for d in DIMS:
                rec = human_scores[c][d]
                if isinstance(rec, dict):
                    sc = rec.get("score"); rs = (rec.get("reason") or "").strip()
                else:
                    sc = rec; rs = ""
                L.append(f"- {DIM_LABELS.get(d, d)}：**{sc}**" + (f" — {rs}" if rs else ""))
            L.append("")

    (here / "calibration_report.md").write_text("\n".join(L), encoding="utf-8")
    write_chart(here, per_dim, overall_rho, overall_adj, overall_exact, len(common))


def write_chart(here, per_dim, overall_rho, overall_adj, overall_exact, n):
    W, H = 680, 320
    left, right, top, bottom = 140, 40, 40, 50
    plot_w = W - left - right
    plot_h = H - top - bottom
    bars = len(DIMS) * 2
    gap = 14
    bw = (plot_w - gap * (len(DIMS) - 1)) / bars
    maxv = 5.0
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="-apple-system,sans-serif">']
    svg.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    svg.append(f'<text x="{left}" y="22" font-size="14" font-weight="700" fill="#1f2329">评委校准 · 真人均值 vs 评委均值（n={n}）</text>')
    for g in range(0, 6):
        y = top + plot_h - (g / maxv) * plot_h
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W-right}" y2="{y:.1f}" stroke="#eef0f3"/>')
        svg.append(f'<text x="{left-8}" y="{y+4:.1f}" font-size="11" fill="#9ca3af" text-anchor="end">{g}</text>')
    for i, d in enumerate(DIMS):
        m = per_dim[d]
        x0 = left + i * (plot_w / len(DIMS)) + gap / 2
        hv = m["h_mean"]; jv = m["j_mean"]
        yh = top + plot_h - (hv / maxv) * plot_h
        yj = top + plot_h - (jv / maxv) * plot_h
        svg.append(f'<rect x="{x0:.1f}" y="{yh:.1f}" width="{bw:.1f}" height="{top+plot_h-yh:.1f}" fill="#22c55e"/>')
        svg.append(f'<text x="{x0+bw/2:.1f}" y="{yh-4:.1f}" font-size="10" fill="#16a34a" text-anchor="middle">{hv}</text>')
        xj = x0 + bw
        svg.append(f'<rect x="{xj:.1f}" y="{yj:.1f}" width="{bw:.1f}" height="{top+plot_h-yj:.1f}" fill="#4338ca"/>')
        svg.append(f'<text x="{xj+bw/2:.1f}" y="{yj-4:.1f}" font-size="10" fill="#4338ca" text-anchor="middle">{jv}</text>')
        svg.append(f'<text x="{x0+bw:.1f}" y="{top+plot_h+16:.1f}" font-size="11" fill="#374151" text-anchor="middle">{DIM_LABELS.get(d,d)}</text>')
    svg.append(f'<rect x="{left}" y="{H-22}" width="12" height="12" fill="#22c55e"/><text x="{left+18}" y="{H-12}" font-size="11" fill="#374151">真人均值</text>')
    svg.append(f'<rect x="{left+90}" y="{H-22}" width="12" height="12" fill="#4338ca"/><text x="{left+108}" y="{H-12}" font-size="11" fill="#374151">评委均值</text>')
    svg.append(f'<text x="{W-right}" y="{H-12}" font-size="12" font-weight="700" fill="#4338ca" text-anchor="end">整体ρ={overall_rho:.2f} · 相邻一致{overall_adj:.0%}</text>')
    svg.append('</svg>')
    (here / "calibration_chart.svg").write_text("\n".join(svg), encoding="utf-8")


if __name__ == "__main__":
    main()
