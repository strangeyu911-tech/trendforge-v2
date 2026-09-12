"""评委校准对齐计算（服务端唯一实现，内存生成报告，不依赖仓库内 tools/ 文件）

从 tools/calibration/compute_alignment.py 收编而来——原实现把报告写死到仓库目录，
Render 镜像只含 src/，线上提交真人打分后读报告必 404。现在：
  - compute_core()      纯计算（Spearman / 相邻一致 / 偏差），无副作用
  - build_report_md()   报告 markdown 字符串（内存）
  - build_chart_svg()   对齐柱状图 SVG 字符串（内存）
  - write_artifacts()   可选落盘（本地仓库保留 md/svg 供「发布校准报告.bat」提交），
                        目录不存在（Render）时静默跳过
tools/calibration/compute_alignment.py 保留为 CLI 薄封装（--human/--judge 旧用法不变）。
"""
from __future__ import annotations

DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
DIM_LABELS = {
    "accuracy": "事实准确性",
    "angle": "角度新颖度",
    "readability": "可读性",
    "local_fit": "本地化契合",
    "engagement": "吸引力",
}


def _get_score(v):
    """兼容两种真人分格式：新结构 {score,reason} 或旧结构纯数字。"""
    if isinstance(v, dict):
        return float(v.get("score", 0))
    return float(v)


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


def compute_core(human_map: dict, judge_map: dict, meta: dict,
                 reasons_map: dict | None = None) -> dict:
    """纯计算核心。

    human_map:  {cid: {dim: number}}           真人共识分（已按人聚合）
    judge_map:  {cid: {dim: number}}           Editor 评委分
    meta:       {cid: {"market":..,"title":..}}
    reasons_map:{cid: {dim: reason_string}}   报告「真人评分理由」节用
    返回 {per_dim, overall_rho, overall_adj, overall_exact, common, human_scores, judge_backup}
    """
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

    human_scores = {}
    judge_backup = {}
    for c in common:
        human_scores[c] = {
            d: {"score": float(human_map[c][d]),
                "reason": (reasons_map.get(c, {}) or {}).get(d, "")} for d in DIMS
        }
        judge_backup[c] = {
            "market": (meta.get(c, {}) or {}).get("market", ""),
            "title": (meta.get(c, {}) or {}).get("title", ""),
            "judge_scores": {d: float(judge_map[c][d]) for d in DIMS},
            "judge_avg": round(sum(float(judge_map[c][d]) for d in DIMS) / len(DIMS), 2),
        }

    return {"per_dim": per_dim, "overall_rho": overall_rho, "overall_adj": overall_adj,
            "overall_exact": overall_exact, "common": common,
            "human_scores": human_scores, "judge_backup": judge_backup}


def build_report_md(res: dict, rater: str = "HUMAN") -> str:
    """由 compute_core 结果生成报告 markdown（纯内存字符串）。"""
    per_dim, common = res["per_dim"], res["common"]
    overall_rho, overall_adj, overall_exact = (
        res["overall_rho"], res["overall_adj"], res["overall_exact"])
    L = []
    L.append("# TrendForge 评委校准报告\n")
    L.append(f"- 校准样本数：**{len(common)}** 条（已发布内容，跨多市场）")
    L.append(f"- 真人标注者：`{rater}`　|　机器评委：总编审核环节（大模型评审，五维评分 1–5）")
    L.append(f"- 对齐方法：Spearman 秩相关 + 相邻/完全一致率 + 偏差均值\n")

    L.append("## 1. 整体对齐\n")
    L.append("| 指标 | 数值 | 解读 |")
    L.append("|---|---|---|")
    L.append(f"| 整体 Spearman ρ | **{overall_rho:.3f}** | {'强' if overall_rho >= 0.7 else '中' if overall_rho >= 0.4 else '弱'}相关 |")
    # 注意：表格单元格内不能出现竖线，Δ 条件用全角括号表述
    L.append(f"| 相邻一致率（Δ≤1 档） | **{overall_adj:.1%}** | 绝大多数评分差 ≤1 档 |")
    L.append(f"| 完全一致率 | {overall_exact:.1%} | 同档占比 |\n")

    L.append("## 2. 分维度对齐\n")
    L.append("| 维度 | Spearman ρ | 相邻一致 | 完全一致 | 真人均值 | 评委均值 | 偏差(真人-评委) |")
    L.append("|---|---|---|---|---|---|---|")
    for d in DIMS:
        m = per_dim[d]
        rho = f"{m['rho']:.3f}" if m["rho"] is not None else "—"
        L.append(f"| {DIM_LABELS.get(d, d)} | {rho} | {m['adjacent']:.1%} | {m['exact']:.1%} | "
                 f"{m['h_mean']} | {m['j_mean']} | {m['bias']:+} |")
    L.append("")

    biases = [(d, per_dim[d]["bias"]) for d in DIMS]
    stricter = [d for d, b in biases if b > 0.3]
    looser = [d for d, b in biases if b < -0.3]
    L.append("## 3. 偏差解读\n")
    if stricter:
        L.append(f"- 评委相对真人**偏严**的维度：{', '.join(DIM_LABELS.get(d, d) for d in stricter)}（可加提示词缓解或人工复核阈值）。")
    if looser:
        L.append(f"- 评委相对真人**偏松**的维度：{', '.join(DIM_LABELS.get(d, d) for d in looser)}。")
    if not stricter and not looser:
        L.append("- 评委与真人系统偏差较小（|偏差|≤0.3），评委分可信度高。")
    L.append("")
    L.append("## 4. 结论\n")
    if overall_rho >= 0.7:
        verdict = ("评委排序与人工判断高度一致，LLM-as-judge 可作为内容质量闸门的可信信号，"
                   "而非「自己评自己」的空转。")
    elif overall_rho >= 0.4:
        verdict = ("评委排序与人工判断中等相关：趋势可用于初筛与迭代对比，pass/fail 边界样本"
                   "仍需人工复核；扩样并校准偏差维度可进一步提升对齐。")
    else:
        verdict = ("评委排序与人工判断相关性弱：当前评委分不足以单独作为质量闸门信号，"
                   "应以人工复核为主，并把「评委与真人打分标准对齐」列为下一轮迭代项。")
    caveat = "（样本量 n<20，结论为方向性参考）" if len(common) < 20 else ""
    L.append(f"> 对 {len(common)} 条多市场内容进行了「真人 vs LLM 评委」对齐：整体 Spearman ρ={overall_rho:.2f}，"
             f"相邻一致率 {overall_adj:.0%}{caveat}。{verdict}\n")

    human_scores = res["human_scores"]
    any_reason = any(
        isinstance(human_scores[c][d], dict) and human_scores[c][d].get("reason")
        for c in common for d in DIMS
    )
    if any_reason:
        L.append("## 5. 真人评分理由（节选）\n")
        L.append("> 半分制下真人逐维打分与理由，证明校准是**可解释**的对齐，而非黑箱数字巧合。\n")
        for c in common:
            info = res["judge_backup"][c]
            L.append(f"**{info['market']} · {info['title']}**")
            for d in DIMS:
                rec = human_scores[c][d]
                sc = rec.get("score") if isinstance(rec, dict) else rec
                rs = (rec.get("reason") or "").strip() if isinstance(rec, dict) else ""
                L.append(f"- {DIM_LABELS.get(d, d)}：**{sc}**" + (f" — {rs}" if rs else ""))
            L.append("")
    return "\n".join(L)


def build_chart_svg(res: dict, n: int) -> str:
    """真人均值 vs 评委均值分组柱状图（SVG 字符串）。"""
    per_dim = res["per_dim"]
    overall_rho, overall_adj = res["overall_rho"], res["overall_adj"]
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
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W - right}" y2="{y:.1f}" stroke="#eef0f3"/>')
        svg.append(f'<text x="{left - 8}" y="{y + 4:.1f}" font-size="11" fill="#9ca3af" text-anchor="end">{g}</text>')
    for i, d in enumerate(DIMS):
        m = per_dim[d]
        x0 = left + i * (plot_w / len(DIMS)) + gap / 2
        hv, jv = m["h_mean"], m["j_mean"]
        yh = top + plot_h - (hv / maxv) * plot_h
        yj = top + plot_h - (jv / maxv) * plot_h
        svg.append(f'<rect x="{x0:.1f}" y="{yh:.1f}" width="{bw:.1f}" height="{top + plot_h - yh:.1f}" fill="#22c55e"/>')
        svg.append(f'<text x="{x0 + bw / 2:.1f}" y="{yh - 4:.1f}" font-size="10" fill="#16a34a" text-anchor="middle">{hv}</text>')
        xj = x0 + bw
        svg.append(f'<rect x="{xj:.1f}" y="{yj:.1f}" width="{bw:.1f}" height="{top + plot_h - yj:.1f}" fill="#4338ca"/>')
        svg.append(f'<text x="{xj + bw / 2:.1f}" y="{yj - 4:.1f}" font-size="10" fill="#4338ca" text-anchor="middle">{jv}</text>')
        svg.append(f'<text x="{x0 + bw:.1f}" y="{top + plot_h + 16:.1f}" font-size="11" fill="#374151" text-anchor="middle">{DIM_LABELS.get(d, d)}</text>')
    svg.append(f'<rect x="{left}" y="{H - 22}" width="12" height="12" fill="#22c55e"/><text x="{left + 18}" y="{H - 12}" font-size="11" fill="#374151">真人均值</text>')
    svg.append(f'<rect x="{left + 90}" y="{H - 22}" width="12" height="12" fill="#4338ca"/><text x="{left + 108}" y="{H - 12}" font-size="11" fill="#374151">评委均值</text>')
    svg.append(f'<text x="{W - right}" y="{H - 12}" font-size="12" font-weight="700" fill="#4338ca" text-anchor="end">整体ρ={overall_rho:.2f} · 相邻一致{overall_adj:.0%}</text>')
    svg.append('</svg>')
    return "\n".join(svg)


def write_artifacts(res: dict, out_dir, rater: str = "HUMAN") -> None:
    """可选落盘：本地仓库导出 md/svg 供发布脚本提交。目录不存在或只读时静默跳过。"""
    from pathlib import Path
    try:
        out = Path(out_dir)
        if not out.is_dir():
            return
        (out / "calibration_report.md").write_text(build_report_md(res, rater), encoding="utf-8")
        (out / "calibration_chart.svg").write_text(build_chart_svg(res, len(res["common"])), encoding="utf-8")
    except OSError:
        pass
