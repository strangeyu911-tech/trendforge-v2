/**
 * RunState 回归测试：验证「跑供给时切换视图，任务不中断且能恢复展示」。
 * 用 stub DOM 在 Node 中加载真实的 ui/console/assets/app.js。
 * 运行： node tools/test_runstate.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'ui', 'console', 'assets', 'app.js');

let failures = 0;
function ok(cond, label) {
  console.log(`${cond ? '  PASS' : '  FAIL'}  ${label}`);
  if (!cond) failures++;
}

class El {
  constructor(id = '', cls = '') {
    this.id = id; this.className = cls; this._html = ''; this.textContent = '';
    this.children = []; this.dataset = {}; this.disabled = false; this.options = [];
    this.value = ''; this.onclick = null;
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  remove() { }
  querySelector(sel) {
    if (sel === '.job-close') return this._html.includes('job-close') ? new El('', 'job-close') : null;
    if (sel === '.nav-badge') return this.children.find(c => (c.className || '').includes('nav-badge')) || null;
    return null;
  }
  classList = { toggle() { }, add() { }, remove() { } };
}

function makeEnv(store) {
  const els = {
    'view-root': new El('view-root'),
    'sys-status': new El('sys-status'),
    'job-box': new El('job-box'),
    'tasks-panel': new El('tasks-panel'),
    'run-btn': new El('run-btn'),
    'force-btn': new El('force-btn'),
    'mk': Object.assign(new El('mk'), { value: 'US', options: [{ value: 'US' }, { value: 'JP' }] }),
  };
  // 仅在「跑供给」视图挂载的元素
  const viewScoped = ['job-box', 'tasks-panel', 'run-btn', 'force-btn', 'mk'];
  const navLink = new El('', ''); navLink.dataset.view = 'pipeline';

  const state = { onPipelineView: true, timers: [] };

  const document = {
    getElementById(id) {
      if (viewScoped.includes(id) && !state.onPipelineView) return null;   // 切走视图 → DOM 不存在
      return els[id] || null;
    },
    querySelector(sel) { return sel.includes('data-view="pipeline"') ? navLink : null; },
    querySelectorAll() { return []; },
    createElement() { return new El(); },
  };

  const sandbox = {
    document, console,
    window: { addEventListener() { } },
    location: { hash: '#pipeline' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    setInterval: (fn) => { state.timers.push(fn); return state.timers.length; },
    clearInterval: (id) => { if (id) state.timers[id - 1] = null; },
    setTimeout, clearTimeout,
    API: {
      health: async () => ({ llm: { model: 'stub', configured: false }, kb: { documents: 1, chunks: 1 } }),
      contents: async () => ({ contents: [] }),
      markets: async () => ({ markets: [{ code: 'US', name: '美国' }, { code: 'JP', name: '日本' }] }),
      tasks: async () => ({ tasks: [] }),
      runPipeline: async () => ({ job_id: 'job-abcdef123456', cached: false }),
      job: async () => sandbox.__jobReply,
    },
    __jobReply: { status: 'running', progress: 'Researcher' },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), sandbox, { filename: 'app.js' });
  // const 声明位于 context 的全局词法作用域，需显式桥接到 sandbox 对象上
  vm.runInContext('globalThis.RunState = RunState;', sandbox);
  return { sandbox, els, navLink, state };
}

const tick = () => new Promise(r => setTimeout(r, 0));

(async () => {
  console.log('\n=== 场景 1：跑供给中切换视图再切回 ===');
  const store = {};
  const { sandbox, els, navLink, state } = makeEnv(store);
  await tick(); await tick();

  const RunState = sandbox.RunState;

  // 1. 发起供给
  await sandbox.startRun(false);
  ok(RunState.job && RunState.job.status === 'running', '发起后进入 running 状态');
  ok(state.timers.filter(Boolean).length === 1, '仅创建 1 个轮询 timer');
  ok(els['run-btn'].disabled === true, '运行中禁用「开始供给」按钮');
  ok(!!store['tf_active_run'], '任务已持久化到 localStorage');

  // 2. 轮询一次拿到进度
  await state.timers[0]();
  ok(els['job-box'].innerHTML.includes('Researcher'), '进度渲染到 job-box');

  // 3. 切走到「仪表盘」——视图作用域内的 DOM 全部消失
  state.onPipelineView = false;
  sandbox.location.hash = '#overview';
  sandbox.__jobReply = { status: 'running', progress: 'Writer' };
  let threw = null;
  try { await state.timers[0](); } catch (e) { threw = e; }
  ok(threw === null, '切走后轮询不抛异常（原 bug：写入游离 DOM）');
  ok(RunState.job.status === 'running', '切走后任务状态仍为 running（未中断）');
  ok(RunState.timer !== null, '切走后轮询 timer 仍存活');
  ok(RunState.job.progress === 'Writer', '切走期间进度仍在更新');
  const badge = navLink.querySelector('.nav-badge');
  ok(badge && badge.textContent === '运行中', '侧边栏显示「运行中」徽标');

  // 4. 切回「跑供给」——视图重建
  state.onPipelineView = true;
  sandbox.location.hash = '#pipeline';
  els['job-box'].innerHTML = ''; els['job-box'].className = 'job-box';   // 模拟 innerHTML 重渲染
  await sandbox.pipeline();
  await tick();
  ok(els['job-box'].className.includes('show'), '切回后 job-box 重新显示（原 bug：空白）');
  ok(els['job-box'].innerHTML.includes('Writer'), '切回后恢复当前 Agent 进度');
  ok(state.timers.filter(Boolean).length === 1, '切回后未重复创建 timer');
  ok(els['mk'].value === 'US', '切回后市场选择器回填为运行中的市场');

  // 5. 任务完成
  sandbox.__jobReply = { status: 'done', result: { content_id: 'c-123' } };
  await state.timers[0]();
  ok(RunState.job.status === 'done', '完成后状态为 done');
  ok(RunState.timer === null, '完成后轮询已停止');
  ok(els['job-box'].innerHTML.includes('c-123'), '完成后展示内容链接');
  ok(els['run-btn'].disabled === false, '完成后按钮恢复可用');

  console.log('\n=== 场景 2：运行中刷新浏览器（新 JS 环境 + 保留 localStorage） ===');
  const store2 = {};
  const env2 = makeEnv(store2);
  await tick(); await tick();
  await env2.sandbox.startRun(false);
  ok(!!store2['tf_active_run'], '运行态已写入 localStorage');

  const env3 = makeEnv(store2);          // 模拟刷新：全新环境，共享 localStorage
  await tick(); await tick();
  ok(env3.sandbox.RunState.job && env3.sandbox.RunState.job.status === 'running', '刷新后恢复 running 状态');
  ok(env3.sandbox.RunState.timer !== null, '刷新后自动续上轮询');
  ok(env3.state.timers.filter(Boolean).length === 1, '刷新后仅 1 个 timer');

  console.log('\n=== 场景 3：后端重启导致 job 失联 ===');
  const env4 = makeEnv({});
  await tick(); await tick();
  await env4.sandbox.startRun(false);
  env4.sandbox.__jobReply = { status: 'unknown' };
  await env4.state.timers[0]();
  ok(env4.sandbox.RunState.job.status === 'running', '首次 unknown 容忍，不误判');
  await env4.state.timers[0]();
  await env4.state.timers[0]();
  ok(env4.sandbox.RunState.job.status === 'lost', '连续 3 次 unknown 判定失联');
  ok(env4.sandbox.RunState.timer === null, '失联后停止轮询，不无限空转');

  console.log(`\n${failures === 0 ? 'ALL PASSED' : failures + ' FAILED'}\n`);
  process.exit(failures === 0 ? 0 : 1);
})();
