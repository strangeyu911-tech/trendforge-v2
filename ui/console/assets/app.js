/* TrendForge V2 控制台 SPA（最简 MVP 版） */
const root = document.getElementById('view-root');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const VIEWS = { overview, pipeline, contents, markets, eval: evalView, kb: kbView, analytics: analyticsView, calibrate: calibrateView, closedloop: closedLoopView };

function route() {
  const hash = location.hash.slice(1) || 'overview';
  const [view, ...rest] = hash.split('/');
  document.querySelectorAll('#sidebar nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.view === view));
  if (view === 'content' && rest[0]) return contentDetail(rest[0]);
  (VIEWS[view] || overview)();
}
window.addEventListener('hashchange', route);

/* ---------- 全局供给任务状态 ----------
   关键：轮询与状态存活于视图之外（模块级 + localStorage），
   切换页面/刷新浏览器都不会中断正在运行的供给任务。 */
const RUN_KEY = 'tf_active_run';
const RESULT_TTL = 30 * 60 * 1000; // 已完成结果保留 30 分钟

const RunState = {
  job: null,      // {job_id, market, status, progress, content_id, error, started_at, updated_at}
  timer: null,
  miss: 0,

  init() {
    try { this.job = JSON.parse(localStorage.getItem(RUN_KEY) || 'null'); } catch (e) { this.job = null; }
    // 陈旧的已完成结果不再恢复
    if (this.job && this.job.status !== 'running' && this.job.status !== 'starting'
        && Date.now() - (this.job.updated_at || 0) > RESULT_TTL) {
      this.job = null;
      try { localStorage.removeItem(RUN_KEY); } catch (e) { }
    }
    if (this.job && (this.job.status === 'running' || this.job.status === 'starting')) this.startPolling();
    this.paint();
  },

  set(patch) {
    this.job = Object.assign({}, this.job, patch, { updated_at: Date.now() });
    try { localStorage.setItem(RUN_KEY, JSON.stringify(this.job)); } catch (e) { }
    this.paint();
  },

  clear() {
    this.stopPolling();
    this.job = null;
    try { localStorage.removeItem(RUN_KEY); } catch (e) { }
    this.paint();
  },

  startPolling() {
    if (this.timer) return;               // 全局唯一，杜绝重复 timer 泄漏
    this.miss = 0;
    this.timer = setInterval(() => this.tick(), 5000);
  },

  stopPolling() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  },

  async tick() {
    if (!this.job || !this.job.job_id) { this.stopPolling(); return; }
    let j;
    try { j = await API.job(this.job.job_id); }
    catch (e) { this.paint(); return; }   // 网络抖动/冷启动：保持轮询，下次再试
    if (j.status === 'done') {
      this.stopPolling();
      this.set({ status: 'done', progress: '', content_id: (j.result || {}).content_id });
      if (currentView() === 'pipeline') loadTasks();
    } else if (j.status === 'failed') {
      this.stopPolling();
      this.set({ status: 'failed', error: j.error || '未知错误' });
      if (currentView() === 'pipeline') loadTasks();
    } else if (j.status === 'unknown') {
      // 后端重启会丢失内存中的 JOBS 表，容忍 3 次后判定失联
      if (++this.miss >= 3) { this.stopPolling(); this.set({ status: 'lost' }); }
      else this.paint();
    } else {
      this.miss = 0;
      this.set({ status: 'running', progress: j.progress || this.job.progress || '' });
    }
  },

  paint() { this.paintBox(); this.paintBadge(); },

  paintBox() {
    const box = document.getElementById('job-box');
    const busy = !!this.job && (this.job.status === 'running' || this.job.status === 'starting');
    [document.getElementById('run-btn'), document.getElementById('force-btn')].forEach(b => {
      if (b) b.disabled = busy;
    });
    if (!box) return;                     // 当前不在「跑供给」视图，只更新侧边栏徽标
    const j = this.job;
    if (!j) { box.className = 'job-box'; box.innerHTML = ''; return; }
    box.className = 'job-box show';
    const close = '<button class="job-close" title="清除">✕</button>';
    if (j.status === 'starting') {
      box.innerHTML = '发起中…';
    } else if (j.status === 'running') {
      box.innerHTML = `⏳ 流水线运行中 · <b>${esc(j.market || '')}</b>${j.job_id ? ` · job ${esc(j.job_id.slice(0, 8))}` : ''}
        ${j.progress ? ` · 当前 Agent：<b>${esc(j.progress)}</b>` : ' · 10 个 Agent 依次执行，约 2-5 分钟'}
        <span class="job-elapsed">已运行 ${fmtElapsed(j.started_at)}</span>
        <br><small style="color:#77809a">任务在服务端运行，切换页面或刷新浏览器都不会中断，可随时回来查看进度。</small>`;
    } else if (j.status === 'cached') {
      box.innerHTML = `${close}✅ 命中缓存（秒开，零额度消耗）→ <a class="link" href="#content/${esc(j.content_id)}">查看内容</a>`;
    } else if (j.status === 'done') {
      box.innerHTML = `${close}✅ 供给完成（耗时 ${fmtElapsed(j.started_at, j.updated_at)}）→ <a class="link" href="#content/${esc(j.content_id)}">查看内容与 Trace</a>`;
    } else if (j.status === 'failed') {
      box.innerHTML = `${close}❌ 失败：${esc(j.error)}`;
    } else if (j.status === 'error') {
      box.innerHTML = `${close}❌ 发起失败：${esc(j.error)}`;
    } else if (j.status === 'lost') {
      box.innerHTML = `${close}⚠️ 运行状态失联（后端可能已重启或休眠），任务结果请查看下方「运行历史」。`;
    }
    const btn = box.querySelector('.job-close');
    if (btn) btn.onclick = () => this.clear();
  },

  paintBadge() {
    const link = document.querySelector('#sidebar nav a[data-view="pipeline"]');
    if (!link) return;
    let b = link.querySelector('.nav-badge');
    const j = this.job;
    const map = { running: ['运行中', 'running'], starting: ['运行中', 'running'], done: ['完成', 'done'], cached: ['完成', 'done'], failed: ['失败', 'failed'], error: ['失败', 'failed'], lost: ['失联', 'failed'] };
    const hit = j && map[j.status];
    if (!hit) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement('span'); b.className = 'nav-badge'; link.appendChild(b); }
    b.textContent = hit[0];
    b.className = `nav-badge ${hit[1]}`;
  },
};

/* ---------- 全局内容修订任务状态 ----------
   与 RunState 完全同构：轮询与状态存活于视图之外（模块级 + localStorage），
   切换页面/刷新浏览器都不会中断正在运行的重写，侧边栏「内容」导航常驻徽标。 */
const REVISE_KEY = 'tf_active_revise';

const ReviseState = {
  job: null,      // {job_id, content_id, title, status, progress, error, started_at, updated_at}
  timer: null,
  miss: 0,

  init() {
    try { this.job = JSON.parse(localStorage.getItem(REVISE_KEY) || 'null'); } catch (e) { this.job = null; }
    if (this.job && (this.job.status === 'running' || this.job.status === 'starting')) this.startPolling();
    this.paint();
  },

  set(patch) {
    this.job = Object.assign({}, this.job, patch, { updated_at: Date.now() });
    try { localStorage.setItem(REVISE_KEY, JSON.stringify(this.job)); } catch (e) { }
    this.paint();
  },

  clear() {
    this.stopPolling();
    this.job = null;
    try { localStorage.removeItem(REVISE_KEY); } catch (e) { }
    this.paint();
  },

  startPolling() {
    if (this.timer) return;               // 全局唯一，杜绝重复 timer 泄漏
    this.miss = 0;
    this.timer = setInterval(() => this.tick(), 5000);
  },

  stopPolling() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  },

  async tick() {
    if (!this.job || !this.job.job_id) { this.stopPolling(); return; }
    let j;
    try { j = await API.contentReviseJob(this.job.job_id); }
    catch (e) { this.paint(); return; }   // 网络抖动/冷启动：保持轮询，下次再试
    const here = () => currentView() === 'content' && contentIdFromHash() === this.job.content_id;
    if (j.status === 'done') {
      this.stopPolling();
      this.set({ status: 'done' });
      if (here()) await contentDetail(this.job.content_id);
      setTimeout(() => { if (this.job && this.job.status === 'done') this.clear(); }, 5000);
    } else if (j.status === 'failed') {
      this.stopPolling();
      this.set({ status: 'failed', error: j.error || '未知错误' });
      if (here()) await contentDetail(this.job.content_id);
    } else if (j.status === 'unknown') {
      // 免费层休眠/重启会丢失内存任务表：若内容已回到 published 说明任务其实跑完了
      let published = false;
      try { const c = await API.content(this.job.content_id); published = (c.status === 'published'); } catch (e) { }
      if (published) {
        this.stopPolling();
        this.set({ status: 'done' });
        if (here()) await contentDetail(this.job.content_id);
        setTimeout(() => { if (this.job && this.job.status === 'done') this.clear(); }, 5000);
      } else if (++this.miss >= 3) {
        this.stopPolling(); this.set({ status: 'lost' });
      } else this.paint();
    } else {
      this.miss = 0;
      this.set({ status: 'running', progress: j.progress || this.job.progress || 'Agent 执行中' });
    }
  },

  paint() { this.paintBadge(); this.paintInline(); },

  paintBadge() {
    const link = document.querySelector('#sidebar nav a[data-view="contents"]');
    if (!link) return;
    let b = link.querySelector('.nav-badge');
    const j = this.job;
    const map = { running: ['重写中', 'running'], starting: ['重写中', 'running'], done: ['完成', 'done'], failed: ['失败', 'failed'], lost: ['失联', 'failed'] };
    const hit = j && map[j.status];
    if (!hit) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement('span'); b.className = 'nav-badge'; link.appendChild(b); }
    b.textContent = hit[0];
    b.className = `nav-badge ${hit[1]}`;
  },

  paintInline() {
    const el = document.getElementById('revise-status');
    if (!el) return;                       // 当前不在该内容详情页
    const btn = document.getElementById('btn-revise');
    const j = this.job;
    const activeHere = j && j.content_id === contentIdFromHash();
    if (!activeHere) { el.style.display = 'none'; if (btn) btn.style.display = ''; return; }
    if (btn) btn.style.display = 'none';
    el.style.display = '';
    if (j.status === 'starting') {
      el.className = 'revise-status'; el.innerHTML = '⏳ 发起重写任务…';
    } else if (j.status === 'running') {
      el.className = 'revise-status';
      el.innerHTML = `⏳ 正在按修改意见重写 · 当前环节：<b>${esc(j.progress || 'Agent 执行中')}</b>
        <span class="job-elapsed">已运行 ${fmtElapsed(j.started_at)}</span>
        <br><small style="color:#77809a">任务在服务端运行，切换页面或刷新浏览器都不会中断，可随时回来查看进度。</small>`;
    } else if (j.status === 'done') {
      el.className = 'revise-status done'; el.innerHTML = `✅ 重写完成，内容已更新（可再次点击「按修改意见重写」重跑）`;
    } else if (j.status === 'failed') {
      el.className = 'revise-status failed'; el.innerHTML = `❌ 重写失败：${esc(j.error || '')}`;
    } else if (j.status === 'lost') {
      el.className = 'revise-status failed'; el.innerHTML = `⚠️ 运行状态失联（后端可能已重启）。内容状态仍可在本页查看；如长时间无更新，请重新点击「按修改意见重写」。`;
    } else {
      el.style.display = 'none';
    }
  },
};

function currentView() { return (location.hash.slice(1) || 'overview').split('/')[0]; }

function contentIdFromHash() { const h = location.hash.slice(1).split('/'); return h[0] === 'content' ? h[1] : null; }

function fmtElapsed(from, to) {
  if (!from) return '—';
  const s = Math.max(0, Math.floor(((to || Date.now()) - from) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
}

// 后端存的是 UTC（datetime.utcnow，无时区标记）。按 UTC 解析后转浏览器本地时区显示，
// 保证与用户电脑系统时钟一致（服务器在 UTC，直接显示会早 8 小时）。
// 兼容两种串：带偏移的 "2026-08-05T12:00:00+00:00" 与裸 UTC "2026-08-05T12:00:00"。
function fmtTime(iso) {
  if (!iso) return '—';
  let s = String(iso);
  if (!/[Zz]$|[+\-]\d{2}:?\d{2}$/.test(s)) s = s + 'Z';  // 裸 UTC 串补 Z
  const d = new Date(s);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

/* ---------- 仪表盘 ---------- */
async function overview() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [h, c] = await Promise.all([API.health(), API.contents()]);
    const contents = c.contents;
    const byMarket = {};
    contents.forEach(x => byMarket[x.market] = (byMarket[x.market] || 0) + 1);
    root.innerHTML = `
      <h1 class="page-title">仪表盘</h1>
      <p class="page-sub">AI Native 内容供给引擎 · 运行概览</p>
      <div class="cards">
        <div class="card"><div class="kpi">${contents.length}</div><div class="kpi-label">已供给内容</div></div>
        <div class="card"><div class="kpi">${h.kb.documents}</div><div class="kpi-label">知识库文档</div></div>
        <div class="card"><div class="kpi">${h.kb.chunks}</div><div class="kpi-label">知识库分块</div></div>
        <div class="card"><div class="kpi">${Object.keys(byMarket).length}</div><div class="kpi-label">覆盖市场</div></div>
      </div>
      <div class="panel"><h3>系统状态</h3>
        <p>LLM：${esc(h.llm.model)} ${h.llm.configured ? '<span class="tag green">已配置</span>' : '<span class="tag red">未配置（兜底模式）</span>'}</p>
        <p style="margin-top:8px">市场分布：${Object.entries(byMarket).map(([m, n]) => `<span class="tag">${m} × ${n}</span>`).join('') || '暂无'}</p>
      </div>
      <div class="panel"><h3>最新供给</h3>${contentsTable(contents.slice(0, 8))}</div>`;
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 跑供给 ---------- */
async function pipeline() {
  const markets = await API.markets().catch(() => ({ markets: [] }));
  root.innerHTML = `
    <h1 class="page-title">跑供给</h1>
    <p class="page-sub">选定目标市场 → 10 个 Agent 依次执行 → 产出母稿 + 多形态 + 分发计划</p>
    <div class="toolbar">
      <select id="mk">${markets.markets.map(m => `<option value="${m.code}">${m.name} (${m.code})</option>`).join('')}</select>
      <button class="btn" id="run-btn">开始供给</button>
      <button class="btn ghost" id="force-btn">强制重跑（忽略缓存）</button>
    </div>
    <div class="job-box" id="job-box"></div>
    <div class="panel" id="tasks-panel"><h3>运行历史</h3><div class="loading">加载中…</div></div>`;
  loadTasks();
  document.getElementById('run-btn').onclick = () => startRun(false);
  document.getElementById('force-btn').onclick = () => startRun(true);
  // 视图重建后恢复正在运行/已完成的任务展示（切页面回来不会“看起来停了”）
  const active = RunState.job;
  if (active && active.market) {
    const sel = document.getElementById('mk');
    if (sel && [...sel.options].some(o => o.value === active.market)) sel.value = active.market;
  }
  RunState.paint();
}

async function startRun(force) {
  if (RunState.job && (RunState.job.status === 'running' || RunState.job.status === 'starting')) return;
  const market = document.getElementById('mk').value;
  RunState.clear();
  RunState.set({ status: 'starting', market, started_at: Date.now() });
  try {
    const r = await API.runPipeline(market, force);
    if (r.cached) {
      RunState.set({ status: 'cached', job_id: null, content_id: r.content_id });
      return;
    }
    RunState.set({ status: 'running', job_id: r.job_id, progress: '', started_at: Date.now() });
    RunState.startPolling();
  } catch (e) {
    RunState.set({ status: 'error', error: e.message });
  }
}

async function loadTasks() {
  const panel = document.getElementById('tasks-panel');
  try {
    const t = await API.tasks();
    panel.innerHTML = `<h3>运行历史</h3><table>
      <tr><th>市场</th><th>状态</th><th>结果</th><th>耗时</th><th>成本(¥)</th><th>时间</th></tr>
      ${t.tasks.map(x => `<tr>
        <td>${x.market}</td>
        <td>${statusTag(x.status)}</td>
        <td>${x.output && x.output.content_id ? `<a class="link" href="#content/${x.output.content_id}">${esc((x.output.title || '').slice(0, 30))}</a>` : esc((x.error || '').slice(0, 40))}</td>
        <td>${(x.total_duration_ms / 1000).toFixed(0)}s</td><td>${x.total_cost_cny.toFixed(4)}</td>
        <td>${fmtTime(x.created_at)}</td></tr>`).join('') || '<tr><td colspan="6">暂无运行</td></tr>'}
    </table>`;
  } catch (e) { panel.innerHTML = errBox(e); }
}

/* ---------- 内容列表 ---------- */
async function contents() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const c = await API.contents();
    root.innerHTML = `
      <h1 class="page-title">内容</h1>
      <p class="page-sub">每条内容 = 母稿 + 多形态派生 + 分发计划 + 完整 Trace</p>
      <div class="panel">${contentsTable(c.contents)}</div>`;
  } catch (e) { root.innerHTML = errBox(e); }
}

function contentsTable(list) {
  return `<table>
    <tr><th>标题</th><th>市场</th><th>质量</th><th>形态</th><th>时间</th></tr>
    ${list.map(x => `<tr>
      <td><a class="link" href="#content/${x.id}">${esc(x.title.slice(0, 46))}</a>
        ${x.is_fallback ? '<span class="tag orange">兜底</span>' : ''}</td>
      <td><span class="tag">${x.market}</span></td>
      <td>${x.quality_avg ? x.quality_avg.toFixed(1) : '-'}</td>
      <td>${x.formats.map(f => `<span class="tag gray">${esc(FMT_META[f]?.label || f)}</span>`).join('')}</td>
      <td>${fmtTime(x.created_at)}</td></tr>`).join('') || '<tr><td colspan="5">暂无内容，先去「跑供给」</td></tr>'}
  </table>`;
}

/* ---------- 内容详情 ---------- */
/* 中文对照状态：非中文市场的产出需要给中文运营一份回译对照，按需生成 + 缓存 */
const ZH = {
  mode: localStorage.getItem('tf_zh_mode') || 'zh',  // zh 仅中文(默认) | both 双语 | src 原文
  status: 'none',   // none 未生成 | loading | ready | unavailable
  reason: '', data: null, content: null, tab: 'article',
};

async function contentDetail(id) {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const c = await API.content(id);
    const t = c.translation || {};
    Object.assign(ZH, {
      content: c, tab: 'article', reason: '',
      data: t.brief ? t : null,
      status: t.brief ? 'ready' : 'none',
    });
    root.innerHTML = `
      <h1 class="page-title">${esc(c.title)}</h1>
      <p class="page-sub"><span class="tag">${c.market}</span> <span class="tag gray">${c.language}</span>
        质量 ${c.quality_avg?.toFixed(1) || '-'}/5 · 裁决 ${esc(VERDICT_LABEL[c.verdict] || c.verdict || '-')}
        ${c.is_fallback ? '<span class="tag orange">含兜底环节</span>' : ''}
        ${c.verdict === 'revise' ? '<button id="btn-revise" class="btn-primary">按修改意见重写</button>' : ''}</p>
      ${c.verdict === 'revise' ? '<div id="revise-status" class="revise-status" style="display:none"></div>' : ''}
      <div class="tabs">
        <a data-tab="article" class="active">母稿</a><a data-tab="brief">选题简报</a>
        <a data-tab="formats">多形态 (${Object.keys(c.formats || {}).length})</a>
        <a data-tab="dist">分发计划</a><a data-tab="trace">Trace</a><a data-tab="quality">质量</a>
      </div>
      <div id="tab-body"></div>`;
    document.querySelectorAll('.tabs a').forEach(a => a.onclick = () => {
      document.querySelectorAll('.tabs a').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
      ZH.tab = a.dataset.tab;
      paintTab();
    });
    const btnRevise = document.getElementById('btn-revise');
    if (btnRevise) btnRevise.onclick = () => startRevise(c.id, c.title);
    ReviseState.paint();  // 还原进行中的重写状态（切回来不会"看起来停了"）
    paintTab();
    if (ZH.content?.needs_zh) ensureZh();  // 非中文市场：按需生成中文对照（含分发计划+质量）
  } catch (e) { root.innerHTML = errBox(e); }
}

function paintTab() {
  const body = document.getElementById('tab-body');
  const c = ZH.content;
  if (!body || !c) return;
  const renderers = {
    article: () => renderArticle(c), brief: () => renderBrief(c),
    formats: () => renderFormats(c), dist: () => renderDist(c.distribution),
    signals: () => renderSignals(c),
    trace: () => renderTrace(c.id), quality: () => renderQuality(c.quality),
  };
  const tab = ZH.tab;
  const out = renderers[tab] ? renderers[tab]() : '';
  if (out instanceof Promise) body.innerHTML = '<div class="loading">…</div>';
  Promise.resolve(out).then(h => {
    if (ZH.tab !== tab || ZH.content !== c) return;  // 期间已切走/换内容，丢弃这次结果
    body.innerHTML = h;
    bindZhBar();
  });
  if (ZH.content?.needs_zh) ensureZh();
}

/* 缺中文对照时按需触发生成（一次调用同时覆盖简报+多形态） */
async function ensureZh() {
  const c = ZH.content;
  if (!c || !c.needs_zh || ZH.status !== 'none') return;
  ZH.status = 'loading';
  paintTab();
  try {
    const r = await API.contentZh(c.id);
    if (r.available) { ZH.data = r.translation; ZH.status = 'ready'; }
    else { ZH.status = 'unavailable'; ZH.reason = r.reason || '暂无中文对照'; }
  } catch (e) {
    ZH.status = 'unavailable';
    ZH.reason = `回译请求失败（${e.message}）`;
  }
  paintTab();
}

/* 按总编修改意见就地重写：提交后台任务，状态交由全局 ReviseState 持续轮询/展示
   （与 RunState 同构：轮询存活于视图之外，切页面/刷新不中断，侧边栏常驻徽标）。 */
async function startRevise(id, title) {
  if (ReviseState.job && (ReviseState.job.status === 'running' || ReviseState.job.status === 'starting')) return;
  ReviseState.clear();
  ReviseState.set({ status: 'starting', job_id: null, content_id: id, title, started_at: Date.now() });
  try {
    const r = await API.contentRevise(id);
    const jobId = r.job_id;
    if (!jobId) throw new Error('未获取到任务编号');
    ReviseState.set({ status: 'running', job_id: jobId, started_at: Date.now() });
    ReviseState.startPolling();
  } catch (e) {
    ReviseState.set({ status: 'failed', error: e.message });
  }
}

function bindZhBar() {
  document.querySelectorAll('[data-zhmode]').forEach(a => a.onclick = () => {
    ZH.mode = a.dataset.zhmode;
    localStorage.setItem('tf_zh_mode', ZH.mode);
    paintTab();
  });
  const rf = document.querySelector('[data-zhrefresh]');
  if (rf) rf.onclick = async () => {
    ZH.status = 'loading'; paintTab();
    try {
      const r = await API.contentZh(ZH.content.id, true);
      if (r.available) { ZH.data = r.translation; ZH.status = 'ready'; }
      else { ZH.status = 'unavailable'; ZH.reason = r.reason || '暂无中文对照'; }
    } catch (e) { ZH.status = 'unavailable'; ZH.reason = e.message; }
    paintTab();
  };
}

/* 语言切换条：只在非中文市场出现 */
function zhBar(c) {
  if (!c.needs_zh) return '';
  const segs = [['both', '双语对照'], ['src', `${(c.language || '').toUpperCase()} 原文`], ['zh', '仅中文']]
    .map(([k, l]) => `<a class="seg${ZH.mode === k ? ' on' : ''}" data-zhmode="${k}">${l}</a>`).join('');
  let note = '';
  if (ZH.status === 'loading') note = '<span class="zh-note">⏳ AI 回译生成中…（首次约 20–40 秒，之后缓存秒开）</span>';
  else if (ZH.status === 'unavailable') note = `<span class="zh-note err">⚠ ${esc(ZH.reason)}</span>`;
  else if (ZH.status === 'ready' && ZH.data) note = `<span class="zh-note">中文对照 · ${esc(ZH.data.model || 'AI')} 回译 · <a class="link" data-zhrefresh="1">重新生成</a></span>`;
  return `<div class="zh-bar"><span class="zh-lab">🌏 面向中文运营的对照视图</span>
    <div class="seg-group">${segs}</div>${note}</div>`;
}

/* 双语文本：原文 + 中文对照（按当前模式） */
function bi(src, zh) {
  const s = String(src == null ? '' : src);
  const z = String(zh == null ? '' : zh).trim();
  if (!ZH.content?.needs_zh || ZH.mode === 'src') return esc(s);
  if (ZH.mode === 'zh') {
    if (z) return esc(z);
    // 中文对照尚未就绪（生成中/无缓存）：占位，避免闪现英文原文
    if (ZH.status === 'loading' || ZH.status === 'none') return '<span class="zh-loading">（中文对照生成中…）</span>';
    return esc(s);  // 不可用/缺译文：回退原文
  }
  // both 双语对照
  if (z === s.trim()) return esc(s);
  return `${esc(s)}<span class="zh-line">${esc(z)}</span>`;
}

const biList = (arr, zarr) => (arr || []).map((v, i) => bi(v, (zarr || [])[i]));

function renderArticle(c) {
  const sections = (c.body?.sections || []).map(s =>
    `<h4>${esc(s.heading)}</h4><p>${linkifyEv(esc(s.text), c.evidences)}</p>`).join('');
  return `<div class="panel article-body">
    <p style="color:#77809a;font-size:13px;margin-bottom:10px">${esc(c.summary)}</p>${sections}
    <h4 style="margin-top:24px">证据集 (${(c.evidences || []).length})</h4>
    ${(c.evidences || []).map(e => `<p style="font-size:12px;color:#77809a"><span class="rank">#${evNum(e.ev_id)}</span> ${esc(e.source)} · 可信度${e.credibility} · ${esc(e.doc_title.slice(0, 50))}</p>`).join('')}
  </div>`;
}

const evNum = (id) => { const m = String(id || '').match(/\d+/); return m ? parseInt(m[0], 10) : ''; };

function linkifyEv(html, evs) {
  const map = {}; (evs || []).forEach(e => map[e.ev_id] = e);
  // 正文引用改为上标编号（保留来源 tooltip），去掉 [ev_xxx] 代码感
  return html.replace(/\[(ev_\d+)\]/g, (m, id) => {
    const e = map[id];
    if (!e) return m;
    return `<sup class="cite" title="${esc(e.source)}：${esc(e.text.slice(0, 120))}">${evNum(id)}</sup>`;
  });
}

// 风格是枚举而非自由文本，用固定映射给中文运营看，不必花 LLM 额度回译
const STYLE_LABEL = {
  deep_dive: '深度解析', explainer: '科普解释', news_roundup: '资讯汇总',
  opinion: '观点评论', listicle: '清单体', how_to: '教程指南',
};

function renderBrief(c) {
  const b = c.brief;
  if (!b) return '<div class="panel">无简报</div>';
  const z = (ZH.data && ZH.data.brief) || {};
  const avoid = biList(b.avoid, z.avoid);
  const kw = biList(b.keywords, z.keywords);
  return `<div class="panel"><h3>AngleEditor 选题简报（AI 的"主编判断"）</h3>
    ${zhBar(c)}
    <dl class="brief-grid">
      <dt>选题</dt><dd>${bi(b.topic, z.topic)}</dd>
      <dt>角度</dt><dd>${bi(b.angle, z.angle)}</dd>
      <dt>钩子</dt><dd>${bi(b.hook, z.hook)}</dd>
      <dt>受众</dt><dd>${bi(b.audience, z.audience)}</dd>
      <dt>风格</dt><dd><span class="tag gray">${esc(STYLE_LABEL[b.style] || b.style || '—')}</span></dd>
      <dt>why now</dt><dd>${bi(b.why_now, z.why_now)}</dd>
      <dt>避免事项</dt><dd>${avoid.length ? avoid.map(a => `<span class="tag red block">${a}</span>`).join('') : '—'}</dd>
      <dt>检索关键词</dt><dd>${kw.length ? kw.map(k => `<span class="tag gray block">${k}</span>`).join('') : '—'}</dd>
      <dt>形态计划</dt><dd>${(b.format_plan || []).map(f => `<span class="tag">${esc(FMT_META[f]?.label || f)}</span>`).join('') || '—'}</dd>
    </dl></div>`;
}

/* ---------- 多形态：结构化渲染（不再直接抛 JSON） ---------- */
const FMT_META = {
  article: { label: '母稿', icon: '📄' },
  video_script: { label: '短视频脚本', icon: '🎬', desc: '45–60 秒竖屏' },
  card: { label: '资讯摘要卡片', icon: '🗂', desc: '3–5 条要点' },
  brief_news: { label: '快讯', icon: '⚡', desc: '≤120 字' },
  comment: { label: '评论区引导', icon: '💬', desc: '提问 + 讨论角度' },
};

/* 平台枚举 → 中文运营标签：专有名词（X/LinkedIn/YouTube 等）保留英文，复合标识符翻译后缀 */
const PLATFORM_LABEL = {
  x: 'X', linkedin: 'LinkedIn', youtube_shorts: 'YouTube短视频', youtube: 'YouTube',
  line: 'LINE', yahoo_news: '雅虎新闻', naver: 'Naver',
  instagram: 'Instagram', whatsapp: 'WhatsApp', kwai: '快手',
  wechat: '微信公众号', weibo: '微博', douyin: '抖音',
};

/* 质量裁决枚举 → 中文 */
const VERDICT_LABEL = { publish: '可发布', revise: '需修改', reject: '不通过' };

/* ---------- 真实信号溯源：展示 SignalScout 实时抓取的源头（来源/时间/真实互动/原文链接） ---------- */
function renderSignals(c) {
  const sigs = c.signals || [];
  if (!sigs.length) return '<div class="panel">本次内容未关联实时真实信号（可能由兜底路径从本地 KB 生成）。</div>';
  const items = sigs.map(s => {
    const e = s.engagement || {};
    const eng = [];
    if (e.score != null && e.score !== '') eng.push(`互动值 ${esc(String(e.score))}`);
    if (e.comments != null && e.comments !== '') eng.push(`评论 ${esc(String(e.comments))}`);
    if (e.tone != null && e.tone !== '') eng.push(`情感 ${esc(String(e.tone))}`);
    const link = s.source_url
      ? `<a class="link" href="${esc(s.source_url)}" target="_blank" rel="noopener">原文 ↗</a>`
      : (s.source ? esc(s.source) : '');
    return `<li class="sig-item">
      <div class="sig-title">${esc(s.title || '(无标题)')}</div>
      <div class="sig-meta">
        <span class="tag gray">${esc(s.source || '—')}</span>
        ${s.published_at ? `<span class="tag gray">${esc(String(s.published_at).slice(0, 10))}</span>` : ''}
        ${s.category ? `<span class="tag gray">${esc(s.category)}</span>` : ''}
        ${eng.length ? `<span class="tag green">${eng.join(' · ')}</span>` : ''}
        ${link}
      </div>
      ${s.angle_hint ? `<div class="sig-angle">角度建议：${esc(s.angle_hint)}</div>` : ''}
    </li>`;
  }).join('');
  return `<div class="panel"><h3>驱动本内容的实时真实信号</h3>
    <p class="muted">信号由 SignalScout 从 Hacker News / Dev.to / GDELT 等公开源实时抓取，互动数据为真实人类消费行为。下方链接可点击溯源。</p>
    <ul class="sig-list">${items}</ul></div>`;
}

function renderFormats(c) {
  const fmts = c.formats || {};
  if (!Object.keys(fmts).length) return '<div class="panel">无派生形态</div>';
  const zf = (ZH.data && ZH.data.formats) || {};
  const blocks = Object.entries(fmts).map(([k, v]) => {
    const m = FMT_META[k] || { label: k, icon: '📦' };
    return `<div class="fmt-block">
      <h5>${m.icon} ${esc(m.label)}${m.desc ? `<span class="fmt-desc">${esc(m.desc)}</span>` : ''}</h5>
      ${fmtBody(k, v, zf[k] || {})}
    </div>`;
  }).join('');
  return `<div class="panel"><h3>FormatAdapter 一稿多发（${Object.keys(fmts).length} 种形态）</h3>
    ${zhBar(c)}${blocks}</div>`;
}

function fmtBody(kind, v, z) {
  if (v == null) return '<p class="muted">空</p>';
  if (kind === 'video_script') return fmtVideo(v, z);
  if (kind === 'card') return fmtCard(v, z);
  if (kind === 'brief_news') return fmtNews(v, z);
  if (kind === 'comment') return fmtComment(v, z);
  return kvTree(v, z);
}

function fmtVideo(v, z) {
  const scenes = v.scenes || [];
  const zs = z.scenes || [];
  const tbl = scenes.length ? `<table class="scenes">
    <tr><th style="width:36px">#</th><th style="width:26%">画面</th><th>口播</th><th style="width:22%">字幕</th></tr>
    ${scenes.map((s, i) => {
      const q = zs[i] || {};
      return `<tr><td class="sc-n">${i + 1}</td><td class="sc-shot">${bi(s.shot, q.shot)}</td>
        <td>${bi(s.voiceover, q.voiceover)}</td><td class="sc-sub">${bi(s.subtitle, q.subtitle)}</td></tr>`;
    }).join('')}</table>` : '';
  const tags = (v.hashtags || []).map((h, i) =>
    `<span class="tag">${bi(String(h).replace(/^#/, '#'), (z.hashtags || [])[i])}</span>`).join('');
  return `${v.hook ? `<div class="fmt-hook"><span class="fmt-k">钩子</span>${bi(v.hook, z.hook)}</div>` : ''}
    ${tbl}
    ${v.cta ? `<div class="fmt-row"><span class="fmt-k">CTA</span><span>${bi(v.cta, z.cta)}</span></div>` : ''}
    ${tags ? `<div class="fmt-row"><span class="fmt-k">话题标签</span><span>${tags}</span></div>` : ''}`;
}

function fmtCard(v, z) {
  const pts = (v.points || []).map((p, i) => `<li>${bi(p, (z.points || [])[i])}</li>`).join('');
  return `${v.title ? `<div class="fmt-title">${bi(v.title, z.title)}</div>` : ''}
    ${pts ? `<ol class="fmt-points">${pts}</ol>` : ''}
    ${v.key_data ? `<div class="fmt-stat"><span class="fmt-k">关键数据</span><b>${bi(v.key_data, z.key_data)}</b></div>` : ''}`;
}

function fmtNews(v, z) {
  return `${v.headline ? `<div class="fmt-title">${bi(v.headline, z.headline)}</div>` : ''}
    ${v.body ? `<p class="fmt-p">${bi(v.body, z.body)}</p>` : ''}`;
}

function fmtComment(v, z) {
  const angs = (v.angles || []).map((a, i) => `<li>${bi(a, (z.angles || [])[i])}</li>`).join('');
  return `${v.question ? `<div class="fmt-quote">${bi(v.question, z.question)}</div>` : ''}
    ${angs ? `<div class="fmt-row"><span class="fmt-k">讨论角度</span></div><ol class="fmt-points">${angs}</ol>` : ''}`;
}

/* 通用键值渲染：兜底未知结构，仍然保持可读，不退化成 JSON */
/* 枚举型取值 → 中文标签（与 key 标签区分，作用于值本身） */
const VAL_LABELS = {
  verdict: VERDICT_LABEL,
};
const KV_LABELS = {
  title: '标题', headline: '标题', body: '正文', text: '正文', summary: '摘要',
  hook: '钩子', cta: 'CTA', question: '提问', angles: '讨论角度', points: '要点',
  key_data: '关键数据', hashtags: '话题标签', scenes: '分镜', shot: '画面',
  voiceover: '口播', subtitle: '字幕', avg: '均分', verdict: '裁决',
  scores: '各维度评分', rubric: '评分标准', fact_check: '事实核查',
  supported: '有据支持', weak: '弱支持', unverified: '未证实', notes: '说明',
  // 质量各维度评分
  accuracy: '准确性', angle: '角度质量', readability: '可读性',
  local_fit: '本地契合度', engagement: '互动性', depth: '深度',
  credibility: '可信度', originality: '原创性', compliance: '合规性',
  freshness: '时效性', structure: '结构', tone: '语气', factuality: '事实性',
  // 事实核查
  claim_count: '声明数', support_ratio: '支持率', weak_claims: '弱支持声明',
  unsupported_claims: '未证实声明', confidence: '置信度',
  // 合规 / 修订
  compliance_hits: '合规命中', revision_advice: '修改建议', comments: '评语',
};
const kvLabel = (k) => KV_LABELS[k] || k;

function kvTree(v, z, depth = 0, key) {
  if (v == null || v === '') return '<span class="muted">—</span>';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number') return `<b>${v}</b>`;
  if (typeof v === 'string') {
    if (key && VAL_LABELS[key] && VAL_LABELS[key][v] != null) return esc(VAL_LABELS[key][v]);
    return bi(v, typeof z === 'string' ? z : '');
  }
  if (Array.isArray(v)) {
    if (!v.length) return '<span class="muted">—</span>';
    const za = Array.isArray(z) ? z : [];
    if (v.every(x => typeof x !== 'object' || x === null)) {
      return `<ol class="fmt-points">${v.map((x, i) => `<li>${kvTree(x, za[i], depth + 1, key)}</li>`).join('')}</ol>`;
    }
    return v.map((x, i) => `<div class="kv-card">${kvTree(x, za[i], depth + 1, key)}</div>`).join('');
  }
  const zo = (z && typeof z === 'object') ? z : {};
  return `<dl class="kv-grid${depth ? ' sub' : ''}">${Object.entries(v).map(([k, val]) =>
    `<dt>${esc(kvLabel(k))}</dt><dd>${kvTree(val, zo[k], depth + 1, k)}</dd>`).join('')}</dl>`;
}

function renderQuality(q) {
  if (!q || !Object.keys(q).length) return '<div class="panel">无质量数据</div>';
  const avg = typeof q.avg === 'number' ? q.avg.toFixed(1) : '-';
  const zq = (ZH.data && ZH.data.quality) || {};
  return `<div class="panel"><h3>质量裁决 · 均分 ${avg}/5 ${q.verdict ? `<span class="tag">${esc(VERDICT_LABEL[q.verdict] || q.verdict)}</span>` : ''}</h3>
    ${kvTree(q, zq)}</div>`;
}

function renderDist(d) {
  const plan = d?.plan || [];
  if (!plan.length) return '<div class="panel">无分发计划</div>';
  const zd = (ZH.data && ZH.data.distribution) || {};
  const zplan = zd.plan || [];
  return `<div class="panel"><h3>Distributor 分发计划</h3><table>
    <tr><th>#</th><th>平台</th><th>形态</th><th>受众</th><th>时段</th><th>理由</th></tr>
    ${plan.map((p, i) => `<tr><td>${p.priority}</td>
      <td>${esc(PLATFORM_LABEL[p.platform] || p.platform)}</td>
      <td><span class="tag">${esc(FMT_META[p.format]?.label || p.format)}</span></td>
      <td>${bi(p.audience, zplan[i] && zplan[i].audience)}</td>
      <td>${bi(p.timing, zplan[i] && zplan[i].timing)}</td>
      <td>${bi(p.reason, zplan[i] && zplan[i].reason)}</td></tr>`).join('')}
  </table></div>`;
}

async function renderTrace(contentId) {
  const t = await API.trace(contentId);
  return `<div class="panel"><h3>执行 Trace（${t.spans.length} 步 · 总耗时 ${(t.task.total_duration_ms / 1000).toFixed(0)}s · ¥${t.task.total_cost_cny.toFixed(4)} · 审核回退 ${t.task.review_rounds} 轮）</h3>
    ${t.spans.map(s => `<div class="trace-step ${s.status}">
      <div class="agent">${esc(s.agent)} ${statusTag(s.status)}</div>
      <div class="meta">${esc(s.model || '规则')} · ${s.tokens_in}+${s.tokens_out} tokens · ${s.duration_ms}ms</div>
      <div class="decision">${esc(s.decision_reason)}</div>
      ${(s.warnings || []).map(w => `<div class="meta" style="color:#e08a00">⚠ ${esc(w)}</div>`).join('')}
    </div>`).join('')}</div>`;
}

/* ---------- 市场档案 ---------- */
async function markets() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const m = await API.markets();
    root.innerHTML = `
      <h1 class="page-title">市场档案</h1>
      <p class="page-sub">人定义标准的核心载体：AI 据此理解当地内容生态、文化语境与用户需求</p>
      <div class="market-grid">${m.markets.map(x => `
        <div class="panel market-card">
          <h3>${esc(x.name)} <span class="tag">${x.code}</span></h3>
          <div class="section"><b>调性 / 默认风格</b>${esc(x.tone)} · ${esc(x.default_style)}</div>
          <div class="section"><b>文化语境与禁忌</b><ul>${x.culture_notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>
          <div class="section"><b>平台生态</b>${Object.entries(x.platforms).map(([p, s]) =>
            `<span class="tag">${p}: ${(s.formats || []).join('/')}</span>`).join('')}</div>
          <div class="section"><b>兴趣画像</b>${Object.entries(x.interests).map(([k, v]) =>
            `<span class="tag gray">${k} ${v}</span>`).join('')}</div>
        </div>`).join('')}</div>`;
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 评估中心 ---------- */
async function evalView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [ov, reps, mkts] = await Promise.all([API.analyticsOverview(), API.reports(), API.markets()]);
    root.innerHTML = `
      <h1 class="page-title">评估中心</h1>
      <p class="page-sub">消费反馈 → 指标分析 → FeedbackAnalyst 迭代建议（闭环的最后一步）</p>
      <div class="toolbar">
        <button class="btn ghost" id="sim-btn">模拟消费事件</button>
        <select id="fb-mk">${mkts.markets.map(m => `<option value="${m.code}">${m.name}</option>`).join('')}</select>
        <button class="btn" id="fb-btn">运行 FeedbackAnalyst</button>
      </div>
      <div class="cards">
        <div class="card"><div class="kpi">${(ov.ctr * 100).toFixed(1)}%</div><div class="kpi-label">CTR（${ov.exposed} 曝光）</div></div>
        <div class="card"><div class="kpi">${(ov.finish_rate * 100).toFixed(1)}%</div><div class="kpi-label">完读率</div></div>
        <div class="card"><div class="kpi">${(ov.engagement * 100).toFixed(1)}%</div><div class="kpi-label">互动率</div></div>
        <div class="card"><div class="kpi">${(ov.neg_rate * 100).toFixed(1)}%</div><div class="kpi-label">负反馈率</div></div>
      </div>
      <div class="panel"><h3>分形态 CTR</h3>
        ${Object.entries(ov.by_format_ctr || {}).map(([f, v]) =>
          `<span class="tag">${f}: ${(v * 100).toFixed(1)}%</span>`).join('') || '暂无数据（先模拟事件）'}</div>
      <div class="panel"><h3>评估报告</h3><div id="reports">
        ${reps.reports.map(r => `
          <div style="margin-bottom:18px">
            <p style="font-size:12px;color:#77809a">${fmtTime(r.created_at)} · 质量均分 ${r.quality_avg}</p>
            ${r.findings.map(f => `<div class="finding">📊 ${esc(f)}</div>`).join('')}
            ${r.suggestions.map(s => `<div class="suggestion">💡 ${esc(s)}</div>`).join('')}
          </div>`).join('') || '暂无报告'}
      </div></div>`;
    document.getElementById('sim-btn').onclick = async () => {
      const r = await API.simulate(); alert(`已模拟 ${r.events} 条事件`); evalView();
    };
    document.getElementById('fb-btn').onclick = async () => {
      const mk = document.getElementById('fb-mk').value;
      const r = await API.runFeedback(mk);
      if (r.ok) evalView(); else alert(r.error || '失败');
    };
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 知识库 ---------- */
async function kbView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const s = await API.kbStats();
    root.innerHTML = `
      <h1 class="page-title">知识库</h1>
      <p class="page-sub">BM25 检索 · 全部事实可溯源（LLM 不联网，知识库是唯一事实来源）</p>
      <div class="cards">
        <div class="card"><div class="kpi">${s.documents}</div><div class="kpi-label">文档</div></div>
        <div class="card"><div class="kpi">${s.chunks}</div><div class="kpi-label">分块</div></div>
      </div>
      <div class="panel"><h3>类目分布</h3>${Object.entries(s.by_category).map(([k, v]) => `<span class="tag">${k} × ${v}</span>`).join('')}</div>
      <div class="panel"><h3>检索演示</h3>
        <div class="toolbar"><input id="kb-q" placeholder="试试：AI agent / 电动车 出口" style="width:340px"><button class="btn" id="kb-go">检索</button></div>
        <div id="kb-results"></div></div>
      <div class="panel"><h3>知识库治理（AI 提议 · 人审闸门）</h3>
        <div id="kb-fresh" class="loading">加载新鲜度…</div>
        <div class="toolbar" style="margin-top:14px">
          <button class="btn" id="kb-curate">运行 KBCurator 策展</button>
          <button class="btn ghost" id="kb-reload">刷新</button>
        </div>
        <div id="kb-patch"></div>
        <div id="kb-history" style="margin-top:18px"></div>
      </div>`;
    document.getElementById('kb-go').onclick = async () => {
      const q = document.getElementById('kb-q').value.trim();
      if (!q) return;
      const r = await API.kbSearch(q);
      document.getElementById('kb-results').innerHTML = r.results.map((e, i) =>
        `<div class="finding"><span class="rank">#${i + 1}</span> <b>${esc(e.doc_title)}</b>
           <span class="tag gray">${esc(e.source)}</span>${e.published_at ? ` <span class="tag gray">${esc(String(e.published_at).slice(0, 10))}</span>` : ''}${e.credibility ? ` <span class="tag gray">可信度 ${e.credibility}</span>` : ''}
           ${e.is_stale ? '<span class="tag orange">⚠ 待核实</span>' : ''} · score ${e.score}<br>
         <span style="color:#77809a">${esc(e.text.slice(0, 140))}…</span></div>`).join('') || '无结果';
    };
    document.getElementById('kb-curate').onclick = () => runCurate();
    document.getElementById('kb-reload').onclick = () => loadGovernance();
    loadGovernance();
  } catch (e) { root.innerHTML = errBox(e); }
}

async function loadGovernance() {
  try {
    const [f, p] = await Promise.all([API.kbFreshness(), API.kbPatches()]);
    document.getElementById('kb-fresh').innerHTML =
      `参考日期 <b>${esc(f.ref_date)}</b> · 有效文档 <b>${f.total}</b> · 过期 <b style="color:#e08a00">${f.stale.length}</b> 篇
       <div style="margin-top:8px">${Object.entries(f.by_category).map(([k, v]) => `<span class="tag">${k} × ${v}</span>`).join('')}</div>`;
    renderPatch(p.patches[0]);
    document.getElementById('kb-history').innerHTML = '<h4 style="margin:6px 0 8px">历史补丁</h4>' +
      (p.patches.length ? p.patches.map(h => `<div class="finding" style="background:#f6f8fc">
        <span class="tag ${h.status === 'approved' ? 'green' : h.status === 'rejected' ? 'red' : 'orange'}">${h.status}</span>
        ${esc(h.rationale.slice(0, 80))} · ${h.items.length} 项 · ${fmtTime(h.created_at)}</div>`).join('') : '<span style="color:#77809a;font-size:12px">暂无</span>');
  } catch (e) { document.getElementById('kb-fresh').innerHTML = errBox(e); }
}

async function runCurate() {
  const box = document.getElementById('kb-patch');
  box.innerHTML = 'KBCurator 扫描中…';
  try {
    const r = await API.kbCurate();
    renderPatch({ ...r, status: 'pending', created_at: '' });
  } catch (e) { box.innerHTML = errBox(e); }
}

function renderPatch(p) {
  const box = document.getElementById('kb-patch');
  if (!p || !p.items || !p.items.length) { box.innerHTML = '<span style="color:#77809a;font-size:12px">暂无待审补丁（先点「运行 KBCurator 策展」）</span>'; return; }
  box.innerHTML = `<div style="margin:6px 0 8px"><b>待审补丁</b> <span class="tag orange">${esc(p.status)}</span></div>
    <div style="font-size:12px;color:#55607a;margin-bottom:8px">${esc(p.rationale)}</div>
    ${p.items.map((it, i) => `<div class="finding" style="background:${it.action === 'retire' ? '#fff3e0' : '#e3f8f2'}">
      <span class="tag ${it.action === 'retire' ? 'orange' : 'green'}">${it.action === 'retire' ? '退役' : '入库'}</span>
      <b>${esc(it.title || it.replaces || '')}</b>${it.source ? ` · ${esc(it.source)}` : ''}
      <div style="font-size:12px;color:#77809a;margin-top:3px">${esc(it.reason)}</div>
    </div>`).join('')}
    <div class="toolbar" style="margin-top:6px">
      <button class="btn" id="kb-approve">✅ 人审通过（入库）</button>
      <button class="btn ghost" id="kb-reject">❌ 拒绝</button>
    </div>`;
  document.getElementById('kb-approve').onclick = async () => {
    const r = await API.kbApprove(p.patch_id);
    if (r.ok) { alert(`已入库：新增 ${r.added} 篇、退役 ${r.retired} 篇`); loadGovernance(); }
    else alert(r.error || '失败');
  };
  document.getElementById('kb-reject').onclick = async () => {
    const r = await API.kbReject(p.patch_id);
    if (r.ok) loadGovernance(); else alert(r.error || '失败');
  };
}

/* ---------- 分析中心（M2）：SQL 驱动指标看板 ----------
   每个图表由后端手写 SQL 实时计算，前端展示真实 SQL 原文（可展开），
   消费类图表统一标注「仿真」角标。纯 SVG 渲染，无第三方图表库。 */
async function analyticsView() {
  root.innerHTML = '<div class="loading">加载分析中心…</div>';
  try {
    const data = await API.analyticsCenter();
    renderAnalytics(data);
  } catch (e) { root.innerHTML = errBox(e); }
}

function renderAnalytics(data) {
  const charts = data.charts || [];
  const sim = charts.filter(c => c.reality === 'simulated').length;
  root.innerHTML = `
    <h1 class="page-title">分析中心</h1>
    <p class="page-sub">全部指标由后端手写 SQL 实时计算（非 ORM）。供给 / 成本 / 质量类来自真实运行数据，消费类来自仿真器（已标注「仿真」）。每张图下方可展开驱动它的真实 SQL。</p>
    <div class="toolbar" style="margin-bottom:14px">
      <span class="tag gray">共 ${charts.length} 图</span>
      <span class="tag green">真实 ${charts.length - sim}</span>
      <span class="tag orange">仿真 ${sim}</span>
      <button class="btn ghost" id="btn-refresh-analytics">⟳ 刷新</button>
    </div>
    <div class="charts-grid">${charts.map(chartCard).join('')}</div>`;
  const btn = document.getElementById('btn-refresh-analytics');
  if (btn) btn.onclick = analyticsView;
}

function chartCard(c) {
  const badge = c.reality === 'simulated'
    ? '<span class="tag orange">仿真</span>'
    : '<span class="tag green">真实</span>';
  const head = (c.headline && c.headline.kind) ? statHead(c.headline) : '';
  return `<div class="chart-card">
    <div class="chart-head"><h3>${esc(c.title)}</h3>${badge}</div>
    ${head}
    <div class="chart-body">${renderChart(c)}</div>
    <p class="chart-note">${esc(c.note || '')}</p>
    <details class="sql-reveal"><summary>驱动此图的 SQL ▸</summary><pre>${esc(c.sql)}</pre></details>
  </div>`;
}

function statHead(h) {
  if (h.kind === 'cost')
    return `<div class="stat-big">${esc(h.value)}<span class="stat-suffix">${esc(h.suffix || '')}</span></div><div class="stat-sub">${esc(h.sub || '')}</div>`;
  if (h.kind === 'rate')
    return `<div class="stat-big">${(Number(h.value) * 100).toFixed(1)}%</div><div class="stat-sub">${esc(h.sub || '')}</div>`;
  return '';
}

function renderChart(c) {
  switch (c.chart) {
    case 'bar': return svgBar(c);
    case 'grouped_bar': return svgGroupedBar(c);
    case 'cohort': case 'line': return svgLine(c);
    case 'funnel': return svgFunnel(c);
    case 'heat': return svgHeat(c);
    default: return '';
  }
}

function fmtNum(v) {
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  if (n === 0) return '0';
  if (Number.isInteger(n)) return n.toLocaleString('en-US');
  return n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}

function svgBar(c) {
  const labels = c.rows.map(r => String(r[0]));
  const vals = c.rows.map(r => Number(r[r.length - 1]) || 0);
  if (!vals.length) return '<div class="muted">暂无数据</div>';
  const max = Math.max(1e-9, ...vals);
  const W = 600, H = 220, padL = 46, padB = 42, padT = 12, padR = 12;
  const n = labels.length, gap = (W - padL - padR) / n, bw = gap * 0.6;
  let s = '';
  vals.forEach((v, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const h = (v / max) * (H - padT - padB);
    const y = H - padB - h;
    s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="3" fill="#4c8bf5"/>`;
    if (h > 14) s += `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" font-size="11" fill="#334">${fmtNum(v)}</text>`;
    s += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - padB + 14}" text-anchor="middle" font-size="10" fill="#778">${esc(labels[i].length > 10 ? labels[i].slice(0, 10) + '…' : labels[i])}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg>`;
}

function svgFunnel(c) {
  const rows = c.rows || [];
  if (!rows.length) return '<div class="muted">暂无数据</div>';
  const max = Math.max(1e-9, ...rows.map(r => Number(r[1]) || 0));
  const W = 600, H = 220, padT = 10, padL = 170;
  const n = rows.length, slot = (H - padT - 8) / n, bh = slot * 0.66;
  let s = '';
  rows.forEach((r, i) => {
    const v = Number(r[1]) || 0, w = (v / max) * (W - padL - 40), y = padT + i * slot + (slot - bh) / 2;
    s += `<rect x="${padL}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${bh.toFixed(1)}" rx="3" fill="#6a5acd"/>`;
    s += `<text x="${padL - 8}" y="${(y + bh / 2 + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="#334">${esc(String(r[0]))}</text>`;
    s += `<text x="${(padL + w + 8).toFixed(1)}" y="${(y + bh / 2 + 4).toFixed(1)}" font-size="11" fill="#778">${fmtNum(v)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg>`;
}

const CHART_COLORS = ['#4c8bf5', '#f5a623', '#2bb673', '#e056a0', '#7c5cff', '#19b3c4'];

function svgLine(c) {
  const labels = c.headline?.labels || [];
  const series = c.headline?.series || [];
  if (!series.length) return '<div class="muted">暂无数据</div>';
  const W = 600, H = 240, padL = 46, padB = 28, padT = 12, padR = 12;
  const all = series.flatMap(s => s.data);
  const max = Math.max(1e-9, ...all);
  const n = labels.length || all.length;
  const xstep = n > 1 ? (W - padL - padR) / (n - 1) : 0;
  let s = `<line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#e2e6ef"/>`;
  series.forEach((ser, si) => {
    const col = CHART_COLORS[si % CHART_COLORS.length];
    let path = '';
    ser.data.forEach((v, i) => {
      const x = padL + i * xstep, y = (H - padB) - ((v / max) * (H - padT - padB));
      path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    });
    s += `<path d="${path}" fill="none" stroke="${col}" stroke-width="2"/>`;
    ser.data.forEach((v, i) => {
      const x = padL + i * xstep, y = (H - padB) - ((v / max) * (H - padT - padB));
      s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.4" fill="${col}"/>`;
    });
  });
  labels.forEach((lb, i) => {
    if (n <= 8 || i % Math.ceil(n / 8) === 0)
      s += `<text x="${(padL + i * xstep).toFixed(1)}" y="${H - padB + 13}" text-anchor="middle" font-size="9" fill="#778">${esc(String(lb))}</text>`;
  });
  let leg = series.map((ser, si) => `<span class="lg"><i style="background:${CHART_COLORS[si % CHART_COLORS.length]}"></i>${esc(ser.name)}</span>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg><div class="legend">${leg}</div>`;
}

function svgGroupedBar(c) {
  const labels = c.headline?.labels || [];
  const series = c.headline?.series || [];
  if (!series.length) return '<div class="muted">暂无数据</div>';
  const W = 600, H = 240, padL = 40, padB = 42, padT = 12, padR = 12;
  const all = series.flatMap(s => s.data);
  const max = Math.max(1e-9, ...all);
  const n = labels.length, groupW = (W - padL - padR) / n, bw = groupW * 0.72 / series.length;
  let s = '';
  labels.forEach((lb, i) => {
    const gx = padL + i * groupW + groupW * 0.14;
    series.forEach((ser, si) => {
      const v = ser.data[i] || 0, h = (v / max) * (H - padT - padB), x = gx + si * bw, y = H - padB - h;
      s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="2" fill="${CHART_COLORS[si % CHART_COLORS.length]}"/>`;
    });
    s += `<text x="${(gx + groupW * 0.36).toFixed(1)}" y="${H - padB + 14}" text-anchor="middle" font-size="10" fill="#778">${esc(String(lb))}</text>`;
  });
  let leg = series.map((ser, si) => `<span class="lg"><i style="background:${CHART_COLORS[si % CHART_COLORS.length]}"></i>${esc(ser.name)}</span>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg><div class="legend">${leg}</div>`;
}

function svgHeat(c) {
  const cols = c.columns || [], rows = c.rows || [];
  if (!rows.length) return '<div class="muted">暂无数据</div>';
  const all = rows.flatMap(r => r.slice(1).map(Number));
  const max = Math.max(1e-9, ...all);
  let h = `<table class="heat-table"><thead><tr>${cols.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>`;
  rows.forEach(r => {
    h += `<tr><td class="hm-label">${esc(String(r[0]))}</td>`;
    r.slice(1).forEach(v => {
      const t = Number(v) || 0, inten = Math.min(1, t / max), bg = `rgba(76,139,245,${(0.1 + inten * 0.72).toFixed(3)})`;
      h += `<td style="background:${bg}">${fmtNum(t)}</td>`;
    });
    h += '</tr>';
  });
  return h + '</tbody></table>';
}

/* ---------- 工具 ---------- */
function statusTag(s) {
  const map = { ok: 'green', done: 'green', pass: 'green', running: '', degraded: 'orange', failed: 'red', reject: 'red' };
  return `<span class="tag ${map[s] || 'gray'}">${s}</span>`;
}
function errBox(e) { return `<div class="panel" style="color:#d43d3d">加载失败：${esc(e.message)}<br><small>后端可能冷启动中（Render 免费层休眠约 30-60s），请稍候刷新</small></div>`; }

/* ---------- 迭代闭环（M3）：AI 提议 → 人审闸门 → 采纳 → 回滚 → A/B ---------- */
async function closedLoopView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const tpls = await API.promptTemplates();
    root.innerHTML = `
      <h1 class="page-title">迭代闭环</h1>
      <p class="page-sub">AI 只提议、不自动改系统：FeedbackAnalyst 产出结构化迭代建议 → 人审采纳生成新版本并覆盖生效 → 可 diff / 回滚；同一选题用两版 Prompt 各跑一次即 A/B 验证。</p>
      <div class="panel"><h3>① 迭代建议（AI 提议 · 人审闸门）</h3>
        <div class="toolbar">
          <input id="cl-market" value="US" style="width:70px" title="市场码">
          <button class="btn" id="cl-feedback">运行 FeedbackAnalyst</button>
          <button class="btn ghost" id="cl-reload-sug">刷新</button>
        </div>
        <div id="cl-suggestions" class="loading">暂无建议</div>
      </div>
      <div class="panel"><h3>② Prompt 版本治理（采纳 / 回滚 / diff）</h3>
        <div class="toolbar">
          <select id="cl-tpl"></select>
          <button class="btn ghost" id="cl-reload-ver">刷新版本</button>
        </div>
        <div id="cl-versions" class="loading">选择模板查看版本</div>
        <pre id="cl-diff" class="diff" style="display:none"></pre>
      </div>
      <div class="panel"><h3>③ A/B 对比（同一选题 · 两版 Prompt）</h3>
        <div class="toolbar">
          <select id="cl-ab-tpl"></select>
          <select id="cl-ab-v1"><option value="">v1 版本</option></select>
          <select id="cl-ab-v2"><option value="">v2 版本</option></select>
          <input id="cl-ab-angle" placeholder="选题 / 角度（如：AI 监管）" style="width:200px">
          <input id="cl-ab-market" value="US" style="width:64px" title="市场码">
          <button class="btn" id="cl-ab-run">运行 A/B</button>
        </div>
        <div id="cl-ab-result" class="loading">选择两版 Prompt 并输入选题后运行</div>
      </div>`;
    const opts = tpls.templates.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
    document.getElementById('cl-tpl').innerHTML = opts;
    document.getElementById('cl-ab-tpl').innerHTML = opts;
    document.getElementById('cl-feedback').onclick = runFeedback;
    document.getElementById('cl-reload-sug').onclick = loadSuggestions;
    document.getElementById('cl-reload-ver').onclick = () => loadVersions(document.getElementById('cl-tpl').value);
    document.getElementById('cl-tpl').onchange = (e) => loadVersions(e.target.value);
    document.getElementById('cl-ab-tpl').onchange = (e) => loadAbVersions(e.target.value);
    document.getElementById('cl-ab-run').onclick = runAB;
    loadSuggestions();
    loadVersions(tpls.templates[0]);
  } catch (e) { root.innerHTML = errBox(e); }
}

async function runFeedback() {
  const box = document.getElementById('cl-suggestions');
  box.innerHTML = 'FeedbackAnalyst 分析中…（消费数据 → 结构化建议，可能 10-30s）';
  try {
    const market = document.getElementById('cl-market').value.trim() || 'US';
    const r = await API.promptFeedback(market);
    alert(`已生成 ${r.suggestion_ids.length} 条可采纳迭代建议`);
    loadSuggestions();
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadSuggestions() {
  const box = document.getElementById('cl-suggestions');
  try {
    const r = await API.promptSuggestions('pending');
    const sugs = r.suggestions || [];
    if (!sugs.length) { box.innerHTML = '<span style="color:#77809a;font-size:12px">暂无待审建议（先点「运行 FeedbackAnalyst」）</span>'; return; }
    box.innerHTML = sugs.map(s => `
      <div class="finding" style="background:#eef4ff;border-left:3px solid #3a6df0">
        <span class="tag blue">${esc(s.target_template)}</span>
        <span class="tag gray">${esc(s.section || '—')}</span>
        <span class="tag green">预期改善 ${esc(s.expected_metric || '—')}</span>
        <div style="margin:6px 0 4px"><b>改法：</b>${esc(s.proposed_change)}</div>
        <div style="font-size:12px;color:#55607a">理由：${esc(s.rationale)}</div>
        <details style="margin-top:6px"><summary style="cursor:pointer;color:#3a6df0;font-size:12px">查看完整新版 Prompt</summary>
          <pre class="diff" style="max-height:200px;overflow:auto">${esc(s.new_prompt)}</pre></details>
        <div class="toolbar" style="margin-top:6px">
          <button class="btn" data-adopt="${s.id}">✅ 人审采纳（生成新版本）</button>
          <button class="btn ghost" data-reject="${s.id}">❌ 拒绝</button>
        </div>
      </div>`).join('');
    box.querySelectorAll('[data-adopt]').forEach(b => b.onclick = async () => {
      const res = await API.promptSuggestionAdopt(b.dataset.adopt);
      if (res.ok) { alert(`已采纳 → ${res.name}@${res.version}（下一轮运行即生效）`); loadSuggestions(); }
      else alert(res.error || '采纳失败');
    });
    box.querySelectorAll('[data-reject]').forEach(b => b.onclick = async () => {
      const res = await API.promptSuggestionReject(b.dataset.reject);
      if (res.ok) loadSuggestions(); else alert(res.error || '拒绝失败');
    });
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadVersions(tpl) {
  const box = document.getElementById('cl-versions');
  try {
    const r = await API.promptVersions(tpl);
    const vs = r.versions || [];
    if (!vs.length) { box.innerHTML = '<span style="color:#77809a;font-size:12px">该模板暂无版本</span>'; return; }
    const adopted = vs.find(v => v.adopted);
    box.innerHTML = vs.map(v => `
      <div class="finding" style="background:${v.adopted ? '#e3f8f2' : '#f6f8fc'}">
        <span class="tag ${v.adopted ? 'green' : 'gray'}">${esc(v.version)}</span>
        <span class="tag">${esc(v.source)}</span>
        ${v.adopted ? '<span class="tag green">● 生效中</span>' : ''}
        ${v.parent_version ? `<span class="tag gray">← ${esc(v.parent_version)}</span>` : ''}
        <span style="font-size:12px;color:#77809a">${fmtTime(v.created_at)}</span>
        <div class="toolbar" style="margin-top:6px">
          ${v.adopted ? '<span class="tag green">当前生效</span>'
            : `<button class="btn" data-adopt-v="${v.id}">采纳 / 回滚至此</button>`}
          ${adopted && !v.adopted ? `<button class="btn ghost" data-diff="${v.id}" data-adopted="${adopted.id}">对比生效版</button>` : ''}
        </div>
      </div>`).join('');
    box.querySelectorAll('[data-adopt-v]').forEach(b => b.onclick = async () => {
      const res = await API.promptVersionAdopt(b.dataset.adoptV);
      if (res.ok) { alert(`已采纳 ${res.name}@${res.version}`); loadVersions(tpl); }
      else alert(res.error || '失败');
    });
    box.querySelectorAll('[data-diff]').forEach(b => b.onclick = async () => {
      const d = await API.promptVersionDiff(b.dataset.diff, b.dataset.adopted);
      const pre = document.getElementById('cl-diff');
      pre.style.display = 'block';
      pre.textContent = d.diff || '（无差异）';
      pre.scrollIntoView({ behavior: 'smooth' });
    });
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadAbVersions(tpl) {
  const r = await API.promptVersions(tpl);
  const vs = (r.versions || []).map(v => `<option value="${v.id}">${esc(v.name)}@${esc(v.version)} (${esc(v.source)})</option>`).join('');
  document.getElementById('cl-ab-v1').innerHTML = '<option value="">v1 版本</option>' + vs;
  document.getElementById('cl-ab-v2').innerHTML = '<option value="">v2 版本</option>' + vs;
}

async function runAB() {
  const box = document.getElementById('cl-ab-result');
  const tpl = document.getElementById('cl-ab-tpl').value;
  const v1 = document.getElementById('cl-ab-v1').value;
  const v2 = document.getElementById('cl-ab-v2').value;
  const angle = document.getElementById('cl-ab-angle').value.trim();
  const market = document.getElementById('cl-ab-market').value.trim() || 'US';
  if (!v1 || !v2) { alert('请为 v1 / v2 各选一个 Prompt 版本'); return; }
  if (!angle) { alert('请填写选题 / 角度'); return; }
  box.innerHTML = 'A/B 运行中…（两版 Prompt 各跑一次 produce 段并仿真，约数十秒）';
  try {
    const r = await API.promptABRun({ market, template: tpl, v1_id: Number(v1), v2_id: Number(v2), angle });
    const v1m = r.v1, v2m = r.v2, d = r.delta;
    box.innerHTML = `
      <div class="cards">
        <div class="card"><div class="kpi">${v1m.version}</div><div class="kpi-label">v1 版本</div>
          <div style="margin-top:6px;font-size:12px">质量 ${v1m.quality_avg} · CTR ${v1m.ctr} · 曝光 ${v1m.exposed} · 成本 ¥${v1m.cost_cny}</div></div>
        <div class="card"><div class="kpi">${v2m.version}</div><div class="kpi-label">v2 版本</div>
          <div style="margin-top:6px;font-size:12px">质量 ${v2m.quality_avg} · CTR ${v2m.ctr} · 曝光 ${v2m.exposed} · 成本 ¥${v2m.cost_cny}</div></div>
        <div class="card"><div class="kpi">Δ</div><div class="kpi-label">v2 − v1</div>
          <div style="margin-top:6px;font-size:12px">质量 ${d.quality_avg >= 0 ? '+' : ''}${d.quality_avg} · CTR ${d.ctr >= 0 ? '+' : ''}${d.ctr} · 成本 ${d.cost_cny >= 0 ? '+' : ''}${d.cost_cny}</div></div>
      </div>
      <div style="margin-top:10px;font-size:12px;color:#55607a">
        v1 内容 <a href="#content/${v1m.content_id}" style="color:#3a6df0">${v1m.content_id}</a> · v2 内容 <a href="#content/${v2m.content_id}" style="color:#3a6df0">${v2m.content_id}</a>
      </div>`;
  } catch (e) { box.innerHTML = errBox(e); }
}

/* ---------- 启动 ---------- */
(async () => {
  RunState.init();   // 先恢复未完成的供给任务（刷新浏览器也能续上轮询）
  ReviseState.init(); // 恢复进行中的内容重写状态
  route();
  try {
    const h = await API.health();
    document.getElementById('sys-status').textContent = `● ${h.llm.model}${h.llm.configured ? '' : '（未配置）'}`;
  } catch (e) {
    const el = document.getElementById('sys-status');
    el.textContent = '○ 后端连接失败';
    el.classList.add('err');
  }
})();

/* ---------- 人工校准（Evaluate 段人机闭环） ----------
   半分制 + 逐维理由逻辑，跑在正式控制台上（旧离线 score_sheet.html 已退役）：
   拉取待校准内容 → 真人打分（0.5 半分 + 理由）→ 提交后后端跑对齐计算 → 展示报告。 */
const CAL_DIMS = [
  ['accuracy', '事实准确性'], ['angle', '角度新颖度'], ['readability', '可读性'],
  ['local_fit', '本地化契合'], ['engagement', '吸引力/传播潜力'],
];
let calScores = {};
let calTotal = 0;

async function calibrateView() {
  root.innerHTML = '<div class="loading">加载待校准内容…</div>';
  try {
    const { samples } = await API.calibrationSamples();
    calScores = {};
    calTotal = samples.length;
    let html = `<div class="panel"><h2>🔬 人工校准 · LLM 评委对齐</h2>
      <p class="muted">逐条阅读内容全文，按五维直觉打分（1–5，支持 0.5 半分）。评委分对你不可见（避免锚定）。
      每篇五维都评完会自动标记为「已评」；最后点页面底部的「提交 N 篇已评」即可——后端将你的打分与 EditorAgent 评委分做 Spearman 对齐并生成报告。可只评几篇就提交。</p>
      <div style="margin:10px 0 4px">评审人：
        <input list="cal-rater-list" id="cal-rater" class="input" value="Strange" style="width:200px" placeholder="输入或选择评审人">
        <datalist id="cal-rater-list"><option value="Strange"><option value="评审B"><option value="评审C"></datalist>
        <span class="muted" style="font-size:12px">（同名续打会覆盖其旧分；不同评审人之间累积取平均）</span>
      </div>
      <div id="cal-progress" class="muted"></div></div>`;
    samples.forEach((s, i) => {
      let dims = '';
      CAL_DIMS.forEach(([k, label]) => {
        dims += `<div class="dimblk">
          <div class="dim"><label>${label}</label>
            <div class="dim-range-wrap"><input type="range" class="cal-range" min="1" max="5" step="0.5" value="3" data-id="${esc(s.id)}" data-dim="${k}" oninput="calSet(this)"></div>
            <span class="val" id="cv-${esc(s.id)}-${k}">3</span></div>
          <textarea class="reason" placeholder="这一维的打分理由（可选）" data-id="${esc(s.id)}" data-dim="${k}" oninput="calReason(this)"></textarea>
        </div>`;
      });
      const prior = s.n_raters
        ? `<span class="badge green">已有 ${s.n_raters} 人打分</span>`
        : '';
      const vals = (s.human_avg && typeof s.human_avg === 'object')
        ? Object.values(s.human_avg).filter(v => typeof v === 'number') : [];
      const avgTxt = (s.n_raters && vals.length)
        ? ` · 均分 ${(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2)}` : '';
      html += `<div class="card" id="calcard-${esc(s.id)}">
        <h3><span class="badge">${esc(s.market)}</span><span id="calmeta-${esc(s.id)}">${prior}${avgTxt}</span> ${i + 1}. ${esc(s.title)}</h3>
        <div class="excerpt">${esc(s.excerpt)}</div>${dims}
        <div class="card-actions">
          <span id="calbadge-${esc(s.id)}" class="badge">待评</span>
        </div></div>`;
    });
    html += `<div style="margin:16px 0; display:flex; align-items:center; gap:12px; flex-wrap:wrap">
      <button class="btn" id="cal-submit-btn" onclick="calSubmit()">提交 0 篇已评</button>
      <button class="btn ghost" onclick="calShowReport()">查看最新报告</button>
      <span id="cal-msg" class="muted"></span></div>
      <div id="cal-report"></div>`;
    root.innerHTML = html;
    document.querySelectorAll('.cal-range').forEach(calFill);
    calProgress();
  } catch (e) { root.innerHTML = errBox(e); }
}

function calSet(el) {
  const id = el.dataset.id, dim = el.dataset.dim;
  calScores[id] = calScores[id] || {};
  calScores[id][dim] = { score: parseFloat(el.value), reason: (calScores[id][dim] || {}).reason || '' };
  document.getElementById(`cv-${id}-${dim}`).textContent = el.value;
  calFill(el);
  calProgress();
}
function calReason(el) {
  const id = el.dataset.id, dim = el.dataset.dim;
  calScores[id] = calScores[id] || {};
  const cur = calScores[id][dim] || { score: 3 };
  calScores[id][dim] = { score: cur.score, reason: el.value };
}
function calProgress() {
  const total = calTotal || 0;
  let done = 0;
  const ids = Object.keys(calScores);
  for (const id of ids) {
    const complete = CAL_DIMS.every(([k]) => calScores[id][k] && calScores[id][k].score !== undefined);
    if (complete) done++;
    const badge = document.getElementById('calbadge-' + id);
    if (badge) badge.innerHTML = complete
      ? '<span class="badge green">✓ 已评</span>'
      : '<span class="badge">评分中</span>';
  }
  const el = document.getElementById('cal-progress');
  if (el) el.innerHTML = `已评 <b>${done}</b> / ${total} 条`;
  const btn = document.getElementById('cal-submit-btn');
  if (btn) btn.textContent = `提交 ${done} 篇已评`;
}
function calFill(el) {
  const p = ((parseFloat(el.value) - 1) / (5 - 1)) * 100;
  el.style.setProperty('--p', p + '%');
}
async function calSubmit() {
  const msg = document.getElementById('cal-msg');
  const rater = (document.getElementById('cal-rater') || {}).value || 'HUMAN';
  const payload = {};
  for (const id in calScores) {
    if (CAL_DIMS.every(([k]) => calScores[id][k] && calScores[id][k].score !== undefined))
      payload[id] = calScores[id];
  }
  if (!Object.keys(payload).length) {
    if (msg) msg.textContent = '⚠️ 还没有任何一篇完成五维评分（每维都要打分）';
    return;
  }
  msg.textContent = '提交中…';
  try {
    const r = await API.calibrationSubmit({ rater, scores: payload });
    if (r.ok) {
      const nCal = r.per_content ? Object.keys(r.per_content).length : (r.n || 0);
      const err = r.compute_error ? ` · ⚠️ 对齐未生成（${esc(r.compute_error)}）` : '';
      msg.innerHTML = `✅ 已保存（评审人 <b>${esc(rater)}</b>）· 共 <b>${nCal}</b> 条内容已校准`
        + (r.overall_rho != null ? ` · 整体 Spearman ρ=<b>${r.overall_rho}</b>，相邻一致 <b>${r.overall_adj}</b>` : '')
        + err;
      calShowReport();
    } else msg.textContent = '提交失败';
  } catch (e) { msg.textContent = '提交失败：' + e.message; }
}
async function calShowReport() {
  const box = document.getElementById('cal-report');
  if (!box) return;
  box.innerHTML = '<div class="loading">加载报告…</div>';
  try {
    const r = await API.calibrationReport();
    let h = `<div class="panel"><div class="md">${mdReport(r.markdown)}</div>`;
    if (r.chart) h += `<div class="chart-wrap">${r.chart}</div>`;
    h += `</div>`;
    box.innerHTML = h;
  } catch (e) { box.innerHTML = `<div class="muted">尚无报告（${esc(e.message)}）。先提交一次真人打分即可生成。</div>`; }
}
/* 极简 markdown → HTML（标题/表格/列表/引用/粗体/分段） */
function mdReport(md) {
  const lines = (md || '').split('\n');
  let html = '', inTable = false, tableBuf = [];
  const flushTable = () => {
    if (!tableBuf.length) return;
    html += '<table class="grid">' + tableBuf.map(r =>
      '<tr>' + r.map(c => `<td>${esc(c)}</td>`).join('') + '</tr>').join('') + '</table>';
    tableBuf = []; inTable = false;
  };
  for (const line of lines) {
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      if (cells.every(c => /^-+$/.test(c))) { continue; }
      tableBuf.push(cells); inTable = true; continue;
    }
    flushTable();
    if (line.startsWith('### ')) html += `<h4>${esc(line.slice(4))}</h4>`;
    else if (line.startsWith('## ')) html += `<h3>${esc(line.slice(3))}</h3>`;
    else if (line.startsWith('# ')) html += `<h2>${esc(line.slice(2))}</h2>`;
    else if (line.startsWith('> ')) html += `<blockquote>${esc(line.slice(2))}</blockquote>`;
    else if (line.startsWith('- ')) html += `<li>${esc(line.slice(2))}</li>`;
    else if (line.trim()) html += `<p>${esc(line).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')}</p>`;
  }
  flushTable();
  return html;
}
