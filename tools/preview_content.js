/**
 * 用线上真实数据离线渲染内容详情，导出成可直接打开的 HTML 预览页。
 *
 * 为什么不用浏览器截图：本地没装 Chromium，而渲染逻辑本身是纯函数
 * （app.js 里的 renderBrief / renderFormats），用 stub DOM 喂真实数据
 * 就能得到与线上完全一致的 HTML，再套上真实 styles.css 即可肉眼验收。
 *
 * 用法：
 *   node tools/preview_content.js <contentId> [outFile]
 *   node tools/preview_content.js            # 默认取线上第一条非中文内容
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const APP = path.join(ROOT, 'ui/console/assets/app.js');
const CSS = path.join(ROOT, 'ui/console/assets/styles.css');
const API_BASE = process.env.TF_API || 'https://trendforge-v2-api.onrender.com';

class El {
  constructor(id = '') {
    this.id = id; this._html = ''; this.dataset = {}; this.onclick = null;
    this.disabled = false; this.value = ''; this.options = []; this.children = [];
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  querySelector() { return null; }
}

function makeEnv(content, mode) {
  const els = { 'view-root': new El('view-root'), 'tab-body': new El('tab-body'), 'sys-status': new El('sys-status') };
  const tabs = ['article', 'brief', 'formats', 'dist', 'trace', 'quality'].map(t => {
    const a = new El(); a.dataset.tab = t;
    a.classList = { add() { a._active = true; }, remove() { a._active = false; }, toggle() {} };
    return a;
  });
  tabs[0]._active = true;
  const store = { tf_zh_mode: mode };
  const document = {
    getElementById: id => els[id] || null,
    querySelector: sel => (sel === '.tabs a.active' ? tabs.find(t => t._active) : null),
    querySelectorAll: sel => (sel === '.tabs a' ? tabs : []),
    createElement: () => new El(),
  };
  const sandbox = {
    document, console,
    window: { addEventListener() {} },
    location: { hash: `#content/${content.id}` },
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
    },
    setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
    API: {
      health: async () => ({ llm: { model: 'deepseek-v4-flash', configured: true } }),
      content: async () => JSON.parse(JSON.stringify(content)),
      // 快照已自带对照，线上不会再发这个请求；这里兜底返回缓存
      contentZh: async () => ({ available: true, cached: true, translation: content.translation }),
      trace: async () => ({ task: { total_duration_ms: 0, total_cost_cny: 0, review_rounds: 0 }, spans: [] }),
      contents: async () => ({ contents: [] }), markets: async () => ({ markets: [] }), tasks: async () => ({ tasks: [] }),
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), sandbox, { filename: 'app.js' });
  return { sandbox, els, tabs };
}

const tick = () => new Promise(r => setTimeout(r, 0));

async function renderTab(content, tabName, mode) {
  const { sandbox, els, tabs } = makeEnv(content, mode);
  await sandbox.contentDetail(content.id);
  await tick(); await tick(); await tick();
  tabs.forEach(t => { t._active = false; });
  const t = tabs.find(x => x.dataset.tab === tabName);
  t._active = true;
  await t.onclick();
  await tick(); await tick(); await tick();
  return els['tab-body'].innerHTML;
}

async function main() {
  const argId = process.argv[2];
  const out = process.argv[3] || path.join(ROOT, '.preview', 'content_preview.html');

  const list = await (await fetch(`${API_BASE}/api/contents`)).json();
  const pick = argId
    ? list.contents.find(c => c.id === argId)
    : list.contents.find(c => !(c.language || '').toLowerCase().startsWith('zh'));
  if (!pick) throw new Error('找不到目标内容');
  const content = await (await fetch(`${API_BASE}/api/contents/${pick.id}`)).json();
  console.log(`内容：${content.market}/${content.language} · ${content.title}`);
  console.log(`needs_zh=${content.needs_zh} · 自带对照=${!!(content.translation && content.translation.brief)}`);

  const blocks = [];
  for (const [mode, label] of [['both', '双语对照'], ['src', '仅原文'], ['zh', '仅中文']]) {
    const brief = await renderTab(content, 'brief', mode);
    const formats = await renderTab(content, 'formats', mode);
    blocks.push(`<section class="preview-sec">
      <h2 class="preview-h">选题简报 · ${label}</h2>${brief}
      <h2 class="preview-h">多形态派生 · ${label}</h2>${formats}
    </section>`);
  }

  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>内容渲染预览 · ${content.market}</title>
<style>${fs.readFileSync(CSS, 'utf8')}</style>
<style>
  body{background:#f5f6fa;padding:24px;max-width:1080px;margin:0 auto}
  .preview-h{font-size:15px;color:#3b4b7a;margin:26px 0 10px;padding-left:9px;border-left:3px solid #4a6cf7}
  .preview-sec{margin-bottom:44px;padding-bottom:12px;border-bottom:1px dashed #cfd6e6}
  .preview-top{background:#fff;border:1px solid #e3e7f0;border-radius:10px;padding:16px 18px;margin-bottom:8px}
  .preview-top h1{font-size:17px;margin:0 0 6px}
  .preview-top p{margin:0;color:#6b7490;font-size:13px}
</style></head><body>
<div class="preview-top">
  <h1>${content.title}</h1>
  <p>${content.market} / ${content.language} · 离线渲染预览（数据取自线上 API，渲染逻辑取自 app.js）</p>
</div>
${blocks.join('\n')}
</body></html>`;

  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, html, 'utf8');
  console.log(`\n✓ 预览已生成：${out}`);
  console.log(`  含裸 JSON：${/<pre>\s*[{[]/.test(html) ? '是（异常）' : '否'}`);
}

main().catch(e => { console.error(e); process.exit(1); });
