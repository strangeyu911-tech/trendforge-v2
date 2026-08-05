/**
 * 内容详情渲染回归测试：多形态结构化渲染 + 中英双语对照。
 * 用 stub DOM 在 Node 中加载真实的 ui/console/assets/app.js。
 * 运行： node tools/test_render.js
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

/* ---------- 固件：一条美国市场内容 + 对齐的中文镜像 ---------- */
const US = {
  id: 'us-1', market: 'US', language: 'en', needs_zh: true,
  title: "Apple's $1,999 Foldable", summary: 'Data behind the premium price',
  quality_avg: 4.2, verdict: 'approved', is_fallback: false,
  brief: {
    topic: 'Apple foldable iPhone value proposition',
    angle: 'A data-driven comparison of price vs rivals',
    hook: 'Double the price of its rivals. Worth it?',
    audience: 'Affluent tech enthusiasts aged 25-45',
    style: 'deep_dive',
    why_now: 'Mass production confirmed for September launch',
    avoid: ['Presenting rumors as facts', 'Hype-driven must-buy framing'],
    keywords: ['foldable', 'iPhone Fold'],
    format_plan: ['article', 'card', 'video_script'],
  },
  formats: {
    card: {
      title: "Apple's $1,999 Foldable: The Data",
      points: ['Starting price $1,999', '7.8-inch inner display', 'Hinge rated 600,000 folds'],
      key_data: '600,000-fold rated hinge',
    },
    video_script: {
      hook: 'A $1,999 iPhone that folds. Is it worth it?',
      scenes: [
        { shot: 'Close-up of the foldable opening', voiceover: 'Apple unveils the iPhone Fold.', subtitle: 'iPhone Fold | $1,999' },
        { shot: 'Hinge animation with counter', voiceover: 'Rated for 600,000 folds.', subtitle: '600K folds' },
      ],
      cta: 'Follow for the full teardown',
      hashtags: ['#iPhoneFold', '#Apple'],
    },
    brief_news: { headline: 'Apple to launch $1,999 foldable', body: 'Mass production has begun ahead of the September reveal.' },
    comment: { question: 'Would you pay $1,999 for a foldable?', angles: ['Status symbol', 'Real laptop replacement'] },
    // 未知形态：必须走通用渲染兜底，而不是抛 JSON
    podcast: { intro: 'Welcome back', segments: [{ topic: 'Pricing', minutes: 4 }] },
  },
  distribution: { plan: [] }, quality: { avg: 4.2, verdict: 'approved' },
};

const ZH_MIRROR = {
  lang: 'zh', model: 'deepseek-v4-flash', generated_at: 'pregen',
  title: '苹果 $1,999 折叠屏', summary: '高价背后的数据',
  brief: {
    topic: '苹果折叠屏 iPhone 的价值主张',
    angle: '用数据对比价格与竞品',
    hook: '价格是竞品的两倍，值吗？',
    audience: '25-45 岁高收入科技爱好者',
    why_now: '已确认量产，9 月发布',
    avoid: ['把传闻当事实呈现', '制造"必买"的炒作框架'],
    keywords: ['折叠屏', 'iPhone Fold'],
  },
  formats: {
    card: {
      title: '苹果 $1,999 折叠屏：数据说话',
      points: ['起售价 $1,999', '7.8 英寸内屏', '转轴额定 60 万次折叠'],
      key_data: '转轴额定 60 万次折叠',
    },
    video_script: {
      hook: '一部 $1,999、能对折的 iPhone。值吗？',
      scenes: [
        { shot: '折叠屏开合特写', voiceover: '苹果发布 iPhone Fold。', subtitle: 'iPhone Fold | $1,999' },
        { shot: '转轴动画配计数器', voiceover: '额定 60 万次折叠。', subtitle: '60 万次折叠' },
      ],
      cta: '关注我们看完整拆解',
      hashtags: ['#iPhone折叠屏', '#苹果'],
    },
    brief_news: { headline: '苹果将推出 $1,999 折叠屏', body: '量产已启动，9 月正式亮相。' },
    comment: { question: '你愿意花 $1,999 买折叠屏吗？', angles: ['身份象征', '真正的笔记本替代品'] },
    podcast: { intro: '欢迎回来', segments: [{ topic: '定价', minutes: '' }] },
  },
};

const CN = {
  id: 'cn-1', market: 'CN', language: 'zh', needs_zh: false,
  title: '文心5.0发布', summary: '职场人如何应对', quality_avg: 4.4, verdict: 'approved',
  brief: { topic: '文心5.0', angle: '技能升级', hook: '钩子', audience: '职场人', style: 'explainer', why_now: '刚发布', avoid: [], keywords: [], format_plan: ['card'] },
  formats: { card: { title: '文心5.0', points: ['要点一'], key_data: '5.0' } },
  distribution: { plan: [] }, quality: {},
};

/* ---------- stub DOM ---------- */
class El {
  constructor(id = '') {
    this.id = id; this._html = ''; this.dataset = {}; this.onclick = null;
    this.disabled = false; this.value = ''; this.options = []; this.children = [];
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  querySelector() { return null; }
  classList = { add() { }, remove() { }, toggle() { } };
}

function makeEnv(contentById, opts = {}) {
  const els = { 'view-root': new El('view-root'), 'tab-body': new El('tab-body'), 'sys-status': new El('sys-status') };
  const tabs = ['article', 'brief', 'formats', 'dist', 'trace', 'quality'].map(t => {
    const a = new El(); a.dataset.tab = t;
    a.classList = { add() { a._active = true; }, remove() { a._active = false; }, toggle() { } };
    return a;
  });
  tabs[0]._active = true;
  const calls = { zh: 0 };
  const store = {};

  const document = {
    getElementById: (id) => els[id] || null,
    querySelector: (sel) => (sel === '.tabs a.active' ? tabs.find(t => t._active) : null),
    querySelectorAll: (sel) => {
      if (sel === '.tabs a') return tabs;
      if (sel === '[data-zhmode]') return [];
      return [];
    },
    createElement: () => new El(),
  };

  const sandbox = {
    document, console,
    window: { addEventListener() { } },
    location: { hash: '#content/us-1' },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    setInterval: () => 1, clearInterval() { }, setTimeout, clearTimeout,
    API: {
      health: async () => ({ llm: { model: 'stub', configured: false } }),
      content: async (id) => JSON.parse(JSON.stringify(contentById[id])),
      contentZh: async () => { calls.zh++; return opts.zhReply || { available: true, translation: ZH_MIRROR }; },
      trace: async () => ({ task: { total_duration_ms: 0, total_cost_cny: 0, review_rounds: 0 }, spans: [] }),
      contents: async () => ({ contents: [] }),
      markets: async () => ({ markets: [] }),
      tasks: async () => ({ tasks: [] }),
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), sandbox, { filename: 'app.js' });
  vm.runInContext('globalThis.ZH = ZH;', sandbox);
  return { sandbox, els, tabs, calls, store };
}

const tick = () => new Promise(r => setTimeout(r, 0));
const clickTab = async (tabs, sandbox, name) => {
  tabs.forEach(t => { t._active = false; });
  const t = tabs.find(x => x.dataset.tab === name);
  t._active = true;
  await t.onclick();
  await tick(); await tick(); await tick();
};

(async () => {
  console.log('\n=== 场景 1：多形态结构化渲染（不再是 JSON） ===');
  {
    const { sandbox, els, tabs } = makeEnv({ 'us-1': US });
    await sandbox.contentDetail('us-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'formats');
    const h = els['tab-body'].innerHTML;

    ok(!h.includes('<pre'), '不再使用 <pre> 抛 JSON');
    ok(!/\{\s*&quot;|\{\s*"/.test(h.replace(/class="[^"]*"/g, '')), '输出里没有裸 JSON 片段');
    ok(h.includes('短视频脚本') && h.includes('资讯摘要卡片') && h.includes('快讯') && h.includes('评论区引导'), '四种形态都有中文标题');
    ok(h.includes('分镜') || (h.includes('画面') && h.includes('口播') && h.includes('字幕')), '短视频脚本渲染成分镜表（画面/口播/字幕）');
    ok(h.includes('Apple unveils the iPhone Fold.'), '口播内容被渲染');
    ok(h.includes('fmt-points') && h.includes('Starting price $1,999'), '摘要卡要点渲染成列表');
    ok(h.includes('关键数据') && h.includes('600,000-fold rated hinge'), '关键数据高亮展示');
    ok(h.includes('话题标签') && h.includes('#iPhoneFold'), 'hashtag 渲染成标签');
    ok(h.includes('讨论角度') && h.includes('Status symbol'), '评论引导渲染提问+角度');
    ok(h.includes('kv-grid') && h.includes('Welcome back'), '未知形态 podcast 走通用键值渲染兜底');
  }

  console.log('\n=== 场景 2：双语对照三种模式 ===');
  {
    const { sandbox, els, tabs, store } = makeEnv({ 'us-1': US });
    await sandbox.contentDetail('us-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'formats');
    await tick(); await tick();
    const ZH = sandbox.ZH;

    ok(ZH.status === 'ready', '缺翻译时自动触发回译并就绪');
    let h = els['tab-body'].innerHTML;
    ok(h.includes('zh-bar') && h.includes('双语对照'), '非中文市场出现语言切换条');
    ok(h.includes('Apple unveils the iPhone Fold.') && h.includes('苹果发布 iPhone Fold。'), '双语模式：原文与中文同时出现');
    ok(h.includes('zh-line'), '中文对照使用独立样式行');

    ZH.mode = 'src'; sandbox.paintTab(); await tick(); await tick();
    h = els['tab-body'].innerHTML;
    ok(h.includes('Apple unveils the iPhone Fold.') && !h.includes('苹果发布 iPhone Fold。'), '原文模式：只显示原文');

    ZH.mode = 'zh'; sandbox.paintTab(); await tick(); await tick();
    h = els['tab-body'].innerHTML;
    ok(h.includes('苹果发布 iPhone Fold。') && !h.includes('Apple unveils the iPhone Fold.'), '中文模式：只显示中文');
    ok(h.includes('$1,999'), '中文模式下金额等原样保留的内容仍在');

    ZH.mode = 'both';
  }

  console.log('\n=== 场景 3：选题简报双语 ===');
  {
    const { sandbox, els, tabs, calls } = makeEnv({ 'us-1': US });
    await sandbox.contentDetail('us-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'brief');
    await tick(); await tick();
    const h = els['tab-body'].innerHTML;

    ok(h.includes('选题') && h.includes('角度') && h.includes('钩子'), '简报字段标签完整');
    ok(h.includes('Apple foldable iPhone value proposition') && h.includes('苹果折叠屏 iPhone 的价值主张'), '简报选题中英对照');
    ok(h.includes('把传闻当事实呈现'), '避免事项数组逐项对照');
    ok(h.includes('折叠屏'), '关键词对照');
    ok(calls.zh === 1, '简报与多形态共用一次回译调用');

    await clickTab(tabs, sandbox, 'formats');
    await tick();
    ok(calls.zh === 1, '切到多形态不重复请求回译');
  }

  console.log('\n=== 场景 4：中文市场 / 降级 / 转义 ===');
  {
    const { sandbox, els, tabs, calls } = makeEnv({ 'cn-1': CN });
    await sandbox.contentDetail('cn-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'brief');
    const h = els['tab-body'].innerHTML;
    ok(!h.includes('zh-bar'), '中文市场不显示语言切换条');
    ok(calls.zh === 0, '中文市场不触发回译请求');
  }
  {
    const { sandbox, els, tabs } = makeEnv({ 'us-1': US },
      { zhReply: { available: false, reason: '未配置 LLM API Key，无法生成中文对照' } });
    await sandbox.contentDetail('us-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'formats');
    await tick(); await tick();
    const h = els['tab-body'].innerHTML;
    ok(sandbox.ZH.status === 'unavailable', '回译不可用时状态为 unavailable');
    ok(h.includes('未配置 LLM API Key'), '不可用原因提示给用户');
    ok(h.includes('Apple unveils the iPhone Fold.'), '降级后原文照常展示');
  }
  {
    const evil = JSON.parse(JSON.stringify(US));
    evil.formats.card.title = '<script>alert(1)</script>';
    const { sandbox, els, tabs } = makeEnv({ 'us-1': evil });
    await sandbox.contentDetail('us-1');
    await tick(); await tick();
    await clickTab(tabs, sandbox, 'formats');
    await tick(); await tick();
    const h = els['tab-body'].innerHTML;
    ok(!h.includes('<script>') && h.includes('&lt;script&gt;'), 'HTML 转义生效，不注入脚本');
  }

  console.log(`\n${failures === 0 ? '✅ 全部通过' : `❌ ${failures} 项失败`}`);
  process.exit(failures === 0 ? 0 : 1);
})();
