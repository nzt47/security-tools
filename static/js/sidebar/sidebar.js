// ════════════════════════════════════════════════════════════
// 灵犀 · 导航栏 + 浮层面板
// ════════════════════════════════════════════════════════════

// ── 全局刷新 ──
function refreshAll() {
  updateStatusPanel();
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

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
  // 初始化对话视图
  var cv = document.getElementById('chat-view');
  if (cv) {
    cv.style.display = 'flex';
    cv.classList.add('active');
  }

  updateStatusPanel();
});

// 定时刷新（10秒）
setInterval(() => {
  updateStatusPanel();
}, 10000);
