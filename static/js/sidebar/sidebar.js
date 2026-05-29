// ════════════════════════════════════════════════════════════
// 灵犀 · Agent 管理 — 图标栏 + 浮层面板 + 状态面板
// ════════════════════════════════════════════════════════════

let _activeFloatingPanel = null;
let _sidebarExpanded = false;

// ── 浮层面板开关 ──
function toggleFloatingPanel(panelName) {
  if (_activeFloatingPanel === panelName) {
    closeFloatingPanel();
    return;
  }
  openFloatingPanel(panelName);
}

function openFloatingPanel(panelName) {
  closeFloatingPanel();

  document.querySelectorAll('#icon-bar .icon-btn').forEach(b => b.classList.remove('active'));
  const iconBtn = document.querySelector(`#icon-bar .icon-btn[data-panel="${panelName}"]`);
  if (iconBtn) iconBtn.classList.add('active');

  const titles = {
    history: '🕐 历史会话',
    skills: '🔧 技能管理',
    tools: '🛠 工具集成',
    personality: '🎭 人格配置',
    memory: '🧠 记忆管理'
  };
  document.getElementById('floating-panel-title').textContent = titles[panelName] || panelName;

  renderPanelContent(panelName, 'floating-panel-body');

  document.getElementById('floating-overlay').classList.add('show');
  document.getElementById('floating-panel').classList.add('show');
  _activeFloatingPanel = panelName;

  loadSidebarModule(panelName);
}

function closeFloatingPanel() {
  document.querySelectorAll('#icon-bar .icon-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('floating-overlay').classList.remove('show');
  document.getElementById('floating-panel').classList.remove('show');
  _activeFloatingPanel = null;
}

// ── 面板内容渲染 ──
function renderPanelContent(panelName, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  let html = '';
  switch (panelName) {
    case 'history':
      html = `<h3>🕐 历史会话</h3>
        <input class="sidebar-search" id="history-search" placeholder="搜索历史记录..." oninput="filterHistory()">
        <div class="panel-actions">
          <select id="history-sort" class="btn-sm" onchange="loadHistory()">
            <option value="newest">最新优先</option>
            <option value="oldest">最早优先</option>
          </select>
        </div>
        <div id="history-list"></div>`;
      break;
    case 'skills':
      html = `<h3>🔧 技能管理</h3>
        <div class="panel-actions">
          <button class="btn-sm primary" onclick="showAddSkill()">+ 添加技能</button>
        </div>
        <div id="skills-list"></div>`;
      break;
    case 'tools':
      html = `<h3>🛠 工具集成</h3>
        <div id="tools-count" style="font-size:12px;color:#8b949e;margin-bottom:8px"></div>
        <div id="tools-list"></div>`;
      break;
    case 'personality':
      html = `<h3>🎭 人格配置</h3>
        <div id="personality-presets"></div>
        <div id="personality-sliders"></div>
        <div class="panel-actions" style="margin-top:12px">
          <button class="btn-sm primary" onclick="savePersonality()">💾 保存配置</button>
          <button class="btn-sm" onclick="resetPersonality()">↺ 恢复默认</button>
        </div>`;
      break;
    case 'memory':
      html = `<div class="memory-subtabs">
            <div class="memory-subtab active" data-memory-tab="memory" onclick="switchMemoryTab('memory')">📋 记忆</div>
            <div class="memory-subtab" data-memory-tab="window" onclick="switchMemoryTab('window')">🪟 窗口活动</div>
          </div>
          <div class="memory-panel active" id="memory-panel-content">
            <div id="memory-overview"></div>
            <div id="memory-content"></div>
          </div>
          <div class="window-panel" id="window-panel-content">
            <div class="window-status-bar" id="window-status"></div>
            <div class="window-current-card" id="window-current">
              <div class="wc-label">🪟 当前活跃窗口</div>
              <div class="wc-title" style="color:#8b949e">加载中...</div>
            </div>
            <div style="font-size:9px;color:#8b949e;font-weight:bold;margin-bottom:4px">📡 最近切换事件</div>
            <div class="window-events" id="window-events">
              <div class="sidebar-empty">加载中...</div>
            </div>
            <div style="font-size:9px;color:#8b949e;font-weight:bold;margin-bottom:4px">📊 今日使用时长</div>
            <div class="window-stats" id="window-stats">
              <div class="sidebar-empty">加载中...</div>
            </div>
            <div class="window-panel-actions">
              <button class="btn-sm" onclick="showWindowConfig()">⚙ 监控参数</button>
              <button class="btn-sm danger" onclick="clearWindowEvents()">清空记录</button>
            </div>
          </div>
          <div class="panel-actions" style="margin-top:8px">
            <button class="btn-sm primary" onclick="showAddMemory()">+ 手动添加</button>
            <button class="btn-sm" onclick="triggerCompression()">⚡ 触发压缩</button>
          </div>`;
      break;
  }
  container.innerHTML = html;
}

// ── 完整侧边栏展开/折叠 ──
function toggleSidebar() {
  _sidebarExpanded = !_sidebarExpanded;
  const app = document.getElementById('app');
  const sidebar = document.getElementById('sidebar');

  if (_sidebarExpanded) {
    app.classList.add('sidebar-expanded');
    sidebar.classList.add('expanded');
    if (document.getElementById('sidebar-panels-container').children.length === 0) {
      initSidebarPanels();
    }
    // 恢复上次激活的面板
    let savedPanel = 'history';
    try { savedPanel = sessionStorage.getItem('sidebar_active_panel') || 'history'; } catch(e) {}
    switchSidebarTab(savedPanel);
  } else {
    app.classList.remove('sidebar-expanded');
    sidebar.classList.remove('expanded');
  }

  try { sessionStorage.setItem('sidebar_expanded', _sidebarExpanded ? '1' : '0'); } catch(e) {}
}

function initSidebarPanels() {
  const container = document.getElementById('sidebar-panels-container');
  ['history', 'skills', 'tools', 'personality', 'memory'].forEach(name => {
    const panel = document.createElement('div');
    panel.className = 'sidebar-panel';
    panel.id = 'panel-' + name;
    container.appendChild(panel);
    renderPanelContent(name, 'panel-' + name);
  });
}

// ── 侧边栏标签切换 ──
function switchSidebarTab(panelName) {
  document.querySelectorAll('#sidebar .sidebar-tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector(`#sidebar .sidebar-tab[data-panel="${panelName}"]`);
  if (tab) tab.classList.add('active');

  document.querySelectorAll('#sidebar .sidebar-panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('panel-' + panelName);
  if (panel) {
    panel.classList.add('active');
    loadSidebarModule(panelName);
  }
  try { sessionStorage.setItem('sidebar_active_panel', panelName); } catch(e) {}
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

// ── 右侧状态面板刷新 ──
async function updateStatusPanel() {
  try {
    const r = await fetch('/api/health');
    const data = await r.json();
    const healthMap = {};
    (data || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function setSP(elId, sensorKey, unit) {
      const el = document.getElementById(elId);
      if (!el) return;
      const d = healthMap[sensorKey];
      if (d) {
        el.textContent = d.value + (unit || '');
        el.className = 'sp-value ' + (d.severity || 'normal');
      } else {
        el.textContent = '-';
        el.className = 'sp-value';
      }
    }

    setSP('sp-cpu', 'cpu_usage', '%');
    setSP('sp-memory', 'memory_usage', '%');
    setSP('sp-disk', 'disk_usage', '%');
    setSP('sp-battery', 'battery', '%');
    setSP('sp-network', 'network', '');

    // 传感器计数
    try {
      const sr = await fetch('/api/sensors');
      const sensors = await sr.json();
      const on = sensors.filter(s => s.enabled).length;
      const spSensors = document.getElementById('sp-sensors');
      if (spSensors) {
        spSensors.textContent = on + '/' + sensors.length;
        spSensors.className = 'sp-value normal';
      }
    } catch(e) {}

    // 模式
    try {
      const mr = await fetch('/api/mode');
      const mode = await mr.json();
      const spMode = document.getElementById('sp-mode');
      if (spMode) {
        spMode.textContent = mode.label || mode.mode || '-';
        spMode.className = 'sp-value normal';
      }
    } catch(e) {}

    // 事件流
    updateStatusEvents(healthMap);

  } catch(e) { console.error('Status panel update error:', e); }
}

function updateStatusEvents(healthMap) {
  const el = document.getElementById('sp-events');
  if (!el) return;
  const events = [];
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  Object.entries(healthMap).forEach(([key, m]) => {
    if (m.severity === 'warning' || m.severity === 'critical') {
      events.push({ time: timeStr, text: (m.description || key) + ': ' + m.value + (m.unit||''), cls: m.severity });
    }
  });

  if (events.length === 0) {
    el.innerHTML = '<div class="sidebar-empty">系统正常</div>';
    return;
  }
  el.innerHTML = events.map(e =>
    `<div class="sp-event"><span style="color:${e.cls==='critical'?'#f85149':'#d29922'}">⚠</span> ${e.text}</div>`
  ).join('');
}

// ── 标签栏指标刷新 ──
async function updateTabMetrics() {
  try {
    const r = await fetch('/api/health');
    const data = await r.json();
    const healthMap = {};
    (data || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function metricHTML(key, label, unit) {
      const d = healthMap[key];
      if (!d) return '';
      return `<span>${label} <b class="tm-value ${d.severity || 'normal'}">${d.value}${unit||''}</b></span>`;
    }

    const el = document.getElementById('tab-metrics');
    if (!el) return;
    const parts = [
      metricHTML('cpu_usage', 'CPU', '%'),
      metricHTML('memory_usage', '内存', '%'),
      metricHTML('disk_usage', '磁盘', '%'),
      metricHTML('battery', '电池', '%'),
    ].filter(Boolean);

    try {
      const sr = await fetch('/api/sensors');
      const sensors = await sr.json();
      const on = sensors.filter(s => s.enabled).length;
      parts.push(`<span>📡 <b class="tm-value normal">${on}/${sensors.length}</b></span>`);
    } catch(e) {}

    el.innerHTML = parts.join('<span class="tm-sep">|</span>');
  } catch(e) {}
}

// ── 全局刷新 ──
function refreshAll() {
  updateTabMetrics();
  updateStatusPanel();
}

// ── Toast ──
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
      </div>`;
    document.body.appendChild(overlay);
    window._confirmResolve = resolve;
  });
}

// ── HTML 转义 ──
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

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
  try {
    const expanded = sessionStorage.getItem('sidebar_expanded');
    if (expanded === '1') {
      _sidebarExpanded = true;
      document.getElementById('app').classList.add('sidebar-expanded');
      document.getElementById('sidebar').classList.add('expanded');
      initSidebarPanels();
      const savedPanel = sessionStorage.getItem('sidebar_active_panel') || 'history';
      switchSidebarTab(savedPanel);
    }
  } catch(e) {}

  updateTabMetrics();
  updateStatusPanel();
});

// 定时刷新（10秒）
setInterval(() => {
  updateTabMetrics();
  updateStatusPanel();
}, 10000);
