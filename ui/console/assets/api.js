/* TrendForge V2 API 客户端 */
const API_BASE = (() => {
  if (location.hostname === 'localhost' || location.hostname === '127.0.0.1') {
    return 'http://localhost:8000/api';
  }
  return 'https://trendforge-v2-api.onrender.com/api';
})();

async function req(path, opts = {}) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const API = {
  health: () => req('/health'),
  markets: () => req('/markets'),
  runPipeline: (market, force) => req('/pipeline/run', { method: 'POST', body: JSON.stringify({ market, force }) }),
  job: (id) => req(`/pipeline/jobs/${id}`),
  tasks: () => req('/pipeline/tasks'),
  contents: (market = '') => req(`/contents${market ? '?market=' + market : ''}`),
  content: (id) => req(`/contents/${id}`),
  contentZh: (id, refresh = false) => req(`/contents/${id}/zh${refresh ? '?refresh=true' : ''}`, { method: 'POST' }),
  contentRevise: (id) => req(`/contents/${id}/revise`, { method: 'POST' }),
  contentReviseJob: (jobId) => req(`/contents/jobs/${jobId}`),
  trace: (id) => req(`/contents/${id}/trace`),
  simulate: (contentId) => req('/analytics/events/simulate', { method: 'POST', body: JSON.stringify({ content_id: contentId || null }) }),
  analyticsCenter: () => req('/analytics/center'),
  analyticsCalibration: () => req('/analytics/calibration'),
  analyticsOverview: () => req('/analytics/overview'),
  runFeedback: (market) => req(`/analytics/run-feedback?market=${market}`, { method: 'POST' }),
  reports: () => req('/analytics/reports'),
  kbStats: () => req('/kb/stats'),
  kbSearch: (q) => req(`/kb/search?q=${encodeURIComponent(q)}`),
  kbFreshness: () => req('/kb/freshness'),
  kbCurate: () => req('/kb/curate', { method: 'POST' }),
  kbPatches: () => req('/kb/patches'),
  kbApprove: (id) => req(`/kb/patches/${id}/approve`, { method: 'POST' }),
  kbReject: (id) => req(`/kb/patches/${id}/reject`, { method: 'POST' }),
  prompts: () => req('/prompts'),
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
};
