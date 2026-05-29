// ════════════════════════════════════════════════════════════
// 灵犀 · 导航栏 + 浮层面板
// ════════════════════════════════════════════════════════════

// ── 全局刷新 ──
function refreshAll() {
  app.emit('refresh');
}

// ── Toast ──
function showToast(message, type = 'success') {
  app.showToast(message, type);
}

// ── 确认弹窗 ──
function showConfirm(message) {
  return app.showConfirm(message);
}

// ── HTML 转义 ──
function escapeHtml(text) {
  return app.escapeHtml(text);
}

// ── API 辅助 ──
async function apiGet(url) {
  return app.get(url);
}

async function apiPost(url, data = {}) {
  return app.post(url, data);
}

async function apiDelete(url) {
  return app.del(url);
}

