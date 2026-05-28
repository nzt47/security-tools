// ════════════════════════════════════════════════════════════
// 灵犀 · Agent 管理侧边栏 — 框架核心
// ════════════════════════════════════════════════════════════

// ── 折叠/展开 ──
function toggleSidebar() {
  const app = document.getElementById('app');
  app.classList.toggle('collapsed');
  const isCollapsed = app.classList.contains('collapsed');
  try { sessionStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0'); } catch(e) {}
  window.dispatchEvent(new Event('resize'));
}

function initSidebarState() {
  try {
    const collapsed = sessionStorage.getItem('sidebar_collapsed');
    if (collapsed === '1') {
      document.getElementById('app').classList.add('collapsed');
    }
  } catch(e) {}
}

// ── 标签切换 ──
function switchSidebarTab(panelName) {
  document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector(`.sidebar-tab[data-panel="${panelName}"]`);
  if (tab) tab.classList.add('active');
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
  const detailView = document.getElementById(`detail-${panelName}`);
  if (detailView) {
    detailView.classList.add('active');
    loadSidebarModule(panelName);
  }
  try { sessionStorage.setItem('sidebar_active_panel', panelName); } catch(e) {}
}

function initSidebarTab() {
  try {
    const saved = sessionStorage.getItem('sidebar_active_panel');
    if (saved && document.querySelector(`.sidebar-tab[data-panel="${saved}"]`)) {
      switchSidebarTab(saved);
      return;
    }
  } catch(e) {}
  switchSidebarTab('history');
}

// ── 模块数据加载路由 ──
function loadSidebarModule(name) {
  switch(name) {
    case 'history': typeof loadHistory === 'function' && loadHistory(); break;
    case 'skills': typeof loadSkills === 'function' && loadSkills(); break;
    case 'tools': typeof loadTools === 'function' && loadTools(); break;
    case 'personality': typeof loadPersonality === 'function' && loadPersonality(); break;
    case 'memory': typeof loadMemory === 'function' && loadMemory(); break;
  }
}

// ── Toast 提示 ──
function showToast(message, type = 'success') {
  const existing = document.querySelector('.sidebar-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `sidebar-toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// ── 确认弹窗 ──
function showConfirm(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'sidebar-confirm-overlay';
    overlay.innerHTML = `
      <div class="sidebar-confirm-box">
        <p>${message}</p>
        <div class="sidebar-confirm-actions">
          <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove(); window._confirmResolve(false)">取消</button>
          <button class="btn-sm danger" onclick="this.closest('.sidebar-confirm-overlay').remove(); window._confirmResolve(true)">确定</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    window._confirmResolve = resolve;
  });
}

// ── HTML 转义（防 XSS） ──
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

// ── API 辅助 ──
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`GET ${url} failed: ${r.status}`);
  return r.json();
}

async function apiPost(url, data = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error(`POST ${url} failed: ${r.status}`);
  return r.json();
}

async function apiDelete(url) {
  const r = await fetch(url, { method: 'DELETE' });
  if (!r.ok) throw new Error(`DELETE ${url} failed: ${r.status}`);
  return r.json();
}

// ── 拖拽调整侧边栏宽度 ──
function initSidebarResizer() {
  const resizer = document.getElementById('sidebar-resizer');
  const sidebar = document.getElementById('sidebar');
  if (!resizer || !sidebar) return;

  let startX, startWidth;

  resizer.addEventListener('mousedown', (e) => {
    startX = e.clientX;
    startWidth = sidebar.offsetWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    // 拖拽时禁用动画
    document.getElementById('app').style.transition = 'none';
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });

  function onMouseMove(e) {
    const newWidth = Math.max(160, Math.min(600, startWidth + (e.clientX - startX)));
    sidebar.style.width = newWidth + 'px';
    document.getElementById('app').style.setProperty('--sidebar-width', newWidth + 'px');
  }

  function onMouseUp() {
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.getElementById('app').style.transition = '';
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    // 持久化宽度
    const w = sidebar.offsetWidth;
    try { localStorage.setItem('sidebar_width', w.toString()); } catch(e) {}
  }
}

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
  // 恢复保存的宽度
  try {
    const saved = localStorage.getItem('sidebar_width');
    if (saved) {
      const w = parseInt(saved);
      if (w >= 160 && w <= 600) {
        document.getElementById('sidebar').style.width = w + 'px';
        document.getElementById('app').style.setProperty('--sidebar-width', w + 'px');
      }
    }
  } catch(e) {}
  initSidebarState();
  initSidebarTab();
  initSidebarResizer();
});
