/* TrendForge V2 控制台 SPA
   v2.1：按 AUDIT_REPORT_v2_frontend 整改——
   · 信号溯源入口（内容详情「信号源」Tab）
   · 母稿中文对照（zh_mirror 现含正文分节）
   · 市场档案补 media_landscape / insight_sources
   · 状态枚举中文化、toast 替代 alert、花钱操作二次确认
   · 仪表盘 KPI 增强 + 待办聚合 + 数据截至标注
   · 内容列表筛选/搜索；分析中心零值空态/市场筛选/CSV 导出
   · 知识库 pending 补丁闸门 + 逐项审批 + 文档浏览 + 回车检索
   · 校准页全文链接 + 中文对照切换；闭环页市场下拉/建议历史/仿真标注 */
const root = document.getElementById('view-root');
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const escMd = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

/* ---------- 轻提示 toast（替代 alert）与危险操作确认 ---------- */
function toast(msg, type = 'ok', ms = 4200) {
  let wrap = document.getElementById('toast-wrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'toast-wrap';
    document.body.appendChild(wrap);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = msg;
  wrap.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 400); }, ms);
}
function confirmCostly(msg) { return window.confirm(`${msg}\n\n该操作会消耗真实 LLM 额度，确认继续？`); }

/* ---------- 状态枚举 → 中文 ---------- */
const STATUS_CN = {
  done: '完成', ok: '正常', pass: '通过', running: '运行中', starting: '发起中',
  degraded: '降级', failed: '失败', rejected: '已驳回', reject: '不通过',
  revise: '需修改', pending: '待审', approved: '已通过', cancelled: '已取消',
  adopted: '已采纳', interrupted: '已失联', revising: '重写中', unknown: '未知',
};
function statusTag(s) {
  const map = { done: 'green', ok: 'green', pass: 'green', approved: 'green', adopted: 'green',
    running: '', degraded: 'orange', revise: 'orange', pending: 'orange', interrupted: 'gray',
    failed: 'red', rejected: 'red', reject: 'red', cancelled: 'gray' };
  return `<span class="tag ${map[s] || 'gray'}">${esc(STATUS_CN[s] || s)}</span>`;
}

/* ---------- 主管线 Agent 链（口径：主管线 11 个，加治理/分析链路共 14 个） ---------- */
const AGENT_CHAIN = [
  ['signal_scout', '信号捕捉'], ['trend_analyst', '趋势研判'], ['audience_insight', '受众洞察'],
  ['angle_editor', '角度设计'], ['researcher', '证据检索'], ['writer', '写作'],
  ['topic_guard', '选题守卫'], ['fact_checker', '事实核查'], ['editor', '总编审核'],
  ['format_adapter', '形态适配'], ['distributor', '分发策略'],
];
const AGENT_LABEL = Object.fromEntries(AGENT_CHAIN);

/* ---------- 视图路由 ---------- */
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
    } else if (j.status === 'cancelled') {
      this.stopPolling();
      this.set({ status: 'cancelled', error: j.error || '用户取消' });
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
      const cur = j.progress || '';
      const steps = AGENT_CHAIN.map(([k, label], i) => {
        const state = cur === k ? ' now' : '';
        return `<span class="ag-step${state}" title="第 ${i + 1} 步 · ${esc(label)}">${esc(label)}</span>`;
      }).join('<i class="ag-arrow">→</i>');
      box.innerHTML = `⏳ 流水线运行中 · <b>${esc(j.market || '')}</b>${j.job_id ? ` · 任务编号 ${esc(j.job_id.slice(0, 8))}` : ''}
        <span class="job-elapsed">已运行 ${fmtElapsed(j.started_at)}</span>
        <div class="ag-chain">${steps}</div>
        <div class="toolbar" style="margin-top:8px"><button class="btn ghost" id="job-cancel">取消任务</button></div>
        <br><small style="color:#77809a">任务在服务端运行，切换页面或刷新浏览器都不会中断，可随时回来查看进度。</small>`;
    } else if (j.status === 'cached') {
      box.innerHTML = `${close}✅ 命中缓存（秒开，零额度消耗）→ <a class="link" href="#content/${esc(j.content_id)}">查看内容</a>`;
    } else if (j.status === 'done') {
      box.innerHTML = `${close}✅ 供给完成（耗时 ${fmtElapsed(j.started_at, j.updated_at)}）→ <a class="link" href="#content/${esc(j.content_id)}">查看内容与执行轨迹</a>`;
    } else if (j.status === 'cancelled') {
      box.innerHTML = `${close}⏹ 已取消（${esc(j.error || '用户取消')}）。若流水线随后完成，产物仍会出现在「内容」页。`;
    } else if (j.status === 'failed') {
      box.innerHTML = `${close}❌ 失败：${esc(j.error)}`;
    } else if (j.status === 'error') {
      box.innerHTML = `${close}❌ 发起失败：${esc(j.error)}`;
    } else if (j.status === 'lost') {
      box.innerHTML = `${close}⚠️ 运行状态失联（后端可能已重启或休眠），任务结果请查看下方「运行历史」。`;
    }
    const btn = box.querySelector('.job-close');
    if (btn) btn.onclick = () => this.clear();
    const cancel = document.getElementById('job-cancel');
    if (cancel) cancel.onclick = async () => {
      if (!j.job_id) return;
      try {
        await API.cancelPipeline(j.job_id);
        this.stopPolling();
        this.set({ status: 'cancelled', error: '用户取消' });
        loadTasks();
        toast('已请求取消任务', 'ok');
      } catch (e) { toast(`取消失败：${esc(e.message)}`, 'err'); }
    };
  },

  paintBadge() {
    const link = document.querySelector('#sidebar nav a[data-view="pipeline"]');
    if (!link) return;
    let b = link.querySelector('.nav-badge');
    const j = this.job;
    const map = { running: ['运行中', 'running'], starting: ['运行中', 'running'], done: ['完成', 'done'], cached: ['完成', 'done'], failed: ['失败', 'failed'], error: ['失败', 'failed'], lost: ['失联', 'failed'], cancelled: ['已取消', 'failed'] };
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

/* ---------- 全局 A/B 任务状态 ----------
   A/B 要跑两次完整 Produce 链路（真机数分钟），改成后台任务 + 轮询：
   立即拿到 job_id，切页面/刷新都不丢进度，回来继续看。 */
const AB_KEY = 'tf_active_ab';

const ABState = {
  job: null,      // {job_id, status, progress, error, result, meta, started_at}
  timer: null,
  miss: 0,

  init() {
    try { this.job = JSON.parse(localStorage.getItem(AB_KEY) || 'null'); } catch (e) { this.job = null; }
    if (this.job && (this.job.status === 'running' || this.job.status === 'starting')) {
      this.startPolling();
      this.paint(true);
    }
  },

  set(patch) {
    this.job = Object.assign({}, this.job, patch, { updated_at: Date.now() });
    try {
      // 对比结果（含完整 quality/trace）只活在内存里，不落 localStorage：体量大且刷新后价值有限
      const slim = Object.assign({}, this.job);
      delete slim.result;
      localStorage.setItem(AB_KEY, JSON.stringify(slim));
    } catch (e) { }
    this.paint();
  },

  clear() {
    this.stopPolling();
    this.job = null;
    try { localStorage.removeItem(AB_KEY); } catch (e) { }
    this.paint();
  },

  running() { return this.job && (this.job.status === 'running' || this.job.status === 'starting'); },

  startPolling() {
    if (this.timer) return;
    this.miss = 0;
    this.timer = setInterval(() => this.tick(), 5000);
  },

  stopPolling() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  },

  async tick() {
    if (!this.job || !this.job.job_id) { this.stopPolling(); return; }
    let j;
    try { j = await API.promptABJob(this.job.job_id); }
    catch (e) {
      // 404 = 实例重启丢了内存 job；其余多为网络抖动/冷启动，先保持轮询
      if (++this.miss >= 3) { this.stopPolling(); this.set({ status: 'lost' }); }
      return;
    }
    if (j.status === 'done') {
      this.stopPolling();
      this.set({ status: 'done', progress: '完成', result: j.result });
      setTimeout(() => { if (this.job && this.job.status === 'done') this.clear(); }, 30000);
    } else if (j.status === 'failed') {
      this.stopPolling();
      this.set({ status: 'failed', progress: '', error: j.error || '未知错误' });
    } else {
      this.miss = 0;
      this.set({ status: 'running', progress: j.progress || this.job.progress || '执行中' });
    }
  },

  paint(force) {
    const box = document.getElementById('cl-ab-result');
    if (!box) return;                       // 当前不在系统进化页
    const j = this.job;
    if (!j) { if (force) box.innerHTML = '选择两版提示词并输入选题后运行'; return; }
    if (j.status === 'running' || j.status === 'starting') {
      const m = j.meta || {};
      box.innerHTML = `<div class="revise-status">⏳ A/B 运行中 · 当前环节：<b>${esc(j.progress || '已排队')}</b>
          <span class="job-elapsed">已运行 ${fmtElapsed(j.started_at)}</span>
          <br><small style="color:#77809a">${esc(m.template || '')} · ${esc(m.market || '')} · 选题「${esc(m.angle || '')}」
          —— 两版各跑一次完整链路，真机约数分钟。任务在服务端运行，切换页面或刷新浏览器都不会中断。</small></div>`;
    } else if (j.status === 'done' && j.result) {
      renderABResult(j.result, j.meta || {});
    } else if (j.status === 'done') {
      box.innerHTML = `<div class="revise-status done">✅ 上一次 A/B 已完成（详情未随刷新保留）。
        生成的内容可在「内容」列表里按时间查看。</div>`;
    } else if (j.status === 'failed') {
      box.innerHTML = `<div class="revise-status failed">❌ A/B 运行失败：${esc(j.error || '')}
          <br><small>可调整选题或换一对版本后重试。</small></div>`;
    } else if (j.status === 'lost') {
      box.innerHTML = `<div class="revise-status failed">⚠️ 任务状态失联（后端实例可能已重启，内存任务丢失）。
          请重新运行 A/B。<br><small>注：已生成的内容会留在内容列表里。</small></div>`;
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

/* ---------- 仪表盘：KPI 增强 + 待办聚合 + 数据截至标注 ---------- */
async function overview() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [h, c, t, sugs, patches, mkts, cal] = await Promise.all([
      API.health(), API.contents('', 200), API.tasks().catch(() => ({ tasks: [] })),
      API.promptSuggestions('pending').catch(() => ({ suggestions: [] })),
      API.kbPatches().catch(() => ({ patches: [] })), API.markets().catch(() => ({ markets: [] })),
      API.calibrationSamples().catch(() => ({ samples: [] })),
    ]);
    const contents = c.contents;
    const tasks = t.tasks || [];
    const byMarket = {};
    contents.forEach(x => byMarket[x.market] = (byMarket[x.market] || 0) + 1);
    const allMarkets = mkts.markets.map(m => m.code);
    const missing = allMarkets.filter(m => !byMarket[m]);
    const totalCost = tasks.reduce((s, x) => s + (Number(x.total_cost_cny) || 0), 0);
    const recent = tasks.slice(0, 10);
    const okRate = recent.length
      ? Math.round(recent.filter(x => x.status === 'done').length / recent.length * 100)
      : null;
    const needRevise = contents.filter(x => x.verdict === 'revise').length;
    const pendingSugs = (sugs.suggestions || []).length;
    const pendingPatches = (patches.patches || []).filter(p => p.status === 'pending').length;
    const latestAt = contents.length ? contents[0].created_at : '';
    const staleDays = latestAt ? Math.floor((Date.now() - new Date(latestAt + 'Z').getTime()) / 86400000) : null;
    /* 机器裁决与人工打分：都用真实数据算，不粉饰。
       这份快照里两者长期不一致（机器的通过门槛比人严）——这不是缺陷展示，
       而是「人工校准」模块存在的理由，值得摆在第一屏讲清楚。 */
    const judged = contents.filter(x => typeof x.quality_avg === 'number');
    const machineAvg = judged.length
      ? (judged.reduce((s, x) => s + x.quality_avg, 0) / judged.length) : null;
    const rated = (cal.samples || []).filter(s => s.n_raters > 0 && Object.values(s.human_avg || {}).some(v => typeof v === 'number'));
    const humanAvg = rated.length
      ? (rated.reduce((s, x) => {
        const vs = Object.values(x.human_avg).filter(v => typeof v === 'number');
        return s + vs.reduce((a, b) => a + b, 0) / vs.length;
      }, 0) / rated.length) : null;
    const machinePass = contents.filter(x => (x.verdict === 'pass' || x.verdict === 'publish')).length;

    const todoItems = [
      pendingSugs ? `<a class="link" href="#closedloop">🧠 ${pendingSugs} 条迭代建议待审</a>` : '',
      pendingPatches ? `<a class="link" href="#kb">📚 ${pendingPatches} 个知识库补丁待审</a>` : '',
      needRevise ? `<a class="link" href="#contents">✏️ ${needRevise} 条内容待重写（裁决=需修改）</a>` : '',
      RunState.job && (RunState.job.status === 'running' || RunState.job.status === 'starting')
        ? '<a class="link" href="#pipeline">⏳ 供给任务运行中</a>' : '',
    ].filter(Boolean);
    root.innerHTML = `
      <h1 class="page-title">仪表盘</h1>
      <p class="page-sub">AI Native 内容供给引擎 · 运行概览
        ${latestAt ? `<span class="tag gray" title="距今天 ${staleDays} 天">数据截至 ${fmtTime(latestAt)}</span>` : ''}</p>
      <div class="cards">
        <div class="card"><div class="kpi">${contents.length}</div><div class="kpi-label">已供给内容</div></div>
        <div class="card"><div class="kpi">¥${totalCost.toFixed(2)}</div><div class="kpi-label">近 ${tasks.length} 次任务累计成本</div></div>
        <div class="card"><div class="kpi">${okRate == null ? '—' : okRate + '%'}</div><div class="kpi-label">近 10 次供给成功率</div></div>
        <div class="card" title="分子=已产出内容的市场数；分母=已建档的市场数（未产出的可在市场档案页一键跑）">
          <div class="kpi">${Object.keys(byMarket).length}<span style="font-size:16px;color:#77809a">/${allMarkets.length}</span></div>
          <div class="kpi-label">已产出内容市场 / 已建档市场</div></div>
      </div>
      ${humanAvg != null && machineAvg != null ? `<div class="panel align-panel">
        <h3>机器裁决与人工打分</h3>
        <div class="impact-grid">
          <div class="impact-card"><h4>机器（总编 Agent）均分</h4>
            <div class="stat-big">${machineAvg.toFixed(1)}<span class="stat-suffix">/5</span></div>
            <div class="impact-row"><span>判定可发布</span><b>${machinePass} / ${contents.length} 条</b></div></div>
          <div class="impact-card"><h4>真人校准均分</h4>
            <div class="stat-big">${humanAvg.toFixed(1)}<span class="stat-suffix">/5</span></div>
            <div class="impact-row"><span>已校准内容</span><b>${rated.length} 条</b></div></div>
          <div class="impact-card"><h4>差值说明什么</h4>
            <div class="impact-delta" style="font-size:12.5px">
              机器门槛比人严 ${(humanAvg - machineAvg).toFixed(1)} 分：不少被机器判「需修改」的内容，
              人读完认为已经可用。<b>这正说明自动裁决不能孤立使用</b>——
              所以有了「人工校准」把人的判断回收成标准，再回到「系统进化」改提示词。
            </div>
            <div class="toolbar" style="margin-top:8px">
              <a class="btn ghost" href="#calibrate">去人工校准 →</a>
              <a class="btn ghost" href="#closedloop">看系统进化 →</a></div>
          </div>
        </div></div>` : ''}
      ${todoItems.length ? `<div class="panel todo-panel"><h3>待办</h3>${todoItems.map(x => `<div class="todo-item">${x}</div>`).join('')}</div>` : ''}
      <div class="panel"><h3>系统状态</h3>
        <p>LLM：${esc(h.llm.model)} ${h.llm.configured ? '<span class="tag green">已配置</span>' : '<span class="tag red">未配置（兜底模式）</span>'}
           · 知识库：${h.kb.documents} 文档 / ${h.kb.chunks} 分块</p>
        <p style="margin-top:8px">市场分布：${Object.entries(byMarket).map(([m, n]) => `<span class="tag">${m} × ${n}</span>`).join('') || '暂无'}
          ${missing.length ? `<span class="tag orange">无内容市场：${missing.map(m => `<a class="link" href="#markets">${esc(m)}</a>`).join('、')}</span>` : ''}</p>
      </div>
      <div class="panel"><h3>最新供给</h3>${contentsTable(contents.slice(0, 8))}
        ${contents.length > 8 ? '<p style="margin-top:8px"><a class="link" href="#contents">查看全部 →</a></p>' : ''}</div>`;
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 跑供给 ---------- */
async function pipeline() {
  root.innerHTML = '<div class="loading">加载市场列表…</div>';
  const markets = await API.markets().catch(() => ({ markets: [] }));
  root.innerHTML = `
    <h1 class="page-title">跑供给</h1>
    <p class="page-sub">选定目标市场 → 11 个 Agent 依次执行（系统共 14 个）→ 产出母稿 + 多形态 + 分发计划</p>
    <div class="toolbar">
      <select id="mk">${markets.markets.map(m => `<option value="${m.code}">${m.name} (${m.code})</option>`).join('')}</select>
      <button class="btn" id="run-btn">开始供给</button>
      <button class="btn ghost" id="force-btn">强制重跑（忽略缓存）</button>
      <select id="task-mk" title="按市场过滤运行历史"><option value="">全部市场</option>${markets.markets.map(m => `<option value="${m.code}">${m.code}</option>`).join('')}</select>
    </div>
    <p class="muted" style="font-size:12px;margin-top:8px">
      ⏱ 预估：单次全链路本地约 1–3 分钟、约 ¥0.3–0.6（DeepSeek）；线上免费实例因冷启动与休眠会更慢（实测可达 10 分钟级），
      但任务在服务端运行，可随时离开去看别的页面。命中缓存时秒开且零额度消耗。
    </p>
    <div class="job-box" id="job-box"></div>
    <div class="panel" id="tasks-panel"><h3>运行历史</h3><div class="loading">加载中…</div></div>
    <div class="panel"><h3>失败案例库</h3>
      <p class="muted" style="font-size:12px;margin-bottom:8px">
        每次被总编驳回或运行失败都会自动落一条案例，记录根因与已采取的修复动作——
        「已自愈」的那几条是在没有人工介入的情况下自动换题重试成功的。</p>
      <div id="badcases-panel" class="loading">加载中…</div></div>`;
  loadTasks();
  loadBadCases();
  document.getElementById('run-btn').onclick = () => startRun(false);
  document.getElementById('force-btn').onclick = () => {
    if (!confirmCostly('强制重跑将忽略缓存、完整执行一次 11 Agent 流水线。')) return;
    startRun(true);
  };
  document.getElementById('task-mk').onchange = () => loadTasks();
  // 视图重建后恢复正在运行/已完成的任务展示（切页面回来不会“看起来停了”）
  const active = RunState.job;
  if (active && active.market) {
    const sel = document.getElementById('mk');
    if (sel && [...sel.options].some(o => o.value === active.market)) sel.value = active.market;
  }
  // 市场档案页「跑该市场 →」带过来的预选市场
  const presetMk = sessionStorage.getItem('tf_pipeline_market');
  if (presetMk) {
    sessionStorage.removeItem('tf_pipeline_market');
    const sel = document.getElementById('mk');
    if (sel && [...sel.options].some(o => o.value === presetMk)) sel.value = presetMk;
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

/* 运行历史“结果”列渲染：
   - 标题完整显示 + CSS 优雅省略（.title-cell），hover 看全量，不再硬截断导致认不出是哪篇；
   - 未产出内容（失败/驳回）的任务显示状态徽标，报错仅在 hover 提示中给出，不再当成标题。
   - 因实例重启/休眠中断而 failed 的任务，显示为灰「任务中断」，与真·运行失败（红）区分。 */
function taskResultCell(x) {
  const out = x.output || {};
  if (out.content_id) {
    const title = (out.title || '').trim();
    const label = title
      ? `<span class="title-cell" title="${esc(title)}">${esc(title)}</span>`
      : '<span class="muted">查看内容</span>';
    let badge = '';
    // 同选题多次供给：最新一条标「↻重跑N次」，旧版标「↻旧版」，消除运行历史"重复"体感（第四条）
    if (x._dup_count > 1) {
      badge = x._is_latest
        ? ` <span class="tag gray" title="同选题更早运行于 ${esc(x._dup_earliest)}">↻ 重跑${x._dup_count - 1}次</span>`
        : ` <span class="tag gray" title="同选题已有更新版本，此为早期运行">↻ 旧版</span>`;
    }
    return `<a class="link" href="#content/${out.content_id}">${label}</a>${badge}`;
  }
  if (x.status === 'rejected') return '<span class="tag red">已驳回</span>';
  if (x.status === 'failed') {
    const interrupted = /中断|重启|休眠|失联/.test(x.error || '');
    return `<span class="tag ${interrupted ? 'gray' : 'red'}" title="${esc(x.error || '')}">${interrupted ? '任务中断' : '运行失败'}</span>`;
  }
  return '<span class="muted">—</span>';
}

/* 运行历史状态列：因实例重启/休眠中断而 failed 的任务标记为灰「已失联」，
   与真·运行失败（红）区分，避免僵尸任务误导运营对系统可靠性的判断。 */
function taskStatusCell(x) {
  if (x.status === 'failed' && /中断|重启|休眠|失联/.test(x.error || ''))
    return '<span class="tag gray">已失联</span>';
  return statusTag(x.status);
}

async function loadTasks() {
  const panel = document.getElementById('tasks-panel');
  try {
    const t = await API.tasks();
    const mk = (document.getElementById('task-mk') || {}).value || '';
    const list = (t.tasks || []).filter(x => !mk || x.market === mk);
    panel.innerHTML = `<h3>运行历史${mk ? ` · ${esc(mk)}` : ''} <span class="tag gray">${list.length} 条</span></h3><table>
      <tr><th>市场</th><th>类型</th><th>状态</th><th>结果</th><th>耗时</th><th>成本(¥)</th><th>时间</th></tr>
      ${list.map(x => {
        // 任务类型走专属映射（kind 的 revise 与质量裁决的 revise 同形不同义，不能共用一套）
        const kind = x.kind || 'pipeline';
        const kindTag = kind !== 'pipeline'
          ? `<span class="tag gray" title="任务类型：${esc(kindLabel(kind))}">${esc(kindLabel(kind))}</span>` : '';
        return `<tr class="${!x._is_latest ? 'dup-old' : ''}">
        <td>${x.market}</td><td>${kindTag || '<span class="tag gray">内容供给</span>'}</td>
        <td>${taskStatusCell(x)}</td>
        <td>${taskResultCell(x)}</td>
        <td>${(x.total_duration_ms / 1000).toFixed(0)}s</td><td>${x.total_cost_cny.toFixed(4)}</td>
        <td>${fmtTime(x.created_at)}</td></tr>`;
      }).join('') || '<tr><td colspan="7">暂无运行</td></tr>'}
    </table>`;
  } catch (e) { panel.innerHTML = errBox(e); }
}

/* 失败案例库：bad_cases 表一直有数据但前端零入口（产品设计里的失败案例库没做 UI） */
async function loadBadCases() {
  const box = document.getElementById('badcases-panel');
  if (!box) return;
  try {
    const r = await API.badCases();
    const rows = r.bad_cases || [];
    if (!rows.length) { box.innerHTML = '<span style="color:#77809a;font-size:12px">暂无失败案例</span>'; return; }
    box.innerHTML = rows.map(b => {
      const healed = b.status === 'auto_recovered';
      return `<div class="finding" style="background:${healed ? '#f6f8fc' : '#fff6f6'};border-left:3px solid ${healed ? '#9aa3b8' : '#d43d3d'}">
        <span class="tag ${healed ? 'gray' : 'red'}">${healed ? '已自愈' : '待处理'}</span>
        <span class="tag gray">${esc(b.category || '')}</span>
        <span class="tag gray">${fmtTime(b.created_at)}</span>
        <div style="margin:6px 0 3px"><b>${esc(b.title || '（无标题）')}</b></div>
        <div style="font-size:12px;color:#55607a">根因：${esc(b.root_cause || '—')}</div>
        ${b.fix_action ? `<div style="font-size:12px;color:#0d9268">修复动作：${esc(b.fix_action)}</div>` : ''}
      </div>`;
    }).join('');
  } catch (e) { box.innerHTML = errBox(e); }
}

/* ---------- 内容列表：市场/裁决筛选 + 标题搜索 + 计数 ---------- */
const CONTENTS_F = { market: '', verdict: '', q: '' };

async function contents() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  const preset = sessionStorage.getItem('tf_contents_market');
  if (preset) { CONTENTS_F.market = preset; sessionStorage.removeItem('tf_contents_market'); }
  try {
    const [c, mkts] = await Promise.all([API.contents('', 200), API.markets().catch(() => ({ markets: [] }))]);
    const all = c.contents;
    const mkOpts = mkts.markets.map(m => `<option value="${m.code}">${m.name} (${m.code})</option>`).join('');
    root.innerHTML = `
      <h1 class="page-title">内容</h1>
      <p class="page-sub">每条内容 = 母稿 + 多形态派生 + 分发计划 + 完整执行轨迹
        <span class="tag gray">演示知识库为合成知识库：部分实体/引语为教学用虚构，配置真实信源后自动消除</span></p>
      <div class="toolbar">
        <select id="cf-mk"><option value="">全部市场</option>${mkOpts}</select>
        <select id="cf-st" title="按质量裁决筛选——列表里看到的就是这一列">
          <option value="">全部裁决</option>
          <option value="pass">${VERDICT_LABEL.pass}</option>
          <option value="revise">${VERDICT_LABEL.revise}</option>
          <option value="reject">${VERDICT_LABEL.reject}</option>
        </select>
        <input id="cf-q" placeholder="搜索标题 / 选题…" style="width:220px">
        <span class="tag gray" id="cf-count"></span>
      </div>
      <div class="panel" id="cf-table"></div>`;
    const selMk = document.getElementById('cf-mk');
    const selSt = document.getElementById('cf-st');
    if (CONTENTS_F.market) selMk.value = CONTENTS_F.market;
    selMk.onchange = () => { CONTENTS_F.market = selMk.value; paintContentsTable(all); };
    selSt.onchange = () => { CONTENTS_F.verdict = selSt.value; paintContentsTable(all); };
    const qInput = document.getElementById('cf-q');
    qInput.value = CONTENTS_F.q;
    qInput.oninput = () => { CONTENTS_F.q = qInput.value.trim().toLowerCase(); paintContentsTable(all); };
    paintContentsTable(all);
  } catch (e) { root.innerHTML = errBox(e); }
}

function paintContentsTable(all) {
  const list = all.filter(x =>
    (!CONTENTS_F.market || x.market === CONTENTS_F.market)
    // 按「质量裁决」筛选：用户看到列表里挂着的就是这一列，按 status 筛选会让人筛不出来
    && (!CONTENTS_F.verdict || (x.verdict || '') === CONTENTS_F.verdict)
    && (!CONTENTS_F.q || `${x.title || ''} ${x.topic || ''}`.toLowerCase().includes(CONTENTS_F.q)));
  document.getElementById('cf-count').textContent = `${list.length} / ${all.length} 条`;
  document.getElementById('cf-table').innerHTML = contentsTable(list);
}

function contentsTable(list) {
  return `<table>
    <tr><th>标题</th><th>市场</th><th>质量</th><th>形态</th><th>时间</th></tr>
    ${list.map(x => `<tr>
      <td><a class="link title-cell" href="#content/${x.id}" title="${esc(x.title || '（无标题）')}">${esc(x.title || '（无标题）')}</a>
        ${verdictTag(x.verdict)}
        ${x.is_fallback ? '<span class="tag orange" title="该流水线有环节走降级兜底，非完整真实 LLM 产出">含兜底</span>' : ''}
        ${x.status && x.status !== 'published' ? `<span class="tag red">${esc(STATUS_CN[x.status] || x.status)}</span>` : ''}</td>
      <td><span class="tag">${x.market}</span></td>
      <td>${x.quality_avg ? x.quality_avg.toFixed(1) : '-'}</td>
      <td>${x.formats.map(f => `<span class="tag gray">${esc(FMT_META[f]?.label || f)}</span>`).join('')}</td>
      <td>${fmtTime(x.created_at)}</td></tr>`).join('') || '<tr><td colspan="5">无匹配内容（调整筛选或先去「跑供给」）</td></tr>'}
  </table>`;
}

/* ---------- 内容详情 ---------- */
/* 中文对照状态：非中文市场的产出需要给中文运营一份回译对照，按需生成 + 缓存 */
const ZH = {
  // 全球化内容产品：默认展示「原文 + 中文对照」，原文立刻可见；
  // 旧默认值是 'zh'（仅中文），会把 AI 生成的英文母稿整篇藏起来，还要等 20–40s 回译。
  mode: localStorage.getItem('tf_zh_mode') || 'both',  // both 双语(默认) | src 原文 | zh 仅中文
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
    const advice = reviseAdviceText(c.quality);
    root.innerHTML = `
      <p style="margin-bottom:6px"><a class="link" href="#contents">← 返回内容列表</a></p>
      <h1 class="page-title">${esc(c.title || '（无标题）')}</h1>
      <p class="page-sub"><span class="tag">${c.market}</span> <span class="tag gray">${c.language}</span>
        质量 ${c.quality_avg?.toFixed(1) || '-'}/5 · 裁决 ${esc(VERDICT_LABEL[c.verdict] || c.verdict || '-')}
        ${c.is_fallback ? '<span class="tag orange">含兜底环节</span>' : ''}
        ${c.verdict === 'revise' ? '<button id="btn-revise" class="btn-primary">按修改意见重写</button>' : ''}</p>
      ${c.verdict === 'revise' ? `<div id="revise-status" class="revise-status" style="display:none"></div>
        ${advice ? `<div class="panel revise-advice"><b>总编修改意见</b><br>${escMd(advice)}</div>` : ''}` : ''}
      ${c.is_fallback ? '<div class="panel fallback-note">⚠ 本条为兜底路径产出（部分环节未走真实 LLM）。演示知识库为合成知识库，文中实体/引语可能为教学用虚构。</div>' : ''}
      <div class="tabs">
        <a data-tab="article" class="active">母稿</a><a data-tab="formats">多形态 (${Object.keys(c.formats || {}).length})</a>
        <a data-tab="dist">分发计划</a><a data-tab="brief">选题简报</a>
        <a data-tab="signals">信号源 (${(c.signals || []).length})</a>
        <span class="tabs-sep" title="以下是过程证据">证据</span>
        <a data-tab="quality" class="tab-ev">质量</a><a data-tab="trace" class="tab-ev">执行轨迹</a>
      </div>
      <div id="tab-body"></div>`;
    document.querySelectorAll('.tabs a').forEach(a => a.onclick = () => {
      document.querySelectorAll('.tabs a').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
      ZH.tab = a.dataset.tab;
      paintTab();
    });
    const btnRevise = document.getElementById('btn-revise');
    if (btnRevise) btnRevise.onclick = () => {
      if (!confirmCostly('重写将按总编修改意见重跑 写作 → 事实核查 → 总编审核，并刷新多形态。')) return;
      startRevise(c.id, c.title);
    };
    ReviseState.paint();  // 还原进行中的重写状态（切回来不会"看起来停了"）
    paintTab();
    if (ZH.content?.needs_zh) ensureZh();  // 非中文市场：按需生成中文对照（含母稿正文）
  } catch (e) { root.innerHTML = errBox(e); }
}

function reviseAdviceText(q) {
  if (!q || typeof q !== 'object') return '';
  const parts = [];
  if (q.comments) parts.push(String(q.comments));
  const ra = q.revision_advice;
  if (Array.isArray(ra) && ra.length) parts.push(ra.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join('；'));
  else if (typeof ra === 'string' && ra) parts.push(ra);
  return parts.join('\n');
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

/* 缺中文对照时按需触发生成（一次调用同时覆盖简报+多形态+母稿正文） */
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
    if (!confirmCostly('重新生成中文对照将再次调用回译模型。')) return;
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
  const zbody = (ZH.data && ZH.data.body) || {};
  const zsecs = zbody.sections || [];
  const sections = (c.body?.sections || []).map((s, i) => {
    const z = zsecs[i] || {};
    return `<h4>${bi(s.heading, z.heading)}</h4><p>${linkifyEv(bi(s.text, z.text), c.evidences)}</p>`;
  }).join('');
  return `<div class="panel article-body">
    ${zhBar(c)}
    <p style="color:#77809a;font-size:13px;margin:10px 0">${bi(c.summary, (ZH.data || {}).summary)}</p>${sections}
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
  return `<div class="panel"><h3>角度设计 · 选题简报（AI 的"主编判断"）</h3>
    ${zhBar(c)}
    <dl class="brief-grid">
      <dt>选题</dt><dd>${bi(b.topic, z.topic)}</dd>
      <dt>角度</dt><dd>${bi(b.angle, z.angle)}</dd>
      <dt>钩子</dt><dd>${bi(b.hook, z.hook)}</dd>
      <dt>受众</dt><dd>${bi(b.audience, z.audience)}</dd>
      <dt>风格</dt><dd><span class="tag gray">${esc(STYLE_LABEL[b.style] || b.style || '—')}</span></dd>
      <dt>为何是现在</dt><dd>${bi(b.why_now, z.why_now)}</dd>
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
const platformLabel = (p) => PLATFORM_LABEL[p] || p;

/* 质量裁决枚举 → 中文 */
const VERDICT_LABEL = { publish: '可发布', revise: '需修改', reject: '不通过', pass: '可发布' };
const VERDICT_TAG = { publish: 'green', pass: 'green', revise: 'orange', reject: 'red' };
function verdictTag(v) {
  if (!v) return '';
  return `<span class="tag ${VERDICT_TAG[v] || 'gray'}">${esc(VERDICT_LABEL[v] || v)}</span>`;
}

/* ---------- 真实信号溯源：展示信号捕捉环节实时抓取的源头（来源/时间/真实互动/原文链接） ---------- */
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
    <p class="muted">信号由「信号捕捉」环节从 Hacker News / Dev.to / GDELT 等公开源实时抓取，互动数据为真实人类消费行为。下方链接可点击溯源。</p>
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
  return `<div class="panel"><h3>形态适配 · 一稿多发（${Object.keys(fmts).length} 种形态）</h3>
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
    ${v.cta ? `<div class="fmt-row"><span class="fmt-k">行动号召</span><span>${bi(v.cta, z.cta)}</span></div>` : ''}
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
  hook: '钩子', cta: '行动号召', question: '提问', angles: '讨论角度', points: '要点',
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

/* ---------- 全局标签翻译层（2026-09-12 全站审计后统一） ----------
   根因：枚举翻译过去只零星做了几处（内容列表形态列 / 分发计划 / 市场档案平台），
   而分析中心的图表渲染函数直接把后端返回的数据库原值（article / video_script /
   signal_scout / accuracy / ab …）画到了图上，同一批 token 还在 Trace、质量 Tab、
   知识库类目、建议文案里反复泄漏。
   这里把所有「面向读者的枚举」一次性收敛：任何渲染点在输出前过一遍 lb()，
   未知 key 原样返回（保证不丢信息，也不会把正文误译）。

   注意：任务类型 kind 的 'revise' 与质量裁决 verdict 的 'revise' 同形不同义
   （前者=「按意见重写」任务，后者=「需修改」裁决），因此任务类型单独用 kindLabel()，
   不并入全局 LABEL。 */
const TEMPLATE_LABEL = {
  writer: '写作', editor: '总编审核', distributor: '分发策略', angle_editor: '角度设计',
  signal_scout: '信号捕捉', trend_analyst: '趋势研判', audience_insight: '受众洞察',
  researcher: '证据检索', format_adapter: '形态适配', fact_checker: '事实核查',
  topic_guard: '选题守卫', kb_curator: '知识库治理', feedback_analyst: '反馈分析',
  zh_mirror: '中文对照',
};
const KIND_LABEL = {
  pipeline: '内容供给', ab: 'A/B 对比', feedback: '反馈分析',
  revise: '按意见重写', kb_curate: '知识库策展',
};
const SOURCE_LABEL = {
  file: '初始版本', ai_suggested: 'AI 提议', ai_generated: 'AI 生成', manual: '人工创建',
};
const MODEL_LABEL = {
  'deepseek-v4-flash': 'DeepSeek V4 Flash', 'deepseek-chat': 'DeepSeek Chat',
  'deepseek-reasoner': 'DeepSeek Reasoner', rule: '规则引擎', '': '规则引擎',
};
const CATEGORY_LABEL = {
  ai: 'AI', tech: '科技', business: '商业', ev: '电动车', sports: '体育',
  entertainment: '娱乐', finance: '财经', policy: '政策', science: '科学',
  health: '健康', crypto: '加密', education: '教育', culture: '文化',
  world: '国际', general: '综合',
};
const EVENT_LABEL = {
  exposed: '曝光', clicked: '点击', finished: '完读', liked: '点赞',
  shared: '分享', commented: '评论', negative: '负反馈',
};

const LABEL = {
  ...Object.fromEntries(AGENT_CHAIN),
  ...Object.fromEntries(Object.entries(FMT_META).map(([k, v]) => [k, v.label])),
  ...PLATFORM_LABEL, ...STYLE_LABEL, ...VERDICT_LABEL, ...KV_LABELS,
  ...TEMPLATE_LABEL, ...CATEGORY_LABEL, ...EVENT_LABEL,
  // 守卫/重写等内部键（质量 Tab、Trace 会暴露）
  topic_guard: '选题守卫', evidence_guard: '证据守卫', language_guard: '语言守卫',
  _revise: '重写记录', review_rounds: '审核轮次', n_raters: '评分人数',
  title_fixed: '标题已修正', body_fixed: '正文已修正', summary_fixed: '摘要已修正',
  retire: '退役', add: '入库', update: '更新',
};
const lb = (s) => { const k = String(s ?? ''); return LABEL[k] ?? k; };
/* 图表用：只翻译「小写枚举」（形态 / Agent / 维度 / 类目…），
   市场代码（US/JP/BR）与专名（LINE/YouTube/DeepSeek）保持原样，避免把代号误译成通用词。 */
const lbEnum = (s) => {
  const k = String(s ?? '');
  return (LABEL[k] !== undefined && k === k.toLowerCase()) ? LABEL[k] : k;
};
const kindLabel = (k) => KIND_LABEL[k] || String(k || '—');
const sourceLabel = (k) => SOURCE_LABEL[k] || String(k || '');
const modelLabel = (m) => MODEL_LABEL[m] || String(m || '规则');
/* 漏斗/事件阶段形如「曝光 Exposed」「Liked+Shared」：中文在前、英文冗余在后 */
function stageLabel(s) {
  const raw = String(s ?? '');
  if (EVENT_LABEL[raw]) return EVENT_LABEL[raw];
  const m = raw.match(/^(.+?)\s+([A-Za-z][A-Za-z+]*)$/);
  return m ? m[1] : raw;
}

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
    ${zhBar(c2q(q))}${kvTree(q, zq)}</div>`;
}
// renderQuality 里 zhBar 需要 content 对象；从 ZH 取
function c2q() { return ZH.content || {}; }

function renderDist(d) {
  const plan = d?.plan || [];
  if (!plan.length) return '<div class="panel">无分发计划</div>';
  const zd = (ZH.data && ZH.data.distribution) || {};
  const zplan = zd.plan || [];
  return `<div class="panel"><h3>分发策略 · 分发计划</h3>${zhBar(ZH.content || {})}<table>
    <tr><th>#</th><th>平台</th><th>形态</th><th>受众</th><th>时段</th><th>理由</th></tr>
    ${plan.map((p, i) => `<tr><td>${p.priority}</td>
      <td>${esc(platformLabel(p.platform))}</td>
      <td><span class="tag">${esc(FMT_META[p.format]?.label || p.format)}</span></td>
      <td>${bi(p.audience, zplan[i] && zplan[i].audience)}</td>
      <td>${bi(p.timing, zplan[i] && zplan[i].timing)}</td>
      <td>${bi(p.reason, zplan[i] && zplan[i].reason)}</td></tr>`).join('')}
  </table></div>`;
}

async function renderTrace(contentId) {
  const t = await API.trace(contentId);
  return `<div class="panel"><h3>执行轨迹（${t.spans.length} 步 · 总耗时 ${(t.task.total_duration_ms / 1000).toFixed(0)}s · ¥${t.task.total_cost_cny.toFixed(4)} · 审核回退 ${t.task.review_rounds} 轮）</h3>
    ${t.spans.map(s => `<div class="trace-step ${s.status}">
      <div class="agent">${esc(lb(s.agent))} ${statusTag(s.status)}</div>
      <div class="meta">${esc(modelLabel(s.model))} · 输入 ${s.tokens_in} + 输出 ${s.tokens_out} 词元 · ${s.duration_ms}ms</div>
      <div class="decision">${esc(s.decision_reason)}</div>
      ${(s.warnings || []).map(w => `<div class="meta" style="color:#e08a00">⚠ ${esc(w)}</div>`).join('')}
    </div>`).join('')}</div>`;
}

/* ---------- 市场档案：补 media_landscape / insight_sources + 平台中文 + CTA ---------- */
function gotoPipelineMarket(code) {
  sessionStorage.setItem('tf_pipeline_market', code);
  location.hash = '#pipeline';
}
function gotoContentsMarket(code) {
  CONTENTS_F.market = code;
  sessionStorage.setItem('tf_contents_market', code);
  location.hash = '#contents';
}

/* media_landscape 结构：{mainstream:[], social:[], notes} 或字符串——两种都渲染成人话 */
function mediaLandscapeHtml(ml) {
  if (!ml) return '—';
  if (typeof ml === 'string') return esc(ml);
  const parts = [];
  if (Array.isArray(ml.mainstream) && ml.mainstream.length) parts.push(`主流媒体：${ml.mainstream.join('、')}`);
  if (Array.isArray(ml.social) && ml.social.length) parts.push(`社媒：${ml.social.join('、')}`);
  if (ml.notes) parts.push(esc(ml.notes));
  return parts.length ? parts.map(p => `<div style="margin:2px 0">${p}</div>`).join('') : '—';
}

async function markets() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const m = await API.markets();
    root.innerHTML = `
      <h1 class="page-title">市场档案</h1>
      <p class="page-sub">人定义标准的核心载体：AI 据此理解当地内容生态、文化语境与用户需求（含媒体生态与洞察依据出处）</p>
      <div class="market-grid">${m.markets.map(x => `
        <div class="panel market-card">
          <h3>${esc(x.name)} <span class="tag">${x.code}</span>
            <span class="tag gray">${esc((x.language || '').toUpperCase())}</span></h3>
          <div class="section"><b>调性 / 默认风格</b>${esc(x.tone)} · ${esc(STYLE_LABEL[x.default_style] || x.default_style)}</div>
          <div class="section"><b>媒体生态</b>${mediaLandscapeHtml(x.media_landscape)}</div>
          <div class="section"><b>文化语境与禁忌</b><ul>${x.culture_notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul></div>
          <div class="section"><b>平台生态</b>${Object.entries(x.platforms).map(([p, s]) =>
    `<span class="tag" title="${esc((s.formats || []).join('/'))}">${esc(platformLabel(p))}: ${(s.formats || []).map(f => FMT_META[f]?.label || f).join('/')}</span>`).join('')}</div>
          <div class="section"><b>兴趣画像</b>${Object.entries(x.interests).map(([k, v]) =>
    `<span class="tag gray">${esc(lb(k))} ${v}</span>`).join('')}</div>
          ${x.insight_sources && x.insight_sources.length ? `<div class="section"><b>洞察依据（公开报告）</b><ul>${x.insight_sources.map(s =>
    `<li style="font-size:12px;color:#55607a">${esc(typeof s === 'string' ? s : (s.name || s.title || JSON.stringify(s)))}${typeof s === 'object' && s.url ? ` <a class="link" href="${esc(s.url)}" target="_blank" rel="noopener">链接 ↗</a>` : ''}</li>`).join('')}</ul></div>` : ''}
          <div class="toolbar" style="margin-top:10px">
            <button class="btn ghost" data-run="${esc(x.code)}">跑该市场 →</button>
            <button class="btn ghost" data-see="${esc(x.code)}">看该市场内容 →</button>
          </div>
        </div>`).join('')}</div>`;
    root.querySelectorAll('[data-run]').forEach(b => b.onclick = () => gotoPipelineMarket(b.dataset.run));
    root.querySelectorAll('[data-see]').forEach(b => b.onclick = () => gotoContentsMarket(b.dataset.see));
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 评估中心：KPI 跟随市场选择器（含全局）、形态中文标签 ---------- */
const EVAL_F = { market: '' };

async function evalView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [ov, reps, mkts] = await Promise.all([
      API.analyticsOverview(EVAL_F.market), API.reports(), API.markets().catch(() => ({ markets: [] })),
    ]);
    const mktLabel = EVAL_F.market ? esc(EVAL_F.market) : '全部市场';
    // 四个 KPI 全部来自仿真器（锚定真实信号分布的行为仿真）——必须自带角标，
    // 不能让读的人误以为是真实分发数据（与分析中心口径一致）。
    const simBadge = '<span class="tag orange" title="数据来自仿真器，非真实分发数据">仿真</span>';
    root.innerHTML = `
      <h1 class="page-title">评估中心</h1>
      <p class="page-sub">消费反馈 → 指标分析 → 反馈分析迭代建议（闭环的最后一步）</p>
      <div class="panel sim-banner">
        ⚠️ <b>本页全部消费指标为仿真口径</b>：CTR / 完读率 / 互动率 / 负反馈率均来自
        仿真器锚定真实信号分布拟合出的行为仿真（真实分发光标为平台私有数据，无法获取）。
        每份仿真都带 <span class="tag orange">仿真</span> 角标，与分析中心口径一致。
      </div>
      <div class="toolbar">
        <button class="btn ghost" id="sim-btn">模拟消费事件</button>
        <span style="width:14px"></span>
        <label style="font-size:12px;color:#55607a">指标口径市场：</label>
        <select id="ov-mk"><option value="">全部市场</option>${mkts.markets.map(m => `<option value="${m.code}" ${m.code === EVAL_F.market ? 'selected' : ''}>${m.name}</option>`).join('')}</select>
        <span class="tag gray">当前：${mktLabel}</span>
        <a class="btn" style="margin-left:auto" href="#closedloop">生成迭代建议 →</a>
      </div>
      <div class="cards">
        <div class="card"><div class="kpi">${(ov.ctr * 100).toFixed(1)}%</div><div class="kpi-label">CTR（${ov.exposed} 曝光）${simBadge}</div></div>
        <div class="card"><div class="kpi">${(ov.finish_rate * 100).toFixed(1)}%</div><div class="kpi-label">完读率${simBadge}</div></div>
        <div class="card"><div class="kpi">${(ov.engagement * 100).toFixed(1)}%</div><div class="kpi-label">互动率${simBadge}</div></div>
        <div class="card"><div class="kpi">${(ov.neg_rate * 100).toFixed(1)}%</div><div class="kpi-label">负反馈率${simBadge}</div></div>
      </div>
      <div class="panel"><h3>分形态 CTR ${simBadge}</h3>
        ${Object.entries(ov.by_format_ctr || {}).map(([f, v]) =>
    `<span class="tag">${esc(FMT_META[f]?.label || f)}: ${(v * 100).toFixed(1)}%</span>`).join('') || '暂无数据（先模拟事件）'}</div>
      <div class="panel"><h3>评估报告</h3><div id="reports">
        ${reps.reports.map(r => `
          <div style="margin-bottom:18px">
            <p style="font-size:12px;color:#77809a">${fmtTime(r.created_at)} · 质量均分 ${r.quality_avg}</p>
            ${r.findings.map(f => `<div class="finding">📊 ${esc(f)}</div>`).join('')}
            ${r.suggestions.map(s => `<div class="suggestion">💡 ${esc(s)}</div>`).join('')}
          </div>`).join('') || `<div class="chart-zero">
            <b>暂无评估报告</b> —— 这不是功能缺失，而是这条链路要由人触发：
            <ol style="margin:8px 0 0 18px">
              <li>先在上面点「模拟消费事件」生成消费数据（仿真口径）；</li>
              <li>再去 <a class="link" href="#closedloop">系统进化 → 迭代建议</a> 运行反馈分析，
                  产出「发现 + 可采纳建议」；</li>
              <li>采纳后新版本即刻生效，并在同页「采纳效果回收」看到前后对比。</li>
            </ol></div>`}
      </div>
      <p style="margin-top:8px;font-size:12px;color:#55607a">迭代建议的生成与「采纳」统一在 <a class="link" href="#closedloop">系统进化 →</a> 完成（AI 提议 → 人审闸门），全站只此一个入口。</p></div>`;
    document.getElementById('sim-btn').onclick = () => {
      if (!confirmCostly('将为每条内容生成约 300 条仿真消费事件（锚定真实信号分布，不消耗 LLM 额度，但会追加事件数据）。')) return;
      (async () => {
        try {
          const r = await API.simulate();
          toast(`已模拟 ${r.events} 条事件`, 'ok');
          evalView();
        } catch (e) { toast(`模拟失败：${esc(e.message)}`, 'err'); }
      })();
    };
    // 反馈分析的唯一入口在「系统进化」页（本页只保留跳转），避免同动作两个入口
    document.getElementById('ov-mk').onchange = (e) => { EVAL_F.market = e.target.value; evalView(); };
  } catch (e) { root.innerHTML = errBox(e); }
}

/* ---------- 知识库：回车检索 + pending 补丁闸门 + 过期清单 + 文档浏览 ---------- */
async function kbView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const s = await API.kbStats();
    root.innerHTML = `
      <h1 class="page-title">知识库</h1>
      <p class="page-sub">BM25 检索 · 全部事实可溯源（LLM 不联网，知识库是唯一事实来源）
        <span class="tag orange">合成知识库</span></p>
      <div class="panel synthetic-note">⚠️ 演示文档为教学用合成语料：部分实体、引语为虚构，用于演示检索与治理链路。
        配置真实信源后同一套流程即可接入真实语料。</div>
      <div class="cards">
        <div class="card"><div class="kpi">${s.documents}</div><div class="kpi-label">文档</div></div>
        <div class="card"><div class="kpi">${s.chunks}</div><div class="kpi-label">分块</div></div>
      </div>
      <div class="panel"><h3>类目分布</h3>${Object.entries(s.by_category).map(([k, v]) => `<span class="tag">${esc(lb(k))} × ${v}</span>`).join('')}</div>
      <div class="panel"><h3>检索演示</h3>
        <div class="toolbar"><input id="kb-q" placeholder="试试：AI agent / 电动车 出口" style="width:340px"><button class="btn" id="kb-go">检索</button></div>
        <div id="kb-results"></div></div>
      <div class="panel"><h3>文档浏览</h3><div id="kb-docs" class="loading">加载文档清单…</div></div>
      <div class="panel"><h3>知识库治理（AI 提议 · 人审闸门）</h3>
        <div id="kb-fresh" class="loading">加载新鲜度…</div>
        <div class="toolbar" style="margin-top:14px">
          <button class="btn" id="kb-curate">运行知识库策展</button>
          <button class="btn ghost" id="kb-reload">刷新</button>
        </div>
        <div id="kb-patch"></div>
        <div id="kb-history" style="margin-top:18px"></div>
      </div>`;
    const doSearch = async () => {
      const q = document.getElementById('kb-q').value.trim();
      if (!q) return;
      const box = document.getElementById('kb-results');
      box.innerHTML = '<div class="loading">检索中…</div>';
      try {
        const r = await API.kbSearch(q);
        box.innerHTML = r.results.map((e, i) =>
          `<div class="finding"><span class="rank">#${i + 1}</span> <b>${esc(e.doc_title)}</b>
           <span class="tag gray">${esc(e.source)}</span>${e.published_at ? ` <span class="tag gray">${esc(String(e.published_at).slice(0, 10))}</span>` : ''}${e.credibility ? ` <span class="tag gray">可信度 ${e.credibility}</span>` : ''}
           ${e.is_stale ? '<span class="tag orange">⚠ 待核实</span>' : ''} · 相关度 ${e.score}<br>
         <span style="color:#77809a">${esc(e.text.slice(0, 140))}${e.text.length > 140 ? '…' : ''}</span></div>`).join('') || '无结果';
      } catch (e) { box.innerHTML = errBox(e); }
    };
    document.getElementById('kb-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
    document.getElementById('kb-go').onclick = doSearch;
    document.getElementById('kb-curate').onclick = () => {
      if (!confirmCostly('运行知识库策展将调用一次 LLM 生成补丁（AI 只提议、人审后才入库）。')) return;
      runCurate();
    };
    document.getElementById('kb-reload').onclick = () => loadGovernance();
    loadDocuments();
    loadGovernance();
  } catch (e) { root.innerHTML = errBox(e); }
}

async function loadDocuments() {
  const box = document.getElementById('kb-docs');
  if (!box) return;
  try {
    const r = await API.kbDocuments();
    const docs = r.documents || [];
    box.innerHTML = `<details><summary style="cursor:pointer">共 ${docs.length} 篇文档（点开浏览）</summary>
      <table style="margin-top:10px">
        <tr><th>标题</th><th>来源</th><th>类目</th><th>发布</th><th>可信度</th></tr>
        ${docs.map(d => `<tr>
          <td><span class="title-cell" title="${esc(d.title)}">${esc(d.title)}</span>
            ${d.is_stale ? '<span class="tag orange">过期</span>' : ''}
            ${d.url ? `<a class="link" href="${esc(d.url)}" target="_blank" rel="noopener">↗</a>` : ''}</td>
          <td>${esc(d.source || '—')}</td>
          <td><span class="tag gray">${esc(lb(d.category))}</span></td>
          <td>${esc(String(d.published_at || '—').slice(0, 10))}</td>
          <td>${d.credibility}</td></tr>`).join('')}
      </table></details>`;
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadGovernance() {
  try {
    const [f, p] = await Promise.all([API.kbFreshness(), API.kbPatches()]);
    document.getElementById('kb-fresh').innerHTML =
      `参考日期 <b>${esc(f.ref_date)}</b> · 有效文档 <b>${f.total}</b> · 过期 <b style="color:#e08a00">${f.stale.length}</b> 篇
       <div style="margin-top:8px">${Object.entries(f.by_category).map(([k, v]) => `<span class="tag">${esc(lb(k))} × ${v}</span>`).join('')}</div>
       ${(f.stale || []).length ? `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:#e08a00">过期文档清单（${f.stale.length}）</summary>
         <ul style="margin:6px 0 0 18px;font-size:12px;color:#77809a">${f.stale.map(d =>
    `<li>${esc(d.title)} <span class="tag orange">已 ${d.age_days} 天未核实</span></li>`).join('')}</ul></details>` : ''}`;
    const pending = (p.patches || []).find(x => x.status === 'pending');
    renderPatch(pending);
    document.getElementById('kb-history').innerHTML = '<h4 style="margin:6px 0 8px">历史补丁</h4>' +
      (p.patches.length ? p.patches.map(h => `<div class="finding" style="background:#f6f8fc">
        <span class="tag ${h.status === 'approved' ? 'green' : h.status === 'rejected' ? 'red' : 'orange'}">${esc(STATUS_CN[h.status] || h.status)}</span>
        ${esc(h.rationale.slice(0, 80))} · ${h.items.length} 项 · ${fmtTime(h.created_at)}</div>`).join('') : '<span style="color:#77809a;font-size:12px">暂无</span>');
  } catch (e) { const el = document.getElementById('kb-fresh'); if (el) el.innerHTML = errBox(e); }
}

async function runCurate() {
  const box = document.getElementById('kb-patch');
  box.innerHTML = '知识库策展扫描中…';
  try {
    const r = await API.kbCurate();
    renderPatch({ ...r, status: 'pending', created_at: '' });
    toast('策展补丁已生成，等待人审', 'ok');
  } catch (e) { box.innerHTML = errBox(e); }
}

/* 待审补丁：只渲染 status=pending 的补丁；支持逐项勾选审批 */
function renderPatch(p) {
  const box = document.getElementById('kb-patch');
  if (!box) return;
  if (!p || !p.items || !p.items.length) {
    box.innerHTML = '<span style="color:#77809a;font-size:12px">暂无待审补丁（先点「运行知识库策展」）</span>';
    return;
  }
  box.innerHTML = `<div style="margin:6px 0 8px"><b>待审补丁</b> <span class="tag orange">${esc(STATUS_CN[p.status] || p.status)}</span></div>
    <div style="font-size:12px;color:#55607a;margin-bottom:8px">${esc(p.rationale)}</div>
    ${p.items.map((it, i) => `<div class="finding" style="background:${it.action === 'retire' ? '#fff3e0' : '#e3f8f2'}">
      <label style="cursor:pointer"><input type="checkbox" class="kb-item-cb" data-idx="${i}" checked>
      <span class="tag ${it.action === 'retire' ? 'orange' : 'green'}">${it.action === 'retire' ? '退役' : '入库'}</span>
      <b>${esc(it.title || it.replaces || '')}</b>${it.source ? ` · ${esc(it.source)}` : ''}</label>
      <div style="font-size:12px;color:#77809a;margin-top:3px">${esc(it.reason)}</div>
    </div>`).join('')}
    <div class="toolbar" style="margin-top:6px">
      <button class="btn" id="kb-approve">✅ 通过勾选项</button>
      <button class="btn ghost" id="kb-approve-all">✅ 全部通过（${p.items.length} 项）</button>
      <button class="btn ghost" id="kb-reject">❌ 拒绝整单</button>
    </div>`;
  const checkedIdx = () => [...box.querySelectorAll('.kb-item-cb:checked')].map(cb => Number(cb.dataset.idx));
  const doApprove = async (idx) => {
    try {
      const r = await API.kbApprove(p.patch_id, idx);
      if (r.ok) {
        toast(idx ? `已入库：新增 ${r.added} 篇、退役 ${r.retired} 篇（剩余条目继续待审）` : `已入库：新增 ${r.added} 篇、退役 ${r.retired} 篇`, 'ok', 6000);
        loadGovernance();
      } else toast(esc(r.error || '失败'), 'err');
    } catch (e) { toast(`操作失败：${esc(e.message)}`, 'err'); }
  };
  document.getElementById('kb-approve').onclick = () => {
    const idx = checkedIdx();
    if (!idx.length) { toast('请先勾选要通过的条目', 'err'); return; }
    doApprove(idx);
  };
  document.getElementById('kb-approve-all').onclick = () => doApprove(null);
  document.getElementById('kb-reject').onclick = async () => {
    try {
      const r = await API.kbReject(p.patch_id);
      if (r.ok) { toast('补丁已拒绝，知识库未改动', 'ok'); loadGovernance(); }
      else toast(esc(r.error || '失败'), 'err');
    } catch (e) { toast(`操作失败：${esc(e.message)}`, 'err'); }
  };
}

/* ---------- 分析中心（M2）：SQL 驱动指标看板 ----------
   每个图表由后端手写 SQL 实时计算，前端展示真实 SQL 原文（可展开），
   消费类图表统一标注「仿真」角标。纯 SVG 渲染，无第三方图表库。
   v2.1：零值空态提示 / 市场筛选 / CSV 导出 / 生成时间标注 / 长标签折行。 */
let ANALYTICS_RAW = null;
const ANALYTICS_MKT_FILTER = { market: '' };

async function analyticsView() {
  root.innerHTML = '<div class="loading">加载分析中心…</div>';
  try {
    ANALYTICS_RAW = await API.analyticsCenter();
    const mkts = await API.markets().catch(() => ({ markets: [] }));
    const mkOpts = mkts.markets.map(m => `<option value="${m.code}" ${m.code === ANALYTICS_MKT_FILTER.market ? 'selected' : ''}>${m.code}</option>`).join('');
    root.innerHTML = `
      <h1 class="page-title">分析中心</h1>
      <p class="page-sub">全部指标由后端手写 SQL 实时计算（非 ORM）。供给 / 成本 / 质量类来自真实运行数据，消费类来自仿真器（已标注「仿真」）。每张图下方可展开驱动它的真实 SQL。</p>
      <div class="toolbar" style="margin-bottom:14px">
        <span class="tag gray">生成于 ${fmtTime(ANALYTICS_RAW.generated_at)}</span>
        <label style="font-size:12px;color:#55607a">市场筛选：</label>
        <select id="an-mk"><option value="">全部</option>${mkOpts}</select>
        <span class="tag gray" id="an-count"></span>
        <button class="btn ghost" id="btn-refresh-analytics">⟳ 刷新</button>
      </div>
      <p class="muted" style="font-size:12px;margin-bottom:10px">市场筛选作用于含市场维度的图表（QSR / 成本 / 五维评分 / 形态×市场 / 衰减曲线）；全局口径图表（漏斗 / FPY / 降级率 / 时长）保持不变。</p>
      <div id="charts-root"></div>`;
    document.getElementById('an-mk').onchange = (e) => { ANALYTICS_MKT_FILTER.market = e.target.value; renderAnalytics(); };
    document.getElementById('btn-refresh-analytics').onclick = analyticsView;
    renderAnalytics();
  } catch (e) { root.innerHTML = errBox(e); }
}

/* 按市场过滤图表（仅作用于含市场维度的图） */
function filterChart(c, mkt) {
  if (!mkt) return c;
  const copy = { ...c };
  if (c.id === 'qsr' || c.id === 'cost') {
    copy.rows = c.rows.filter(r => r[0] === mkt);
    if (copy.headline) copy.headline = { ...copy.headline, sub: `${copy.headline.sub || ''}（表：仅 ${mkt}，大数为全局口径）` };
  } else if (c.id === 'rubric') {
    const idx = (c.headline?.labels || []).indexOf(mkt);
    if (idx >= 0) {
      copy.headline = {
        labels: [mkt],
        series: (c.headline.series || []).map(s => ({ name: s.name, data: [s.data[idx]] })),
      };
      copy.chart = 'grouped_bar';
    }
  } else if (c.id === 'format_market') {
    const colIdx = (c.columns || []).indexOf(mkt);
    if (colIdx > 0) {
      copy.rows = (c.rows || []).map(r => [r[0], r[colIdx]]);
      copy.columns = ['形态', `CTR(${mkt})`];
      copy.chart = 'bar';
    }
  } else if (c.id === 'decay') {
    copy.headline = {
      labels: c.headline?.labels || [],
      series: (c.headline?.series || []).filter(s => s.name === mkt),
    };
  }
  return copy;
}

/* 九张图按「要回答什么问题」分组，而不是按 SQL 顺序从上往下翻 */
const CHART_GROUPS = [
  { name: '一、供给效率', desc: '系统能不能稳定地产出可用内容', sim: false, ids: ['qsr', 'fpy'] },
  { name: '二、内容质量', desc: '产出的东西够不够好，弱在哪一维', sim: false, ids: ['rubric'] },
  { name: '三、成本与稳定性', desc: '每条内容花多少钱，哪个环节在降级', sim: false, ids: ['cost', 'agent_degrade'] },
  { name: '四、消费表现', desc: '全部为仿真口径 —— 真实分发光标属平台私有数据，无法获取', sim: true, ids: ['funnel', 'decay', 'format_market', 'read_duration'] },
];

function renderAnalytics() {
  const mkt = ANALYTICS_MKT_FILTER.market;
  const charts = (ANALYTICS_RAW.charts || []).map(c => filterChart(c, mkt));
  const count = document.getElementById('an-count');
  if (count) count.textContent = mkt ? `${charts.length} 图 · 已按 ${mkt} 过滤` : `共 ${charts.length} 图`;
  const grid = document.getElementById('charts-root');
  if (!grid) return;
  const byId = Object.fromEntries(charts.map(c => [c.id, c]));
  let html = '';
  for (const g of CHART_GROUPS) {
    const list = g.ids.map(id => byId[id]).filter(Boolean);
    if (!list.length) continue;
    html += `<div class="chart-group"><h2>${esc(g.name)}</h2><span class="grp-desc">${esc(g.desc)}</span>
      ${g.sim ? '<span class="tag orange">仿真</span>' : '<span class="tag green">真实</span>'}</div>
      <div class="charts-grid">${list.map(chartCard).join('')}</div>`;
  }
  // 兜底：后端新增但未归类的图，仍然展示，不会丢
  const grouped = new Set(CHART_GROUPS.flatMap(g => g.ids));
  const rest = charts.filter(c => !grouped.has(c.id));
  if (rest.length) html += `<div class="chart-group"><h2>其他</h2></div>
    <div class="charts-grid">${rest.map(chartCard).join('')}</div>`;
  grid.innerHTML = html;
  grid.querySelectorAll('[data-csv]').forEach(b => b.onclick = () => exportCsv(b.dataset.csv));
}

function exportCsv(chartId) {
  const c = (ANALYTICS_RAW.charts || []).find(x => x.id === chartId);
  if (!c) return;
  const rows = [c.columns || [], ...(c.rows || [])];
  const csv = rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `trendforge_${chartId}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function chartCard(c) {
  const badge = c.reality === 'simulated'
    ? '<span class="tag orange">仿真</span>'
    : '<span class="tag green">真实</span>';
  const head = (c.headline && c.headline.kind) ? statHead(c.headline) : '';
  return `<div class="chart-card">
    <div class="chart-head"><h3>${esc(displayTitle(c))}</h3>${badge}</div>
    ${head}
    <div class="chart-body">${renderChart(c)}</div>
    <p class="chart-note">${esc(c.note || '')}</p>
    <div class="toolbar" style="margin-top:4px">
      <details class="sql-reveal"><summary>技术细节 · 驱动此图的 SQL ▸</summary><pre>${esc(c.sql)}</pre></details>
      <button class="btn ghost" data-csv="${esc(c.id)}" style="margin-left:auto">导出 CSV</button>
    </div>
  </div>`;
}
/* 图标题去掉与徽标重复的（真实）/（仿真）后缀 */
function displayTitle(c) { return (c.title || '').replace(/（真实( · [^）]*)?）|（仿真( · [^）]*)?）/g, '').trim(); }

function statHead(h) {
  if (h.kind === 'cost')
    return `<div class="stat-big">${esc(h.value)}<span class="stat-suffix">${esc(h.suffix || '')}</span></div><div class="stat-sub">${esc(h.sub || '')}</div>`;
  if (h.kind === 'rate')
    return `<div class="stat-big">${(Number(h.value) * 100).toFixed(1)}%</div><div class="stat-sub">${esc(h.sub || '')}</div>`;
  if (h.kind === 'stat')
    return `<div class="stat-big">${esc(h.value)}<span class="stat-suffix">${esc(h.suffix || '')}</span></div><div class="stat-sub">${esc(h.sub || '')}</div>`;
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

/* 横轴标签：>10 字符折两行（分析中心 Agent 名等长标签不再糊成一团） */
function svgLabel(text, x, y) {
  const s = String(text);
  if (s.length <= 10) {
    return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="10" fill="#778"><title>${esc(s)}</title>${esc(s)}</text>`;
  }
  const mid = Math.ceil(s.length / 2);
  return `<text x="${x.toFixed(1)}" y="${(y - 2).toFixed(1)}" text-anchor="middle" font-size="9" fill="#778"><title>${esc(s)}</title><tspan x="${x.toFixed(1)}" dy="0">${esc(s.slice(0, mid))}</tspan><tspan x="${x.toFixed(1)}" dy="10">${esc(s.slice(mid))}</tspan></text>`;
}

function svgBar(c) {
  // 行标签是数据库枚举（形态 / Agent 名…）：统一过标签层，未知 key 原样保留
  const labels = c.rows.map(r => lbEnum(r[0]));
  const vals = c.rows.map(r => Number(r[r.length - 1]) || 0);
  if (!vals.length) return '<div class="muted">暂无数据</div>';
  const max = Math.max(1e-9, ...vals);
  if (max <= 1e-9) {
    return `<div class="chart-zero">所有取值均为 0——这不是没有数据，而是指标当前确实为零（见下方注释的原因说明）。</div>`;
  }
  const W = 600, H = 220, padL = 46, padB = 42, padT = 12, padR = 12;
  const n = labels.length, gap = (W - padL - padR) / n, bw = gap * 0.6;
  let s = '';
  vals.forEach((v, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const h = (v / max) * (H - padT - padB);
    const y = H - padB - h;
    s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="3" fill="#4c8bf5"/>`;
    if (h > 14) s += `<text x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" font-size="11" fill="#334">${fmtNum(v)}</text>`;
    s += svgLabel(labels[i].length > 22 ? labels[i].slice(0, 22) + '…' : labels[i], x + bw / 2, H - padB + 14);
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
    s += `<text x="${padL - 8}" y="${(y + bh / 2 + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="#334">${esc(stageLabel(r[0]))}</text>`;
    s += `<text x="${(padL + w + 8).toFixed(1)}" y="${(y + bh / 2 + 4).toFixed(1)}" font-size="11" fill="#778">${fmtNum(v)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg>`;
}

const CHART_COLORS = ['#4c8bf5', '#f5a623', '#2bb673', '#e056a0', '#7c5cff', '#19b3c4'];

function svgLine(c) {
  const labels = c.headline?.labels || [];
  const series = c.headline?.series || [];
  if (!series.length) return '<div class="muted">暂无数据</div>';
  const all = series.flatMap(s => s.data);
  const max = Math.max(1e-9, ...all);
  if (max <= 1e-9) return '<div class="chart-zero">所有取值均为 0（仿真事件未覆盖该维度，先「模拟消费事件」）。</div>';
  const W = 600, H = 240, padL = 46, padB = 28, padT = 12, padR = 12;
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
      s += `<text x="${(padL + i * xstep).toFixed(1)}" y="${H - padB + 13}" text-anchor="middle" font-size="9" fill="#778">${esc(stageLabel(lb))}</text>`;
  });
  // 图例：五维评分这类内部维度（小写枚举）翻译；市场代码（US/JP…）保持原样
  let leg = series.map((ser, si) => `<span class="lg"><i style="background:${CHART_COLORS[si % CHART_COLORS.length]}"></i>${esc(lbEnum(ser.name))}</span>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg><div class="legend">${leg}</div>`;
}

function svgGroupedBar(c) {
  const labels = c.headline?.labels || [];
  const series = c.headline?.series || [];
  if (!series.length) return '<div class="muted">暂无数据</div>';
  const all = series.flatMap(s => s.data);
  const max = Math.max(1e-9, ...all);
  if (max <= 1e-9) return '<div class="chart-zero">所有取值均为 0。</div>';
  const W = 600, H = 240, padL = 40, padB = 42, padT = 12, padR = 12;
  const n = labels.length, groupW = (W - padL - padR) / n, bw = groupW * 0.72 / series.length;
  let s = '';
  labels.forEach((lbi, i) => {
    const gx = padL + i * groupW + groupW * 0.14;
    series.forEach((ser, si) => {
      const v = ser.data[i] || 0, h = (v / max) * (H - padT - padB), x = gx + si * bw, y = H - padB - h;
      s += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="2" fill="${CHART_COLORS[si % CHART_COLORS.length]}"/>`;
    });
    s += `<text x="${(gx + groupW * 0.36).toFixed(1)}" y="${H - padB + 14}" text-anchor="middle" font-size="10" fill="#778">${esc(lbEnum(lbi))}</text>`;
  });
  let leg = series.map((ser, si) => `<span class="lg"><i style="background:${CHART_COLORS[si % CHART_COLORS.length]}"></i>${esc(lbEnum(ser.name))}</span>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${s}</svg><div class="legend">${leg}</div>`;
}

function svgHeat(c) {
  const cols = c.columns || [], rows = c.rows || [];
  if (!rows.length) return '<div class="muted">暂无数据</div>';
  const all = rows.flatMap(r => r.slice(1).map(Number));
  const max = Math.max(1e-9, ...all);
  let h = `<table class="heat-table"><thead><tr>${cols.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>`;
  rows.forEach(r => {
    h += `<tr><td class="hm-label">${esc(lbEnum(r[0]))}</td>`;
    r.slice(1).forEach(v => {
      const t = Number(v) || 0, inten = Math.min(1, t / max), bg = `rgba(76,139,245,${(0.1 + inten * 0.72).toFixed(3)})`;
      h += `<td style="background:${bg}">${fmtNum(t)}</td>`;
    });
    h += '</tr>';
  });
  return h + '</tbody></table>';
}

/* ---------- 工具 ---------- */
function errBox(e) { return `<div class="panel" style="color:#d43d3d">加载失败：${esc(e.message)}<br><small>后端可能冷启动中（Render 免费层休眠约 30-60s），请稍候刷新</small></div>`; }

/* ---------- 系统进化（M3，原「迭代闭环」） ----------
   相比旧版的三处改动：
   1. 说人话：模板/版本/来源全部走中文标签，不再满屏内部代号 / v1 / 版本差异；
   2. 补上闭环第四段「效果回收」——采纳之后到底变好了没有，用运行时数据回答
      （闭环第一版缺的就是这一段，点了采纳"没反应"的体感正来源于此）；
   3. A/B 不再用仿真的 CTR 替用户判胜负（CTR 由质量分派生 = 循环论证），
      改为给出真实差异 + 让人二选一，选完直接生效，闭环真正闭合。 */
const CL_F = { sugStatus: 'pending' };

async function closedLoopView() {
  root.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [tpls, mkts] = await Promise.all([
      API.promptTemplates(), API.markets().catch(() => ({ markets: [] })),
    ]);
    const mkOpts = mkts.markets.map(m => `<option value="${esc(m.code)}">${esc(m.name)} (${esc(m.code)})</option>`).join('');
    const tplOpts = tpls.templates.map(t => `<option value="${esc(t)}">${esc(lb(t))}</option>`).join('');
    root.innerHTML = `
      <h1 class="page-title">系统进化</h1>
      <p class="page-sub">这里决定 AI 下一轮怎么写。<b>所有改动都由你确认后才生效，随时可回滚。</b>
        链路：① AI 提议 → ② 你拍板采纳（即刻生效）→ ③ 效果回收对比是否有改善 → ④ 不行就回滚。</p>
      <div class="panel"><h3>① 迭代建议（AI 提议 · 人审闸门）</h3>
        <p class="muted" style="font-size:12px;margin-bottom:8px">反馈分析读消费数据后给出「改哪里 + 为什么改 + 改完长什么样」，
          它只提议，不会自己改系统——这也是全站唯一运行它的入口。</p>
        <div class="toolbar">
          <select id="cl-market">${mkOpts}</select>
          <button class="btn" id="cl-feedback">运行反馈分析</button>
          <select id="cl-sug-status">
            <option value="pending">待审</option>
            <option value="adopted">已采纳</option>
            <option value="rejected">已拒绝</option>
            <option value="all">全部</option>
          </select>
          <button class="btn ghost" id="cl-reload-sug">刷新</button>
        </div>
        <div id="cl-suggestions" class="loading">加载中…</div>
      </div>
      <div class="panel"><h3>② 提示词版本治理（采纳 / 回滚 / 版本对比）</h3>
        <p class="muted" style="font-size:12px;margin-bottom:8px">采纳走运行时覆盖层：<b>无需重启、无需重新部署</b>，下一次供给立刻用新版本。</p>
        <div class="toolbar">
          <select id="cl-tpl"></select>
          <button class="btn ghost" id="cl-reload-ver">刷新版本</button>
        </div>
        <div id="cl-versions" class="loading">选择模板查看版本</div>
        <pre id="cl-diff" class="diff" style="display:none"></pre>
      </div>
      <div class="panel"><h3>③ 采纳效果回收（采纳之后，到底变好了没有）</h3>
        <p class="muted" style="font-size:12px;margin-bottom:8px">
          口径：按运行记录里实际使用的提示词版本分组，对比「用旧版跑出的内容」与「用采纳版跑出的内容」的质量均分 / 单条成本 / 裁决分布。
          数据全部来自真实运行落库，样本数如实标注——样本很小时它是信号，不是结论。</p>
        <div id="cl-impact" class="loading">加载中…</div>
      </div>
      <div class="panel"><h3>④ A/B 对比（同一选题 · 两版提示词 · 你来拍板）</h3>
        <p class="muted" style="font-size:12px;margin-bottom:8px">⚠ A/B 会用两版提示词各完整跑一次写作链路（真实 LLM 额度，真机约数分钟）。
          <b>任务在后台运行</b>：提交后即可切走，回来还能看到进度与结果。
          <b>系统不替你判胜负</b>：仿真的 CTR 由质量分派生再用它反证质量属于循环论证，因此 CTR 只作灰色参考。
          请以质量分与成本为准，看完直接点「选用这版」。</p>
        <div class="toolbar">
          <select id="cl-ab-tpl"></select>
          <select id="cl-ab-v1"><option value="">旧版</option></select>
          <span style="align-self:center">对比</span>
          <select id="cl-ab-v2"><option value="">新版</option></select>
          <input id="cl-ab-angle" placeholder="选题 / 角度（如：AI 监管）" style="width:200px">
          <select id="cl-ab-market">${mkOpts}</select>
          <button class="btn" id="cl-ab-run">运行 A/B</button>
        </div>
        <div id="cl-ab-result">选择两版提示词并输入选题后运行</div>
      </div>`;
    document.getElementById('cl-tpl').innerHTML = tplOpts;
    document.getElementById('cl-ab-tpl').innerHTML = tplOpts;
    document.getElementById('cl-sug-status').value = CL_F.sugStatus;
    document.getElementById('cl-feedback').onclick = runFeedback;
    document.getElementById('cl-reload-sug').onclick = loadSuggestions;
    document.getElementById('cl-sug-status').onchange = (e) => { CL_F.sugStatus = e.target.value; loadSuggestions(); };
    document.getElementById('cl-reload-ver').onclick = () => loadVersions(document.getElementById('cl-tpl').value);
    document.getElementById('cl-tpl').onchange = (e) => loadVersions(e.target.value);
    document.getElementById('cl-ab-tpl').onchange = (e) => loadAbVersions(e.target.value);
    document.getElementById('cl-ab-run').onclick = runAB;
    loadSuggestions();
    loadVersions(tpls.templates[0]);
    loadAdoptionImpact();
    loadAbVersions(tpls.templates[0]);   // A/B 下拉必须初始就填好，否则进来是空的、无从选起
    paintAbPanel();                      // 刷新/切回来时，还原正在跑的 A/B 进度或结果
  } catch (e) { root.innerHTML = errBox(e); }
}

/* 闭环第四段：采纳效果回收。全部数据来自真实运行记录，样本量小的时候如实降调。 */
async function loadAdoptionImpact() {
  const box = document.getElementById('cl-impact');
  try {
    const r = await API.promptAdoptionImpact();
    const items = r.adoptions || [];
    if (!items.length) {
      box.innerHTML = `<div class="chart-zero">还没有任何 AI 提议被采纳。<br>
        先在 ① 里运行反馈分析并采纳一条建议，之后这里会自动给出「采纳前与采纳后」的对比。</div>`;
      return;
    }
    const fmt = (v, suffix = '') => (v === null || v === undefined) ? '<span class="muted">—</span>' : `<b>${v}${suffix}</b>`;
    const row = (name, before, after, suffix = '') => `<div class="impact-row">
        <span>${name}</span><span>${fmt(before, suffix)} <span class="muted">→</span> ${fmt(after, suffix)}</span></div>`;
    const verdictRow = (b, a) => `<div class="impact-row"><span>裁决分布</span><span>
        <span class="tag green">可发布 ${b.pass}→${a.pass}</span>
        <span class="tag orange">需修改 ${b.revise}→${a.revise}</span>
        ${(b.reject || a.reject) ? `<span class="tag red">不通过 ${b.reject}→${a.reject}</span>` : ''}</span></div>`;
    box.innerHTML = `<div class="impact-grid">${items.map(x => {
      const { before: b, after: a, delta: d } = x;
      const thin = (b.n + a.n) < 4;
      let cls = 'impact-flat', txt = '基本持平';
      if (d.quality_avg != null) {
        if (d.quality_avg >= 0.2) { cls = 'impact-up'; txt = '质量提升'; }
        else if (d.quality_avg <= -0.2) { cls = 'impact-down'; txt = '质量下滑'; }
      }
      return `<div class="impact-card">
        <h4>${esc(lb(x.template))} · 第 ${esc(String(x.version).replace(/^v/, ''))} 版
          <span class="tag ${x.source === 'ai_suggested' ? 'blue' : 'gray'}">${esc(sourceLabel(x.source))}</span></h4>
        ${row('内容数', b.n, a.n, ' 条')}
        ${row('质量均分', b.quality_avg, a.quality_avg, '/5')}
        ${row('单条成本', b.cost_avg, a.cost_avg, ' 元')}
        ${row('平均耗时', b.duration_avg != null ? Math.round(b.duration_avg / 1000) : null,
        a.duration_avg != null ? Math.round(a.duration_avg / 1000) : null, 's')}
        ${verdictRow(b, a)}
        <div class="impact-delta">结论：<b class="${cls}">${d.quality_avg == null ? '数据不足，暂无法比较' : `${txt} ${d.quality_avg >= 0 ? '+' : ''}${d.quality_avg} 分`}</b>
          ${b.n + a.n ? `<span class="muted">（样本 ${b.n} + ${a.n} 条）</span>` : ''}</div>
        ${thin ? '<div style="margin-top:6px;font-size:12px;color:#e08a00">⚠ 样本量很小，这是信号不是结论——继续跑数据再看。</div>' : ''}
      </div>`;
    }).join('')}</div>
    <p style="margin-top:10px;font-size:12px;color:#55607a">没有改善怎么办？去 ② 里回滚到上一版，或直接采纳另一条建议再测——每一步都留痕、可逆。</p>`;
  } catch (e) { box.innerHTML = errBox(e); }
}

async function runFeedback() {
  const box = document.getElementById('cl-suggestions');
  box.innerHTML = '反馈分析中…（消费数据 → 结构化建议，可能 10-30s）';
  try {
    const market = document.getElementById('cl-market').value || 'US';
    const r = await API.promptFeedback(market);
    toast(`已生成 ${(r.suggestion_ids || []).length} 条可采纳迭代建议`, 'ok');
    loadSuggestions();
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadSuggestions() {
  const box = document.getElementById('cl-suggestions');
  try {
    const r = await API.promptSuggestions(CL_F.sugStatus);
    const sugs = r.suggestions || [];
    if (!sugs.length) { box.innerHTML = `<span style="color:#77809a;font-size:12px">暂无${{ pending: '待审', adopted: '已采纳', rejected: '已拒绝' }[CL_F.sugStatus] || ''}建议（可点「运行反馈分析」生成，或切换状态查看历史）</span>`; return; }
    box.innerHTML = sugs.map(s => `
      <div class="finding" style="background:#eef4ff;border-left:3px solid #3a6df0">
        <span class="tag blue">建议改：${esc(lb(s.target_template))}</span>
        <span class="tag gray">${esc(s.section || '—')}</span>
        <span class="tag green">预期改善 ${esc(s.expected_metric || '—')}</span>
        <span class="tag ${s.status === 'pending' ? 'orange' : s.status === 'adopted' ? 'green' : 'red'}">${esc(STATUS_CN[s.status] || s.status)}</span>
        <span class="tag gray">${esc(s.market || '')} · ${fmtTime(s.created_at)}</span>
        <div style="margin:6px 0 4px"><b>改法：</b>${esc(s.proposed_change)}</div>
        <div style="font-size:12px;color:#55607a">理由：${esc(s.rationale)}</div>
        <details style="margin-top:6px"><summary style="cursor:pointer;color:#3a6df0;font-size:12px">查看 AI 提议的完整新版提示词</summary>
          <pre class="diff" style="max-height:200px;overflow:auto">${esc(s.new_prompt)}</pre></details>
        ${s.status === 'pending' ? `<div class="toolbar" style="margin-top:6px">
          <button class="btn" data-adopt="${s.id}">✅ 采纳（生成新版本并即刻生效）</button>
          <button class="btn ghost" data-reject="${s.id}">❌ 拒绝</button>
        </div>` : ''}
      </div>`).join('');
    box.querySelectorAll('[data-adopt]').forEach(b => b.onclick = async () => {
      try {
        const res = await API.promptSuggestionAdopt(b.dataset.adopt);
        if (res.ok) {
          toast(`已采纳 → 「${esc(lb(res.name))}」第 ${esc(String(res.version).replace(/^v/, ''))} 版已生效，下一次供给立即使用。
            效果对比见下方「采纳效果回收」。`, 'ok', 7000);
          loadSuggestions(); loadVersions(document.getElementById('cl-tpl').value); loadAdoptionImpact();
        } else toast(esc(res.error || '采纳失败'), 'err');
      } catch (e) { toast(`采纳失败：${esc(e.message)}`, 'err'); }
    });
    box.querySelectorAll('[data-reject]').forEach(b => b.onclick = async () => {
      try {
        const res = await API.promptSuggestionReject(b.dataset.reject);
        if (res.ok) loadSuggestions(); else toast(esc(res.error || '拒绝失败'), 'err');
      } catch (e) { toast(`拒绝失败：${esc(e.message)}`, 'err'); }
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
        <span class="tag ${v.adopted ? 'green' : 'gray'}">第 ${esc(String(v.version).replace(/^v/, ''))} 版</span>
        <span class="tag">${esc(sourceLabel(v.source))}</span>
        ${v.adopted ? '<span class="tag green">● 生效中</span>' : ''}
        ${v.parent_version ? `<span class="tag gray">← 演化自第 ${esc(String(v.parent_version).replace(/^v/, ''))} 版</span>` : ''}
        <span style="font-size:12px;color:#77809a">${fmtTime(v.created_at)}</span>
        <div class="toolbar" style="margin-top:6px">
          ${v.adopted ? '<span class="tag green">当前生效</span>'
      : (vs.length > 1 ? `<button class="btn" data-adopt-v="${v.id}">采纳 / 回滚至此</button>` : '')}
          ${adopted && !v.adopted ? `<button class="btn ghost" data-diff="${v.id}" data-adopted="${adopted.id}">对比生效版</button>` : ''}
        </div>
      </div>`).join('');
    box.querySelectorAll('[data-adopt-v]').forEach(b => b.onclick = async () => {
      try {
        const res = await API.promptVersionAdopt(b.dataset.adoptV);
        if (res.ok) {
          toast(`已采纳「${esc(lb(res.name))}」第 ${esc(String(res.version).replace(/^v/, ''))} 版，即刻生效`, 'ok');
          loadVersions(tpl); loadAdoptionImpact();
        } else toast(esc(res.error || '失败'), 'err');
      } catch (e) { toast(`操作失败：${esc(e.message)}`, 'err'); }
    });
    box.querySelectorAll('[data-diff]').forEach(b => b.onclick = async () => {
      try {
        const d = await API.promptVersionDiff(b.dataset.diff, b.dataset.adopted);
        const pre = document.getElementById('cl-diff');
        pre.style.display = 'block';
        pre.textContent = d.diff || '（无差异）';
        pre.scrollIntoView({ behavior: 'smooth' });
      } catch (e) { toast(`版本对比失败：${esc(e.message)}`, 'err'); }
    });
  } catch (e) { box.innerHTML = errBox(e); }
}

async function loadAbVersions(tpl) {
  const sel1 = document.getElementById('cl-ab-v1');
  const sel2 = document.getElementById('cl-ab-v2');
  const run = document.getElementById('cl-ab-run');
  try {
    const r = await API.promptVersions(tpl);
    const vs = r.versions || [];
    const opt = (v) => `<option value="${v.id}">${esc(lb(v.name))} · 第 ${esc(String(v.version).replace(/^v/, ''))} 版（${esc(sourceLabel(v.source))}${v.adopted ? ' · 当前生效' : ''}）</option>`;
    const opts = vs.map(opt).join('');
    sel1.innerHTML = '<option value="">旧版</option>' + opts;
    sel2.innerHTML = '<option value="">新版</option>' + opts;
    if (vs.length >= 2) {
      // 列表是新版在前：预置「最旧的做旧版、最新的做新版」，进来就能直接跑
      sel1.value = String(vs[vs.length - 1].id);
      sel2.value = String(vs[0].id);
      if (run) { run.disabled = false; run.title = ''; }
    } else {
      if (run) {
        run.disabled = true;
        run.title = '该模板目前只有 1 个提示词版本，无法对比';
      }
      toast(`${esc(lb(tpl))} 目前只有 ${vs.length} 个版本，A/B 需要两版。先在 ① 运行反馈分析并采纳一条建议（或手动新建版本）`, 'err', 6000);
    }
  } catch (e) {
    toast(`加载 A/B 版本失败：${esc(e.message)}`, 'err');
  }
}

/* A/B 面板：先渲染 unfinished 的 job（若有），否则回落空闲态文案 */
function paintAbPanel() {
  ABState.paint(true);
  if (!ABState.job) {
    const box = document.getElementById('cl-ab-result');
    if (box) box.innerHTML = '选择两版提示词并输入选题后运行';
  }
}

async function runAB() {
  const tpl = document.getElementById('cl-ab-tpl').value;
  const v1 = document.getElementById('cl-ab-v1').value;
  const v2 = document.getElementById('cl-ab-v2').value;
  const angle = document.getElementById('cl-ab-angle').value.trim();
  const market = document.getElementById('cl-ab-market').value || 'US';
  if (!v1 || !v2) { toast('请为参与对比的两版各选一个提示词版本', 'err'); return; }
  if (v1 === v2) { toast('两版选的是同一个版本，无法对比', 'err'); return; }
  if (!angle) { toast('请填写选题 / 角度', 'err'); return; }
  if (ABState.running()) { toast('已有 A/B 在跑，请等它出结果', 'err'); return; }
  if (!confirmCostly(`A/B 将用两版提示词各跑一次完整写作链路（市场 ${market}）。\n真机约数分钟，任务在后台运行，不用守着页面。`)) return;
  ABState.set({ status: 'starting', job_id: null, progress: '已排队', result: null, error: null,
    meta: { market, template: tpl, angle }, started_at: Date.now() });
  try {
    const r = await API.promptABRun({ market, template: tpl, v1_id: Number(v1), v2_id: Number(v2), angle });
    ABState.set({ status: 'running', job_id: r.job_id, progress: '已提交后台任务' });
    ABState.startPolling();
  } catch (e) {
    ABState.set({ status: 'failed', error: e.message });
  }
}

function renderABResult(r, meta) {
  const box = document.getElementById('cl-ab-result');
  if (!box) return;
  const v1m = r.v1, v2m = r.v2, d = r.delta;
  const deltaCls = (v) => v > 0 ? 'impact-up' : (v < 0 ? 'impact-down' : 'impact-flat');
  const sign = (v) => `${v >= 0 ? '+' : ''}${v}`;
  box.innerHTML = `
      <div class="impact-grid">
        <div class="impact-card">
          <h4>旧版 · 第 ${esc(String(v1m.version).replace(/^v/, ''))} 版</h4>
          <div class="impact-row"><span>裁决</span>${verdictTag(v1m.verdict)}</div>
          <div class="impact-row"><span>质量分</span><b>${v1m.quality_avg}</b></div>
          <div class="impact-row"><span>成本</span><b>¥${v1m.cost_cny}</b></div>
          <div class="impact-row"><span>CTR（仿真·仅参考）</span><span class="muted">${v1m.ctr}</span></div>
          <div style="margin-top:8px"><button class="btn" data-pick="${esc(String(v1m.id))}">选用这版并生效</button></div>
        </div>
        <div class="impact-card">
          <h4>新版 · 第 ${esc(String(v2m.version).replace(/^v/, ''))} 版</h4>
          <div class="impact-row"><span>裁决</span>${verdictTag(v2m.verdict)}</div>
          <div class="impact-row"><span>质量分</span><b>${v2m.quality_avg}</b></div>
          <div class="impact-row"><span>成本</span><b>¥${v2m.cost_cny}</b></div>
          <div class="impact-row"><span>CTR（仿真·仅参考）</span><span class="muted">${v2m.ctr}</span></div>
          <div style="margin-top:8px"><button class="btn" data-pick="${esc(String(v2m.id))}">选用这版并生效</button></div>
        </div>
        <div class="impact-card">
          <h4>差异（新版 − 旧版）</h4>
          <div class="impact-row"><span>质量分</span><b class="${deltaCls(d.quality_avg)}">${sign(d.quality_avg)}</b></div>
          <div class="impact-row"><span>成本</span><b class="${deltaCls(-d.cost_cny)}">${sign(d.cost_cny)} 元</b></div>
          <div class="impact-row"><span>CTR</span><span class="muted">${sign(d.ctr)}（不作判据）</span></div>
          <div class="impact-delta" style="font-size:12px;color:#55607a">系统不替你判胜负：N=1 的分差可能只是波动，
            建议结合完整原文与修改成本一起判断。</div>
        </div>
      </div>
      <p style="margin-top:10px;font-size:12px;color:#55607a">
        ${esc(r.note || 'CTR/曝光为仿真口径，仅作参考。')} ·
        原文：<a class="link" href="#content/${v1m.content_id}">旧版全文</a> ·
        <a class="link" href="#content/${v2m.content_id}">新版全文</a>
        ${meta && meta.angle ? ` · 选题「${esc(meta.angle)}」` : ''}
      </p>`;
  box.querySelectorAll('[data-pick]').forEach(b => b.onclick = async () => {
    try {
      const res = await API.promptVersionAdopt(Number(b.dataset.pick));
      if (res.ok) {
        toast(`已选用「${esc(lb(res.name))}」第 ${esc(String(res.version).replace(/^v/, ''))} 版，即刻生效`, 'ok', 6000);
        loadVersions(document.getElementById('cl-tpl').value);
        loadAdoptionImpact();
      } else toast(esc(res.error || '生效失败'), 'err');
    } catch (e) { toast(`操作失败：${esc(e.message)}`, 'err'); }
  });
}

/* ---------- 启动 ---------- */
(async () => {
  RunState.init();   // 先恢复未完成的供给任务（刷新浏览器也能续上轮询）
  ReviseState.init(); // 恢复进行中的内容重写状态
  ABState.init();     // 恢复进行中的 A/B 对比任务
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
   拉取待校准内容 → 真人打分（0.5 半分 + 理由）→ 提交后后端跑对齐计算 → 展示报告。
   v2.1：全文折叠滚动 + 跳转内容详情 + 非中文内容一键切中文对照。 */
const CAL_DIMS = [
  ['accuracy', '事实准确性'], ['angle', '角度新颖度'], ['readability', '可读性'],
  ['local_fit', '本地化契合'], ['engagement', '吸引力/传播潜力'],
];
let calScores = {};
let calTotal = 0;
let calSamples = [];

async function calibrateView() {
  root.innerHTML = '<div class="loading">加载待校准内容…</div>';
  try {
    const { samples } = await API.calibrationSamples();
    calSamples = samples || [];
    calScores = {};
    calTotal = calSamples.length;
    let html = `<div class="panel"><h2>🔬 人工校准 · LLM 评委对齐</h2>
      <p class="muted">逐条阅读内容全文，按五维直觉打分（1–5，支持 0.5 半分）。评委分对你不可见（避免锚定）。
      每篇五维都评完会自动标记为「已评」；最后点页面底部的「提交 N 篇已评」即可——后端将你的打分与机器评委分做 Spearman 对齐并生成报告。可只评几篇就提交。
      非中文内容可点「显示中文对照」切换阅读（需该内容已生成中文镜像）。</p>
      <div style="margin:10px 0 4px">评审人：
        <input list="cal-rater-list" id="cal-rater" class="input" value="Strange" style="width:200px" placeholder="输入或选择评审人">
        <datalist id="cal-rater-list"><option value="Strange"></datalist>
        <span class="muted" style="font-size:12px">（同名续打会覆盖其旧分；不同评审人之间累积取平均）</span>
      </div>
      <div id="cal-progress" class="muted"></div></div>`;
    calSamples.forEach((s, i) => {
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
      const zhToggle = s.needs_zh && s.zh_excerpt
        ? `<button class="btn ghost" data-calzh="${esc(s.id)}" style="padding:2px 10px;font-size:12px">显示中文对照</button>` : '';
      html += `<div class="card" id="calcard-${esc(s.id)}">
        <h3><span class="badge">${esc(s.market)}</span><span id="calmeta-${esc(s.id)}">${prior}${avgTxt}</span> ${i + 1}. ${esc(s.title)}</h3>
        <div class="cal-excerpt" id="caltxt-${esc(s.id)}">${esc(s.excerpt)}</div>
        <div class="toolbar" style="margin:4px 0 8px">
          ${zhToggle}
          <a class="link" href="#content/${esc(s.id)}" style="font-size:12px">打开全文与执行轨迹 ↗</a>
        </div>
        ${dims}
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
    root.querySelectorAll('[data-calzh]').forEach(b => {
      b.onclick = () => {
        const s = calSamples.find(x => x.id === b.dataset.calzh);
        const txt = document.getElementById('caltxt-' + s.id);
        const showZh = txt.dataset.mode !== 'zh';
        txt.dataset.mode = showZh ? 'zh' : 'src';
        txt.textContent = showZh ? (s.zh_excerpt || '（暂无中文对照）') : s.excerpt;
        b.textContent = showZh ? '显示原文' : '显示中文对照';
      };
    });
    calProgress();
    // 直接把已有报告铺开：面试官不会现场给 13 条内容逐维打分，
    // 这份由真人打分算出的对齐报告本身就是资产，不该藏在按钮后面。
    calShowReport();
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
  if (!window.confirm(`将以「${rater}」提交 ${Object.keys(payload).length} 篇打分（同名续打会覆盖其旧分），确认？`)) return;
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
/* 极简 markdown → HTML（标题/表格/列表/引用/粗体/分段）；表格单元格内也处理 **粗体** */
function mdReport(md) {
  const lines = (md || '').split('\n');
  let html = '', tableBuf = [];
  const flushTable = () => {
    if (!tableBuf.length) return;
    html += '<table class="grid">' + tableBuf.map((r, ri) =>
      '<tr>' + r.map(c => ri === 0 ? `<th>${escMd(c)}</th>` : `<td>${escMd(c)}</td>`).join('') + '</tr>').join('') + '</table>';
    tableBuf = [];
  };
  for (const line of lines) {
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1).map(c => c.trim());
      if (cells.every(c => /^-+$/.test(c))) { continue; }
      tableBuf.push(cells); continue;
    }
    flushTable();
    if (line.startsWith('### ')) html += `<h4>${esc(line.slice(4))}</h4>`;
    else if (line.startsWith('## ')) html += `<h3>${esc(line.slice(3))}</h3>`;
    else if (line.startsWith('# ')) html += `<h2>${esc(line.slice(2))}</h2>`;
    else if (line.startsWith('> ')) html += `<blockquote>${escMd(line.slice(2))}</blockquote>`;
    else if (line.startsWith('- ')) html += `<li>${escMd(line.slice(2))}</li>`;
    else if (line.trim()) html += `<p>${escMd(line)}</p>`;
  }
  flushTable();
  return html;
}
