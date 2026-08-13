"""
extract_samples.py — 校准数据集抽取

从 demo_snapshot.db 抽取已发布内容的「评委五维分」与「正文片段」，
产出两份数据文件（供离线检查 / 可选 CLI 计算用）：
  - scoring_input.json : 给真人标注员看的内容（标题 + 片段 + 市场），**不暴露评委分**
  - samples_judge.json : 评委分备份（id -> 五维分）

注意：正式校准已改为控制台 DB 驱动（/console/#calibrate），此脚本仅作数据导出/备份，
不再生成离线打分页（旧 score_sheet.html 已退役）。

用法：
  python tools/calibration/extract_samples.py [--db src/app/data/demo_snapshot.db] [--limit 13]
"""
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path

DIMS = ["accuracy", "angle", "readability", "local_fit", "engagement"]
DIM_LABELS = {
    "accuracy": "事实准确性",
    "angle": "角度新颖度",
    "readability": "可读性",
    "local_fit": "本地化契合",
    "engagement": "吸引力/传播潜力",
}
SCALE = "1–5（支持 0.5 半分，如 2.5 / 3.5；1=差，5=优）"


def body_to_text(body) -> str:
    """正文可能是 JSON 列表/字典，提取纯文本。"""
    if body is None:
        return ""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return body
    if isinstance(body, dict):
        secs = body.get("sections") or []
        return "\n".join(s.get("text", "") for s in secs if isinstance(s, dict))
    if isinstance(body, list):
        out = []
        for s in body:
            if isinstance(s, dict):
                out.append(s.get("text", ""))
            elif isinstance(s, str):
                out.append(s)
        return "\n".join(out)
    return str(body)


def extract(db_path: str, limit: int):
    c = sqlite3.connect(db_path)
    cur = c.cursor()
    cur.execute(
        "SELECT id, market, language, status, title, body, quality "
        "FROM contents ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    c.close()

    scoring_input, judge_backup = [], {}
    for r in rows:
        cid, market, lang, status, title, body, quality = r
        q = json.loads(quality) if quality else {}
        scores = q.get("scores") or {}
        # 只保留五维齐全的样本
        if not all(d in scores for d in DIMS):
            continue
        text = body_to_text(body)
        # 全文展示，不做任何截断（真人需要看完整内容才能公平打分）
        excerpt = text.strip()
        scoring_input.append({
            "id": cid,
            "market": market,
            "language": lang or "",
            "status": status,
            "title": title,
            "excerpt": excerpt,
        })
        judge_backup[cid] = {
            "market": market,
            "status": status,
            "title": title,
            "judge_scores": {d: float(scores[d]) for d in DIMS},
            "judge_avg": round(sum(float(scores[d]) for d in DIMS) / len(DIMS), 2),
        }
        if len(scoring_input) >= limit:
            break
    return scoring_input, judge_backup


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--db", default=str(here.parent.parent / "src" / "app" / "data" / "demo_snapshot.db"))
    ap.add_argument("--limit", type=int, default=13)
    args = ap.parse_args()

    scoring_input, judge_backup = extract(args.db, args.limit)
    if not scoring_input:
        print("未抽到合格样本（需五维分齐全），请检查 DB。", file=sys.stderr)
        sys.exit(1)

    out_dir = here
    (out_dir / "scoring_input.json").write_text(json.dumps(scoring_input, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "samples_judge.json").write_text(json.dumps(judge_backup, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"抽取 {len(scoring_input)} 条 →")
    print(f"  scoring_input.json  (真人打分用, 已隐去评委分)")
    print(f"  samples_judge.json  (评委分备份, 计算用)")


if __name__ == "__main__":
    main()
