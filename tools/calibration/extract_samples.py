"""
extract_samples.py — 校准数据集抽取

从 demo_snapshot.db 抽取已发布内容的「评委五维分」与「正文片段」，
产出两份文件：
  - scoring_input.json : 给真人标注员看的内容（标题 + 片段 + 市场），**不暴露评委分**
  - samples_judge.json : 给 compute_alignment.py 用的评委分备份（id -> 五维分）

同时生成 score_sheet.html（数据内联，双击即用的本地打分页）。

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


HTML_TEMPLATE = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>TrendForge 评委校准 · 真人打分</title>
<style>
  body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#1f2329;background:#fafbfc}}
  h1{{font-size:20px}} .meta{{color:#6b7280;font-size:13px;margin-bottom:8px}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
  .card h2{{font-size:15px;margin:0 0 4px}}
  .badge{{display:inline-block;font-size:12px;background:#eef2ff;color:#4338ca;border-radius:6px;padding:1px 8px;margin-right:6px}}
  .excerpt{{font-size:13px;color:#374151;background:#f8fafc;border-left:3px solid #c7d2fe;padding:8px 10px;margin:8px 0;max-height:360px;overflow:auto;line-height:1.7;white-space:pre-wrap}}
  .dimblk{{margin:6px 0 2px}}
  .dim{{display:flex;align-items:center;gap:10px;margin:4px 0}}
  .dim label{{width:110px;font-size:13px}}
  .dim input[type=range]{{flex:1}}
  .dim .val{{width:34px;text-align:center;font-weight:600;color:#4338ca}}
  .reason{{width:100%;font-size:12px;margin:2px 0 4px;box-sizing:border-box;resize:vertical;min-height:32px;border:1px solid #e5e7eb;border-radius:6px;padding:5px 7px;font-family:inherit;color:#374151}}
  .progress{{position:sticky;top:0;background:#fff;border-bottom:1px solid #e5e7eb;padding:10px 0;font-size:13px;z-index:5}}
  button{{background:#4338ca;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:14px;cursor:pointer}}
  button.sec{{background:#fff;color:#4338ca;border:1px solid #c7d2fe}}
  .done{{color:#16a34a;font-weight:600}}
  .hint{{font-size:12px;color:#9ca3af}}
</style>
</head>
<body>
<h1>🎯 TrendForge 评委校准 · 真人打分</h1>
<div class="meta">逐条阅读内容片段，按五维直觉打分（{scale}）。评委分对你<b>不可见</b>，避免锚定。完成后点「导出 human_scores.json」。</div>
<div class="progress" id="progress">已打分 0 / {n}</div>
<div id="cards"></div>
<div style="margin:18px 0">
  <button onclick="exportJSON()">导出 human_scores.json</button>
  <button class="sec" onclick="document.getElementById('file').click()">载入已有分（续打）</button>
  <input type="file" id="file" accept="application/json" style="display:none" onchange="loadJSON(event)">
  <span class="hint"> 导出后把文件放到 tools/calibration/ 下，运行 compute_alignment.py</span>
</div>
<script>
const DIMS = {dims_json};
const LABELS = {labels_json};
const SAMPLES = {samples_json};
const SCALE_MAX = 5;
let scores = {{}};  // id -> {{dim: val}}

function render() {{
  const root = document.getElementById('cards');
  root.innerHTML = '';
  SAMPLES.forEach((s, i) => {{
    const card = document.createElement('div'); card.className='card';
    let dimsHtml = '';
    DIMS.forEach(d => {{
      const rec = (scores[s.id] && scores[s.id][d]) || {{}};
      const sc = (rec && typeof rec==='object' && rec.score!==undefined) ? rec.score : (typeof rec==='number' ? rec : 3);
      const rs = (rec && typeof rec==='object') ? (rec.reason||'') : '';
      dimsHtml += `<div class="dimblk">
        <div class="dim"><label>${{LABELS[d]}}</label>
          <input type="range" min="1" max="5" step="0.5" value="${{sc}}" oninput="setScore('${{s.id}}','${{d}}',this.value)">
          <span class="val" id="v-${{s.id}}-${{d}}">${{sc}}</span></div>
        <textarea class="reason" placeholder="这一维的打分理由（可选，会一并导出）" oninput="setReason('${{s.id}}','${{d}}',this.value)">${{rs}}</textarea>
      </div>`;
    }});
    card.innerHTML = `<h2><span class="badge">${{s.market}}</span>${{i+1}}. ${{s.title}}</h2>
      <div class="excerpt">${{s.excerpt || '（无片段）'}}</div>${{dimsHtml}}`;
    root.appendChild(card);
  }});
  updateProgress();
}}
function setScore(id, dim, v) {{
  scores[id] = scores[id] || {{}};
  const cur = (scores[id][dim] && typeof scores[id][dim]==='object') ? scores[id][dim] : scores[id][dim] || {{}};
  const rec = (scores[id][dim] && typeof scores[id][dim]==='object') ? scores[id][dim] : {{}};
  rec.score = parseFloat(v);
  scores[id][dim] = rec;
  document.getElementById(`v-${{id}}-${{dim}}`).textContent = v;
  updateProgress();
}}
function setReason(id, dim, v) {{
  scores[id] = scores[id] || {{}};
  const rec = (scores[id][dim] && typeof scores[id][dim]==='object') ? scores[id][dim] : {{}};
  rec.score = rec.score!==undefined ? rec.score : 3;
  rec.reason = v;
  scores[id][dim] = rec;
}}
function updateProgress() {{
  const done = Object.keys(scores).filter(id => DIMS.every(d => {{
    const r = scores[id][d];
    return r && ((typeof r==='object' && r.score!==undefined) || typeof r==='number');
  }})).length;
  const el = document.getElementById('progress');
  el.innerHTML = `已打分 <b>${{done}}</b> / ${{SAMPLES.length}}` + (done===SAMPLES.length ? ' <span class="done">✓ 全部完成</span>' : '');
}}
function exportJSON() {{
  const out = {{'__meta':{{'rater':'HUMAN','scale':'1-5 (支持0.5半分)','dims':DIMS,'has_reasons':true}},'scores':scores}};
  const blob = new Blob([JSON.stringify(out,null,2)], {{type:'application/json'}});
  const a = document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='human_scores.json'; a.click();
}}
function loadJSON(e) {{
  const f = e.target.files[0]; if(!f) return;
  const r = new FileReader();
  r.onload = ev => {{ try {{ const d=JSON.parse(ev.target.result); scores=d.scores||{{}}; render(); }} catch(err){{ alert('载入失败: '+err); }} }};
  r.readAsText(f);
}}
render();
</script>
</body>
</html>
"""


def build_html(scoring_input: list) -> str:
    return HTML_TEMPLATE.format(
        scale=SCALE,
        n=len(scoring_input),
        dims_json=json.dumps(DIMS, ensure_ascii=False),
        labels_json=json.dumps(DIM_LABELS, ensure_ascii=False),
        samples_json=json.dumps(scoring_input, ensure_ascii=False),
    )


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
    (out_dir / "score_sheet.html").write_text(build_html(scoring_input), encoding="utf-8")

    print(f"抽取 {len(scoring_input)} 条 →")
    print(f"  scoring_input.json  (真人打分用, 已隐去评委分)")
    print(f"  samples_judge.json  (评委分备份, 计算用)")
    print(f"  score_sheet.html    (双击打开打分)")


if __name__ == "__main__":
    main()
