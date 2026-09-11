/* TrendForge V2 API 客户端 */
const API_BASE = (() => {
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
    return 'http://localhost:8000/api';
  }
  return 'https://trendforge-v2-api.onrender.com/api';
})();

/* 写接口共享 token（服务端配置 TF_API_TOKEN 后启用）：
   本地默认未配置 = 不需要；公网部署时首次写操作会收到 401 并弹窗索取一次，
   存入 localStorage 后自动重试。 */
let apiToken = localStorage.getItem('tf_api_token') || '';

async function req(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (apiToken) headers['X-API-Token'] = apiToken;
  const resp = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (resp.status === 401) {
    const input = prompt('后端已启用写接口鉴权（TF_API_TOKEN），请输入 API Token：');
    if (input) {
      apiToken = input.trim();
      localStorage.setItem('tf_api_token', apiToken);
      headers['X-API-Token'] = apiToken;
      const retry = await fetch(`${API_BASE}${path}`, { ...opts, headers });
      if (retry.ok) return retry.json();
      throw new Error(`鉴权失败（HTTP ${retry.status}）`);
    }
    throw new Error('需要 API Token（写操作被拒绝）');
  }
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const API = {
  health: () => req('/health'),
  markets: () => req('/markets'),
  runPipeline: (market, force) => req('/pipeline/run', { method: 'POST', body: JSON.stringify({ market, force }) }),
  cancelPipeline: (id) => req(`/pipeline/jobs/${id}/cancel`, { method: 'POST' }),
  job: (id) => req(`/pipeline/jobs/${id}`),
  tasks: () => req('/pipeline/tasks?limit=50'),
  contents: (market = '', limit = 50) => req(`/contents?limit=${limit}${market ? '&market=' + encodeURIComponent(market) : ''}`),
  content: (id) => req(`/contents/${id}`),
  contentZh: (id, refresh = false) => req(`/contents/${id}/zh${refresh ? '?refresh=true' : ''}`, { method: 'POST' }),
  contentRevise: (id) => req(`/contents/${id}/revise`, { method: 'POST' }),
  contentReviseJob: (jobId) => req(`/contents/jobs/${jobId}`),
  trace: (id) => req(`/contents/${id}/trace`),
  simulate: (contentId) => req('/analytics/events/simulate', { method: 'POST', body: JSON.stringify({ content_id: contentId || null }) }),
  analyticsCenter: () => req('/analytics/center'),
  analyticsCalibration: () => req('/analytics/calibration'),
  analyticsOverview: (market = '') => req(`/analytics/overview${market ? '?market=' + encodeURIComponent(market) : ''}`),
  runFeedback: (market) => req(`/analytics/run-feedback?market=${market}`, { method: 'POST' }),
  reports: () => req('/analytics/reports'),
  kbStats: () => req('/kb/stats'),
  kbDocuments: () => req('/kb/documents'),
  kbSearch: (q) => req(`/kb/search?q=${encodeURIComponent(q)}`),
  kbFreshness: () => req('/kb/freshness'),
  kbCurate: () => req('/kb/curate', { method: 'POST' }),
  kbPatches: () => req('/kb/patches'),
  kbApprove: (id, itemIndices) => req(`/kb/patches/${id}/approve`, { method: 'POST', body: JSON.stringify(itemIndices ? { item_indices: itemIndices } : {}) }),
  kbReject: (id) => req(`/kb/patches/${id}/reject`, { method: 'POST' }),
  prompts: () => req('/prompts'),
  badCases: () => req('/bad-cases'),
  // M3 闭环
  promptTemplates: () => req('/prompts/templates'),
  promptVersions: (template = '') => req(`/prompts/versions${template ? '?template=' + encodeURIComponent(template) : ''}`),
  promptVersionCreate: (body) => req('/prompts/versions', { method: 'POST', body: JSON.stringify(body) }),
  promptVersionAdopt: (id) => req(`/prompts/versions/${id}/adopt`, { method: 'POST' }),
  promptVersionDiff: (a, b) => req(`/prompts/versions/${a}/diff/${b}`),
  promptSuggestions: (status = 'pending') => req(`/prompts/suggestions?status=${status}`),
  promptSuggestionAdopt: (id) => req(`/prompts/suggestions/${id}/adopt`, { method: 'POST' }),
  promptSuggestionReject: (id) => req(`/prompts/suggestions/${id}/reject`, { method: 'POST' }),
  promptFeedback: (market) => req(`/prompts/feedback?market=${market}`, { method: 'POST' }),
  promptABRun: (body) => req('/prompts/ab/run', { method: 'POST', body: JSON.stringify(body) }),
  promptAdoptionImpact: () => req('/prompts/adoption-impact'),
  // 人工校准（Evaluate 段人机闭环）
  calibrationSamples: () => req('/calibration/samples'),
  calibrationSubmit: (body) => req('/calibration/scores', { method: 'POST', body: JSON.stringify(body) }),
  calibrationReport: () => req('/calibration/report'),
};
