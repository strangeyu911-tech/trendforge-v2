"""
compute_alignment.py — 评委校准 CLI（薄封装）

实现对齐计算/报告生成的唯一实现已收编至 src/app/services/alignment.py
（服务端 API 内存生成报告，不再依赖本目录文件）。本文件保留 CLI 旧用法：

    python compute_alignment.py [--human human_scores.json] [--judge samples_judge.json]

从 json 文件构造输入，调用库内实现，并把 calibration_report.md / calibration_chart.svg
写到本目录（与旧行为一致，供「发布校准报告.bat」提交）。
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"   # tools/calibration -> 仓库根 -> src

try:
    sys.path.insert(0, str(SRC))
    from app.services.alignment import (  # noqa: E402
        DIMS, _get_score, build_chart_svg, build_report_md, compute_core,
    )
except ImportError:
    print("无法导入 src/app/services/alignment.py：请确认从仓库根目录运行且 src/ 完整。", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human", default=str(HERE / "human_scores.json"))
    ap.add_argument("--judge", default=str(HERE / "samples_judge.json"))
    args = ap.parse_args()

    human_path = Path(args.human)
    if not human_path.exists():
        print(f"找不到 {human_path}。请通过正式控制台 /console/#calibrate 提交真人打分（数据自动落库），或指定 --human 参数指向导出的评分文件。", file=sys.stderr)
        sys.exit(1)

    human_doc = json.loads(human_path.read_text(encoding="utf-8"))
    human_scores = human_doc.get("scores", human_doc)
    judge_backup_in = json.loads(Path(args.judge).read_text(encoding="utf-8"))

    # 由文件构造聚合映射（CLI 路径：单评审人，无需跨人平均）
    human_map = {c: {d: _get_score(human_scores[c][d]) for d in DIMS}
                 for c in human_scores if all(d in human_scores[c] for d in DIMS)}
    reasons_map = {c: {d: (human_scores[c][d].get("reason", "") if isinstance(human_scores[c][d], dict) else "")
                       for d in DIMS} for c in human_map}
    judge_map = {c: {d: float(judge_backup_in[c]["judge_scores"][d]) for d in DIMS}
                 for c in judge_backup_in if all(d in judge_backup_in[c].get("judge_scores", {}) for d in DIMS)}
    meta = {c: {"market": judge_backup_in[c].get("market", ""), "title": judge_backup_in[c].get("title", "")}
            for c in judge_map}

    try:
        res = compute_core(human_map, judge_map, meta, reasons_map=reasons_map)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    rater = human_doc.get("__meta", {}).get("rater", "UNKNOWN")
    (HERE / "calibration_report.md").write_text(build_report_md(res, rater), encoding="utf-8")
    (HERE / "calibration_chart.svg").write_text(build_chart_svg(res, len(res["common"])), encoding="utf-8")

    print(f"样本数 {len(res['common'])} | 整体 Spearman={res['overall_rho']:.3f} "
          f"| 相邻一致={res['overall_adj']:.1%} | 完全一致={res['overall_exact']:.1%}")
    print(f"报告 → {HERE / 'calibration_report.md'}")


if __name__ == "__main__":
    main()
