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
  trace: (id) => req(`/contents/${id}/trace`),
  simulate: (contentId) => req('/analytics/events/simulate', { method: 'POST', body: JSON.stringify({ content_id: contentId || null }) }),
  analyticsOverview: () => req('/analytics/overview'),
  runFeedback: (market) => req(`/analytics/run-feedback?market=${market}`, { method: 'POST' }),
  reports: () => req('/analytics/reports'),
  kbStats: () => req('/kb/stats'),
  kbSearch: (q) => req(`/kb/search?q=${encodeURIComponent(q)}`),
  prompts: () => req('/prompts'),
};
