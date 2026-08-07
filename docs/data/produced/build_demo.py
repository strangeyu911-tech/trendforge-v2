"""读取实跑证据 JSON，生成自播 HTML 演示（端到端闭环全过程）。

用法：python build_demo.py
依赖：同目录 RUN_EVIDENCE_newcontent_2026-08-07.json
产物：DEMO_end2end_newcontent.html（纯 HTML+SVG+JS，无外部依赖，可离线打开/内嵌预览）
"""
from __future__ import annotations
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.join(HERE, "RUN_EVIDENCE_newcontent_2026-08-07.json")
OUT = os.path.join(HERE, "DEMO_end2end_newcontent.html")

def main():
    ev = json.load(open(EVIDENCE, encoding="utf-8"))
    meta = ev["meta"]
    sense = ev["sense"]
    rounds = ev["rounds"]
    drift = ev["drift_series"]

    evidences = sense.get("evidences") or []
    main_n = sum(1 for e in evidences if e.get("is_main"))
    frames = []
    frames.append({
        "type": "sense",
        "title": "① 选题感知 · Sense",
        "topic": sense.get("topic", ""), "angle": sense.get("angle", ""),
        "hook": sense.get("hook", ""), "audience": sense.get("audience", ""),
        "ev_n": len(evidences), "main_n": main_n,
    })
    for r in rounds:
        frames.append({
            "type": "round_pre",
            "round": r["round"],
            "title": f"② 第 {r['round']} 轮 · Writer 初稿（守门前）",
            "drift": r["pre_guard_drift"], "tcs": r.get("pre_tcs"),
            "verdict": r["verdict"], "quality": r["quality_avg"],
            "sections": r["sections"],
            "drift_sections": r.get("pre_drift_sections") or [],
        })
        frames.append({
            "type": "round_post",
            "round": r["round"],
            "title": f"③ 第 {r['round']} 轮 · 守门修复 + 总编裁决",
            "drift": r["post_guard_drift"], "tcs": r.get("post_tcs"),
            "verdict": r["verdict"], "quality": r["quality_avg"],
            "scores": r.get("scores") or {}, "advice": r.get("revision_advice", ""),
            "human": r["human_decision"], "cost": r["cost_cny"],
            "reason": r.get("post_reason", ""),
            "sections": r["sections"],
        })
    final = ev["final_article"]
    secs = []
    if final.get("sections"):
        secs = final["sections"][0] if isinstance(final["sections"], list) and final["sections"] and isinstance(final["sections"][0], list) else final.get("sections") or []
    frames.append({
        "type": "final",
        "title": "✅ 最终发布 · Published",
        "title_txt": final.get("title", ""), "summary": final.get("summary", ""),
        "sections": secs, "final_drift": meta["final_drift"],
        "verdict": meta["final_verdict"], "writer": meta["writer_version"],
        "cost": meta["total_cost_cny"], "rounds": meta["total_rounds"],
    })

    payload = {"meta": meta, "frames": frames, "drift": drift}

    html = HTML_TPL.replace("/*__PAYLOAD__*/", json.dumps(payload, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", OUT, "frames=", len(frames))


HTML_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>TrendForge V2 · 闭环端到端实跑演示</title>
<style>
  :root{
    --bg:#f5f7fb; --card:#ffffff; --ink:#1f2733; --muted:#6b7686;
    --line:#e3e8f0; --red:#e0413e; --green:#1f9d55; --blue:#2f6bff;
    --amber:#e0931f; --chip:#eef2fb;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
    background:var(--bg);color:var(--ink);line-height:1.55}
  .wrap{max-width:1080px;margin:0 auto;padding:22px 18px 60px}
  header{border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
  h1{font-size:22px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px}
  .meta-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
  .pill{background:var(--chip);border:1px solid var(--line);border-radius:999px;
    padding:3px 11px;font-size:12px;color:#33405a}
  .pill b{color:var(--ink)}
  .layout{display:grid;grid-template-columns:1fr 320px;gap:18px}
  @media(max-width:880px){.layout{grid-template-columns:1fr}}
  .stage{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;min-height:420px}
  .stage h2{margin:0 0 12px;font-size:17px}
  .side{display:flex;flex-direction:column;gap:14px}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
  .panel h3{margin:0 0 10px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
  /* drift chart */
  .chartbox{width:100%}
  .legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-top:6px}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle}
  /* gauge */
  .gauge{display:flex;align-items:baseline;gap:8px;margin:6px 0 2px}
  .gauge .v{font-size:34px;font-weight:700}
  .gauge .l{color:var(--muted);font-size:13px}
  .bar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin:4px 0 10px}
  .bar > i{display:block;height:100%;border-radius:6px}
  /* sections */
  .sec{border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:7px 0;background:#fcfdff}
  .sec.drift{border-color:#f3c7c6;background:#fff6f5}
  .sec h4{margin:0 0 3px;font-size:14px;display:flex;align-items:center;gap:8px}
  .tag{font-size:11px;padding:1px 8px;border-radius:999px;font-weight:600}
  .tag.ok{background:#e7f6ee;color:var(--green)}
  .tag.bad{background:#fde8e7;color:var(--red)}
  .sec p{margin:0;color:#3b4658;font-size:13px;max-height:64px;overflow:hidden}
  /* scores */
  .score{margin:5px 0}
  .score .row{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
  .score .bar > i{background:var(--blue)}
  /* verdict / badges */
  .badge{display:inline-block;padding:3px 12px;border-radius:8px;font-weight:700;font-size:13px}
  .badge.pass{background:#e7f6ee;color:var(--green)}
  .badge.revise{background:#fff2dc;color:var(--amber)}
  .badge.reject{background:#fde8e7;color:var(--red)}
  .badge.human{background:#eef2fb;color:var(--blue)}
  .quote{background:var(--chip);border-left:3px solid var(--blue);padding:9px 12px;border-radius:8px;
    font-size:13px;color:#33405a;margin-top:8px;white-space:pre-wrap}
  .article-sec{border-bottom:1px solid var(--line);padding:10px 0}
  .article-sec h4{margin:0 0 4px;font-size:15px}
  .article-sec p{margin:0;font-size:13px;color:#3b4658}
  /* controls */
  .controls{display:flex;gap:8px;align-items:center;margin-top:14px;flex-wrap:wrap}
  button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:9px;padding:7px 13px;cursor:pointer}
  button:hover{background:var(--chip)}
  button.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
  .progress{height:6px;background:var(--line);border-radius:6px;margin-top:12px;overflow:hidden}
  .progress > i{display:block;height:100%;background:var(--blue);transition:width .3s}
  .stepinfo{font-size:12px;color:var(--muted);margin-top:6px}
  .kvs{font-size:13px}
  .kvs div{display:flex;gap:8px;padding:3px 0;border-bottom:1px dashed var(--line)}
  .kvs b{color:var(--muted);font-weight:500;min-width:72px}
  .scroll{max-height:300px;overflow:auto}
  .pub{display:inline-block;margin-top:8px;background:#e7f6ee;color:var(--green);border:1px solid #bfe6cd;
    padding:4px 14px;border-radius:8px;font-weight:700}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>TrendForge V2 · 闭环端到端实跑演示</h1>
    <div class="sub">采纳新版 Prompt（writer@<span id="wv"></span>）后，一条新内容的「产出 → revise → 人审打回 → 多轮重写 → 漂移率归零 → 发布」全过程</div>
    <div class="meta-row" id="metaRow"></div>
  </header>

  <div class="layout">
    <div>
      <div class="stage" id="stage"></div>
      <div class="controls">
        <button id="prev">◀ 上一步</button>
        <button id="play" class="primary">⏸ 暂停</button>
        <button id="next">下一步 ▶</button>
        <button id="replay">⟳ 重播</button>
        <span class="stepinfo" id="stepInfo"></span>
      </div>
      <div class="progress"><i id="prog"></i></div>
    </div>
    <div class="side">
      <div class="panel">
        <h3>漂移率收敛曲线</h3>
        <div class="chartbox" id="chart"></div>
        <div class="legend">
          <span><span class="dot" style="background:var(--red)"></span>守门前（作家自身）</span>
          <span><span class="dot" style="background:var(--green)"></span>守门后（发布前）</span>
        </div>
      </div>
      <div class="panel">
        <h3>本轮快照</h3>
        <div class="kvs" id="snap"></div>
      </div>
    </div>
  </div>
</div>

<script>
const PAYLOAD = /*__PAYLOAD__*/;
const M = PAYLOAD.meta, FR = PAYLOAD.frames, DR = PAYLOAD.drift;
document.getElementById('wv').textContent = M.writer_version || 'v3';
const metaRow = document.getElementById('metaRow');
[['市场',M.market],['采用 Prompt','writer@'+M.writer_version],['总轮数',M.total_rounds],
 ['最终漂移率',M.final_drift],['最终裁决',M.final_verdict],['总成本','¥'+M.total_cost_cny],
 ['总时长',(M.total_duration_ms/1000).toFixed(0)+'s']].forEach(([k,v])=>{
  const s=document.createElement('span');s.className='pill';s.innerHTML=k+': <b>'+v+'</b>';metaRow.appendChild(s);});

let idx=0, timer=null;
const stage=document.getElementById('stage'), snap=document.getElementById('snap'),
      prog=document.getElementById('prog'), stepInfo=document.getElementById('stepInfo');
const W=288,H=150,pad=26;

function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function driftColor(d){return d>0?'var(--red)':'var(--green)';}
function verdictBadge(v){const m={pass:'pass',revise:'revise',reject:'reject'};
  return '<span class="badge '+(m[v]||'revise')+'">'+esc(v)+'</span>';}

function renderChart(curRound){
  const ns=DR.pre_guard.length;
  const maxD=Math.max(0.0001,...DR.pre_guard,...DR.post_guard);
  const x=i=>pad+(W-2*pad)*(ns<=1?0.5:i/(ns-1));
  const y=v=>H-pad-(H-2*pad)*(v/maxD);
  let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  svg+=`<line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="#cbd3e0"/>`;
  svg+=`<line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H-pad}" stroke="#cbd3e0"/>`;
  svg+=`<text x="${W-pad}" y="${H-pad+14}" font-size="9" fill="#9aa4b5" text-anchor="end">轮次→</text>`;
  svg+=`<text x="${pad-4}" y="${pad-8}" font-size="9" fill="#9aa4b5" text-anchor="end">漂移率</text>`;
  const line=(arr,color)=>{
    let d='';arr.forEach((v,i)=>{d+=(i?'L':'M')+x(i)+' '+y(v)+' ';});
    let out=`<path d="${d}" fill="none" stroke="${color}" stroke-width="2.2"/>`;
    arr.forEach((v,i)=>{if(i<curRound||curRound===undefined){out+=`<circle cx="${x(i)}" cy="${y(v)}" r="3.4" fill="${color}"/>`;}});
    return out;};
  svg+=line(DR.pre_guard,'#e0413e');
  svg+=line(DR.post_guard,'#1f9d55');
  svg+=`</svg>`;
  document.getElementById('chart').innerHTML=svg;
}

function renderSnap(f){
  let h='';
  if(f.type==='sense'){
    h+=`<div><b>选题</b> ${esc(f.topic)}</div><div><b>角度</b> ${esc(f.angle)}</div>`+
       `<div><b>证据</b> ${f.ev_n} 条（主干 ${f.main_n}）</div>`;
  } else if(f.type==='round_pre'||f.type==='round_post'){
    h+=`<div><b>轮次</b> 第 ${f.round} 轮</div>`+
       `<div><b>守门${f.type==='round_pre'?'前':'后'}漂移</b> <span style="color:${driftColor(f.drift)};font-weight:700">${f.drift}</span></div>`+
       `<div><b>TCS</b> ${f.tcs}</div>`+
       `<div><b>裁决</b> ${verdictBadge(f.verdict)}</div>`+
       `<div><b>质量均分</b> ${f.quality}</div>`;
  } else if(f.type==='final'){
    h+=`<div><b>最终漂移</b> <span style="color:var(--green);font-weight:700">${f.final_drift}</span></div>`+
       `<div><b>裁决</b> ${verdictBadge(f.verdict)}</div>`+
       `<div><b>采用</b> writer@${esc(f.writer)}</div>`+
       `<div><b>总成本</b> ¥${f.cost}</div>`;
  }
  snap.innerHTML=h;
}

function render(i){
  const f=FR[i];
  let h='';
  if(f.type==='sense'){
    h+=`<h2>${esc(f.title)}</h2>`;
    h+=`<div class="kvs"><div><b>选题</b><span>${esc(f.topic)}</span></div>`+
       `<div><b>切入角度</b><span>${esc(f.angle)}</span></div>`+
       `<div><b>钩子</b><span>${esc(f.hook)}</span></div>`+
       `<div><b>目标受众</b><span>${esc(f.audience)}</span></div>`+
       `<div><b>检索证据</b><span>${f.ev_n} 条（主干 ${f.main_n} 条）</span></div></div>`;
    h+=`<p class="stepinfo">Sense 段一次性选定选题与证据，后续多轮 produce 复用同一 brief/证据——唯一变量是 Writer 吃到的「修改意见」，从而隔离「人审反馈」单一因素。</p>`;
  } else if(f.type==='round_pre'){
    h+=`<h2>${esc(f.title)}</h2>`;
    h+=`<div class="gauge"><span class="v" style="color:${driftColor(f.drift)}">${f.drift}</span>`+
       `<span class="l">守门前漂移率（作家自身）</span></div>`;
    h+=`<div class="bar"><i style="width:${Math.min(100,f.drift*100)}%;background:${driftColor(f.drift)}"></i></div>`;
    h+=`<p class="stepinfo">Writer 用采纳版 v3 写出初稿（每节延续钩子、持续信息增量），但首稿仍可能在部分小节引用背景文档而「漂移」。下方红标 = 被 TCS 判为脱离主线的节。</p>`;
    (f.sections||[]).forEach(s=>{
      h+=`<div class="sec ${s.drift?'drift':''}"><h4>${esc(s.heading)}`+
         (s.drift?`<span class="tag bad">漂移</span>`:`<span class="tag ok">主线</span>`)+`</h4>`+
         `<p>${esc(s.text)}</p></div>`;});
  } else if(f.type==='round_post'){
    h+=`<h2>${esc(f.title)}</h2>`;
    h+=`<div class="gauge"><span class="v" style="color:${driftColor(f.drift)}">${f.drift}</span>`+
       `<span class="l">守门后漂移率（发布前）</span></div>`;
    h+=`<div class="bar"><i style="width:${Math.min(100,f.drift*100)}%;background:${driftColor(f.drift)}"></i></div>`;
    h+=`<div style="margin:8px 0">${verdictBadge(f.verdict)} <span class="badge human">人审：${f.human.includes('打回')?'打回重写':'通过'}</span></div>`;
    h+=`<p class="stepinfo">TopicGuard 对漂移节用「仅主干证据」定点重写（或摘除保底），FactChecker/Editor 再裁决。本轮 TCS=${esc(f.tcs)}：${esc(f.reason)}</p>`;
    if(f.scores&&Object.keys(f.scores).length){
      h+=`<div style="margin:6px 0 2px;color:var(--muted);font-size:12px">质量 Rubric（${f.quality}/5）</div>`;
      for(const k in f.scores){const v=f.scores[k];
        h+=`<div class="score"><div class="row"><span>${esc(k)}</span><span>${v}/5</span></div>`+
           `<div class="bar"><i style="width:${v/5*100}%"></i></div></div>`;}
    }
    if(f.advice){h+=`<div class="quote"><b>总编修改意见：</b>\n${esc(f.advice)}</div>`;}
    h+=`<div class="quote" style="border-color:var(--blue)"><b>人审决策：</b>${esc(f.human)}（本轮成本 ¥${f.cost}）</div>`;
  } else if(f.type==='final'){
    h+=`<h2>${esc(f.title)}</h2>`;
    h+=`<div class="pub">已发布 · 漂移率 ${esc(f.final_drift)} · 裁决 ${esc(f.verdict)}</div>`;
    h+=`<h3 style="margin:14px 0 4px;font-size:16px">${esc(f.title_txt)}</h3>`;
    h+=`<p style="color:#3b4658">${esc(f.summary)}</p>`;
    h+=`<div class="scroll">`;
    (f.sections||[]).forEach(s=>{h+=`<div class="article-sec"><h4>${esc(s.heading)}</h4><p>${esc(s.text)}</p></div>`;});
    h+=`</div>`;
    h+=`<p class="stepinfo">历经 ${f.rounds} 轮「revise→人审打回→重写」，守门后漂移率降至 ${esc(f.final_drift)}，达到发布阈值。整条内容由 writer@${esc(f.writer)} 产出，全程审计落库。</p>`;
  }
  stage.innerHTML=h;
  renderSnap(f);
  renderChart(f.type==='round_pre'||f.type==='round_post'?f.round:undefined);
  prog.style.width=((i+1)/FR.length*100)+'%';
  stepInfo.textContent='步骤 '+(i+1)+' / '+FR.length+' · '+f.title;
}

function next(){if(idx<FR.length-1){idx++;render(idx);}else{pause();}}
function prev(){if(idx>0){idx--;render(idx);}}
function play(){if(timer)return;timer=setInterval(()=>{if(idx>=FR.length-1){pause();return;}next();},3400);
  document.getElementById('play').textContent='⏸ 暂停';}
function pause(){if(timer){clearInterval(timer);timer=null;}document.getElementById('play').textContent='▶ 播放';}
document.getElementById('next').onclick=()=>{pause();next();};
document.getElementById('prev').onclick=()=>{pause();prev();};
document.getElementById('play').onclick=()=>{timer?pause():play();};
document.getElementById('replay').onclick=()=>{idx=0;render(0);play();};
render(0);play();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
