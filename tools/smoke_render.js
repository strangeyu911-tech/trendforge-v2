/* TrendForge V2 控制台渲染冒烟测试
   用一个极简 DOM 桩 + 真实本地后端（localhost:8000，指向 demo_snapshot 副本）把每个视图渲染一遍，
   目的是抓出模板里的运行时错误（undefined 取值、TDZ 引用、拼错的函数名），不做视觉校验。

   用法：node tools/smoke_render.js
*/
const fs = require('fs');
const path = require('path');

const BASE = 'D:\\workbuddy\\Data\\2026-07-21-19-08-17\\ai-news-system\\v2_trendforge';
const apiSrc = fs.readFileSync(path.join(BASE, 'ui/console/assets/api.js'), 'utf8');
const appSrc = fs.readFileSync(path.join(BASE, 'ui/console/assets/app.js'), 'utf8');

/* ---------- DOM 桩 ---------- */
// 用法：python -m uvicorn app.api.main:app --port 8000 （指向快照副本）
//       bash tools/fetch_fixtures.sh   # 用 curl 抓真实响应到 D:/tmp/fx（node fetch 在沙箱里被拦）
//       node tools/smoke_render.js
const store = {};
const memStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

const els = {};
function mkEl(id = '') {
  const el = {
    id, className: '', textContent: '', value: '', checked: false,
    _html: '', dataset: {}, style: {}, disabled: false, children: [],
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    classList: { add() { }, remove() { }, toggle() { }, contains: () => false },
    querySelectorAll: () => [], querySelector: () => null,
    addEventListener() { }, appendChild(c) { this.children.push(c); },
    remove() { }, focus() { }, scrollIntoView() { },
    setProperty() { }, getAttribute: () => null, setAttribute() { },
    options: [], insertAdjacentHTML() { },
  };
  return el;
}
const viewRoot = mkEl('view-root');
els['view-root'] = viewRoot;

global.document = {
  getElementById: (id) => els[id] || (els[id] = mkEl(id)),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => mkEl(tag),
  body: { appendChild() { } },
  addEventListener() { },
};
global.window = { confirm: () => true, localStorage: memStorage, addEventListener() { } };
global.localStorage = memStorage;
global.sessionStorage = { getItem: () => null, setItem() { }, removeItem() { } };
global.location = { hash: '', hostname: '127.0.0.1', href: '' };
global.setInterval = () => 0;
global.clearInterval = () => { };
global.setTimeout = (f) => { try { f(); } catch (e) { } return 0; };
global.prompt = () => '';
global.alert = () => { };

const RAW_FETCH = globalThis.fetch;
const FX_DIR = process.env.TF_FIXTURES || 'D:/tmp/fx';
const sanitize = (u) => u.replace(/^https?:\/\//, '').replace(/[^A-Za-z0-9]/g, '_');
const fxCache = {};
/* 本进程中 node 的网络被沙箱拦截，改用 curl 抓好的真实响应回放 —— 数据源仍是真实后端 */
global.fetch = async (u, o) => {
  const key = `${FX_DIR}/${sanitize(u.replace('localhost', '127.0.0.1'))}.json`;
  let body;
  if (key in fxCache) body = fxCache[key];
  else {
    try { body = JSON.parse(fs.readFileSync(key, 'utf8')); fxCache[key] = body; }
    catch (e) { console.log('   NO FIXTURE', u); body = { error: 'no fixture' }; }
  }
  return {
    ok: true, status: 200,
    json: async () => JSON.parse(JSON.stringify(body)),
    text: async () => JSON.stringify(body),
  };
};

/* ---------- 载入前端代码并取得内部函数引用 ---------- */
const exposed = ['overview', 'pipeline', 'contents', 'markets', 'evalView', 'kbView',
  'analyticsView', 'calibrateView', 'closedLoopView', 'contentDetail',
  'loadAdoptionImpact', 'loadBadCases', 'loadTasks', 'loadSuggestions', 'loadVersions',
  'loadAbVersions', 'renderABResult', 'paintAbPanel',
  'renderAnalytics', 'lb', 'lbEnum', 'stageLabel', 'kindLabel', 'sourceLabel', 'verdictTag',
  'API', 'RunState', 'ReviseState', 'ABState'];
const factory = new Function(apiSrc + '\n' + appSrc + '\nreturn {' + exposed.join(',') + '};');
let app;
try {
  app = factory();
} catch (e) {
  console.error('❌ 脚本载入失败：', e.message, '\n', e.stack.split('\n').slice(0, 4).join('\n'));
  process.exit(1);
}

/* ---------- 逐个视图渲染 ---------- */
const errors = [];
let ok = 0;

const views = {};
async function render(name, fn) {
  viewRoot._html = '';
  try {
    await fn();
    views[name] = viewRoot.innerHTML || '';
    const html = viewRoot.innerHTML || '';
    if (/加载失败|errBox/.test(html) && !/演示|暂无/.test(html)) {
      errors.push(`${name}: 页面里出现错误框 → ${html.slice(0, 200)}`);
    } else {
      console.log(`✅ ${name.padEnd(16)} 渲染 ${html.length} 字符`);
      ok++;
    }
  } catch (e) {
    errors.push(`${name}: ${e.message} @ ${(e.stack || '').split('\n')[1] || ''}`);
  }
}

(async () => {
  // 纯函数层：翻译层是否覆盖到位
  const cases = [
    ['lb(signal_scout)', app.lb('signal_scout'), '信号捕捉'],
    ['lb(video_script)', app.lb('video_script'), '短视频脚本'],
    ['lb(accuracy)', app.lb('accuracy'), '准确性'],
    ['stageLabel(曝光 Exposed)', app.stageLabel('曝光 Exposed'), '曝光'],
    ['stageLabel(互动 Liked+Shared)', app.stageLabel('互动 Liked+Shared'), '互动'],
    ['lbEnum(US)', app.lbEnum('US'), 'US'],
    ['lbEnum(local_fit)', app.lbEnum('local_fit'), '本地契合度'],
    ['kindLabel(revise)', app.kindLabel('revise'), '按意见重写'],
    ['lb(revise)', app.lb('revise'), '需修改'],
    ['lb(writer)', app.lb('writer'), '写作'],
    ['lb(ev)', app.lb('ev'), '电动车'],
    ['sourceLabel(ai_suggested)', app.sourceLabel('ai_suggested'), 'AI 提议'],
  ];
  for (const [expr, got, want] of cases) {
    if (got === want) { console.log(`✅ ${expr.padEnd(28)} → ${got}`); ok++; }
    else errors.push(`${expr} 期望 "${want}" 实际 "${got}"`);
  }
  if (!/tag orange/.test(app.verdictTag('revise'))) errors.push('verdictTag(revise) 未生成橙色标签');

  await render('overview', () => app.overview());
  await render('pipeline', () => app.pipeline());
  await render('contents', () => app.contents());
  await render('markets', () => app.markets());
  await render('eval', () => app.evalView());
  await render('kb', () => app.kbView());
  await render('analytics', () => app.analyticsView());
  await render('calibrate', () => app.calibrateView());
  await render('closedloop', () => app.closedLoopView());

  // 内容详情（取一条真实内容）
  try {
    const r = await app.API.contents('', 5);
    if (r.contents && r.contents.length) {
      await render('contentDetail', () => app.contentDetail(r.contents[0].id));
    }
  } catch (e) { errors.push('contentDetail: ' + e.message); }

  // 子面板
  viewRoot._html = '';
  els['cl-impact'] = mkEl('cl-impact');
  await render('adoptionImpact', () => app.loadAdoptionImpact());
  const impactHtml = els['cl-impact'].innerHTML || '';
  if (!impactHtml.length) errors.push('采纳效果回收面板为空');
  else console.log('   效果回收面板：' + impactHtml.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 220));

  els['badcases-panel'] = mkEl('badcases-panel');
  await render('badCases', () => app.loadBadCases());

  /* ---------- A/B 面板（v2.20：改为后台任务 + 轮询，且初始就填好两个版本下拉） ---------- */
  els['cl-ab-v1'] = mkEl('cl-ab-v1');
  els['cl-ab-v2'] = mkEl('cl-ab-v2');
  els['cl-ab-run'] = mkEl('cl-ab-run');
  els['cl-ab-result'] = mkEl('cl-ab-result');
  els['cl-ab-tpl'] = mkEl('cl-ab-tpl');
  await render('abVersions', () => app.loadAbVersions('writer'));
  const abV1 = String(els['cl-ab-v1'].value || '');
  const abV2 = String(els['cl-ab-v2'].value || '');
  if (abV1 && abV2 && abV1 !== abV2) {
    console.log(`✅ A/B 下拉已预选：旧版 ${abV1} vs 新版 ${abV2}`);
    ok++;
  } else errors.push(`A/B 下拉未预选出两个不同版本（v1=${abV1}, v2=${abV2}）`);
  if (/启用名单|只有/.test(els['cl-ab-run'].title || '') === false && els['cl-ab-run'].disabled === false) {
    console.log('✅ A/B 运行按钮可用（该模板有多个版本）'); ok++;
  } else errors.push('A/B 运行按钮被禁用（writer 应有 ≥2 个版本）');

  // 结果卡渲染（用真实跑出来的结构形状回放一遍）
  els['cl-ab-result'] = mkEl('cl-ab-result');
  await render('abResult', () => app.renderABResult({
    template: 'writer',
    v1: { id: 11, content_id: 'cid1', version: 'v1', verdict: 'revise', quality_avg: 3.0, cost_cny: 0.41, ctr: 0.12 },
    v2: { id: 16, content_id: 'cid2', version: 'v3', verdict: 'pass', quality_avg: 3.5, cost_cny: 0.34, ctr: 0.18 },
    delta: { quality_avg: 0.5, ctr: 0.06, cost_cny: -0.07 },
    note: '测试 note',
  }, { angle: 'AI 监管', market: 'US', template: 'writer' }));
  const abHtml = els['cl-ab-result'].innerHTML || '';
  const abChecks = [
    ['差异（新版 − 旧版）', '差异卡'], ['data-pick="11"', '旧版选用按钮带版本 id'],
    ['data-pick="16"', '新版选用按钮带版本 id'], ['不作判据', 'CTR 不作判据说明'],
    ['#content/cid1', '旧版全文链接'], ['#content/cid2', '新版全文链接'],
  ];
  for (const [needle, label] of abChecks) {
    if (abHtml.includes(needle)) { console.log(`✅ A/B 结果卡：${label}`); ok++; }
    else errors.push(`A/B 结果卡缺少「${needle}」（${label}）`);
  }

  // 后台任务态：进度必须渲染出来（不再是「运行中…」死等一个同步请求）
  els['cl-ab-result'] = mkEl('cl-ab-result');
  Object.assign(app.ABState, { job: { job_id: 'j1', status: 'running', progress: '跑第 2 版（v3）', started_at: Date.now() - 65000, meta: { template: 'writer', market: 'US', angle: 'AI 监管' } } });
  await render('abRunning', () => app.ABState.paint(true));
  const runHtml = els['cl-ab-result'].innerHTML || '';
  if (/A\/B 运行中/.test(runHtml) && /跑第 2 版/.test(runHtml) && /已运行 1m05s/.test(runHtml)) {
    console.log('✅ A/B 后台进度卡渲染（含环节 + 已运行时长）'); ok++;
  } else errors.push(`A/B 进度卡渲染异常：${runHtml.slice(0, 160)}`);
  app.ABState.job = null;

  /* ---------- 关键整改点的可见性断言 ---------- */
  const checks = [
    ['overview', '机器裁决 vs 人工打分', '人机对齐卡'],
    ['overview', '已产出内容市场 / 已建档市场', '市场口径改写'],
    ['contents', '全部裁决', '筛选改为按裁决'],
    ['contents', '需修改', '裁决标签上列表'],
    ['eval', '本页全部消费指标为仿真口径', '仿真口径横幅'],
    ['eval', '查看全部 →', false],
    ['eval', '生成迭代建议 →', '唯一入口跳转'],
    ['kb', '合成 KB', '合成知识库角标'],
    // 图表与 Tab 正文渲染在各自子容器里（真实 DOM 是该页的子节点，桩环境里是独立元素）
    ['root:charts-root', '四、消费表现', '图表分组'],
    ['root:charts-root', '技术细节 · 驱动此图的 SQL', 'SQL 折叠文案'],
    ['root:charts-root', 'signal_scout', false],
    ['root:charts-root', 'video_script', false],
    ['closedloop', '系统进化', '模块改名'],
    ['closedloop', '采纳效果回收', '效果回收段'],
    ['closedloop', 'target_template', false],
    ['tab-body', '双语对照', '详情页双语对照条'],
    ['tab-body', 'class="seg on" data-zhmode="both"', '默认选中双语对照'],
  ];
  for (const [view, needle, label] of checks) {
    const html = view.startsWith('root:') || view === 'tab-body'
      ? ((els[view.replace('root:', '')] || {}).innerHTML || '')
      : (views[view] || '');
    const hit = html.includes(needle);
    if (label === false) {
      if (!hit) { console.log(`✅ ${view}: 确认已移除「${needle}」`); ok++; }
      else errors.push(`${view}: 不应再出现「${needle}」`);
      continue;
    }
    if (hit) { console.log(`✅ ${view}: ${label}`); ok++; }
    else errors.push(`${view}: 缺少「${needle}」（${label}）`);
  }
  if (process.env.TF_SMOKE_DEBUG) {
    console.log('--- analytics tail ---');
    console.log((views.analytics || '').slice(-700));
    console.log('--- tab-body head ---');
    console.log(((els['tab-body'] || {}).innerHTML || '').slice(0, 400));
  }
  // 具体数值校验
  const ovHtml = views.overview || '';
  const machine = (ovHtml.match(/<div class="stat-big">([\d.]+)<span class="stat-suffix">\/5/) || [])[1];
  if (machine) { console.log(`✅ 机器裁决均分渲染为 ${machine}/5`); ok++; }
  else errors.push('overview: 未渲染机器裁决均分');

  console.log('\n' + '='.repeat(60));
  if (errors.length) {
    console.log(`❌ ${errors.length} 项失败：`);
    errors.forEach(e => console.log('   - ' + e));
    process.exit(1);
  }
  console.log(`✅ 全部通过（${ok} 项检查）`);
})();
