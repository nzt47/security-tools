// ════════════════════════════════════════════════════════════
// 云枢 · 权限控制面板 — 实时监控 + 数据访问 + 紧急刹车
// ════════════════════════════════════════════════════════════

// ── 状态缓存 ──
let _pcState = {
  status: null,
  accessFilter: 'all',
  emergencyBanner: false,
};

// ── 轮询间隔 ──
const PC_POLL_INTERVAL = 2000; // 2 秒
let _pcTimer = null;

// ── 初始化 ──
function initPermissionControl() {
  // 首次加载
  loadPermissionStatus();
  loadPermissionAccessLog();
  loadPermissionStats();

  // 独立高频轮询（2秒间隔，不受 tick 影响）
  setInterval(() => {
    loadPermissionStatus();
    loadPermissionAccessLog();
  }, PC_POLL_INTERVAL);

  // 低频加载统计展开（每 30 秒刷新一次）
  setInterval(() => {
    const toolsSection = document.getElementById('pc-tools-section');
    if (toolsSection && toolsSection.style.display === 'block') {
      loadPermissionStats();
    }
  }, 30000);

  console.log('[权限面板] 已初始化 (轮询间隔: ' + PC_POLL_INTERVAL + 'ms)');
}

// ════════════════════════════════════════════════════════════
//  状态加载
// ════════════════════════════════════════════════════════════

async function loadPermissionStatus() {
  try {
    const data = await app.get('/api/permission/status');
    _pcState.status = data;
    renderCurrentAction(data.current_action, data.emergency);
    renderStats(data.stats);
    renderToggles(data.toggles);
    renderEmergencyBanner(data.emergency);
  } catch (e) {
    // 静默失败，服务可能刚启动
  }
}

async function loadPermissionAccessLog() {
  try {
    const filter = _pcState.accessFilter;
    const url = filter === 'all'
      ? '/api/permission/access-log?limit=10'
      : '/api/permission/access-log?limit=10&type=' + filter;
    const data = await app.get(url);
    renderAccessLog(data.access_logs || []);
  } catch (e) {}
}

async function loadPermissionStats() {
  try {
    const data = await app.get('/api/permission/stats');
    renderToolList(data.tools || []);
    renderGuardStats(data.guard_stats || {});
    renderPermStats(data.perm_stats || {});
  } catch (e) {}
}

// ════════════════════════════════════════════════════════════
//  渲染：当前操作跟踪
// ════════════════════════════════════════════════════════════

function renderCurrentAction(action, emergency) {
  const el = document.getElementById('pc-current-action');
  if (!el) return;

  // 确定状态
  if (!action) {
    // 空闲状态
    el.className = 'pc-current-action idle';
    el.innerHTML = '<div class="pc-ca-header"><span class="pc-ca-tool"><span class="pc-icon">⏹</span> 智能体空闲中</span><span class="pc-ca-status-badge idle">空闲</span></div>';
    return;
  }

  const isPaused = emergency && emergency.paused;
  const isDangerous = action.tool && /delete|remove|format|stop|shutdown|exec/i.test(action.tool);

  let statusClass = 'running';
  let statusLabel = '执行中';
  if (isPaused) {
    statusClass = 'paused';
    statusLabel = '已暂停';
  } else if (isDangerous) {
    statusClass = 'dangerous';
    statusLabel = '⚠ 危险';
  }

  el.className = 'pc-current-action ' + statusClass;

  const tool = action.tool || '未知操作';
  const target = action.target || (action.params ? JSON.stringify(action.params).substring(0, 40) : '');
  const elapsed = action.elapsed || 0;
  const authStatus = action.auth || 'allowed';

  const authLabel = {
    'allowed': '✅ 已授权',
    'pending': '⏳ 待确认',
    'blocked': '🚫 已拦截',
  }[authStatus] || '✅ 已授权';

  el.innerHTML =
    '<div class="pc-ca-header">' +
      '<span class="pc-ca-tool"><span class="pc-icon">🔧</span> ' + app.escapeHtml(tool) + '</span>' +
      '<span class="pc-ca-status-badge ' + statusClass + '">' + statusLabel + '</span>' +
    '</div>' +
    (target ? '<div class="pc-ca-target">📂 ' + app.escapeHtml(target) + '</div>' : '') +
    '<div class="pc-ca-meta">' +
      '<span class="pc-ca-auth ' + authStatus + '">' + authLabel + '</span>' +
      '<span>⏱ ' + elapsed.toFixed(1) + 's</span>' +
    '</div>';
}

// ════════════════════════════════════════════════════════════
//  渲染：统计数据
// ════════════════════════════════════════════════════════════

function renderStats(stats) {
  if (!stats) return;
  const el = document.getElementById('pc-stats');
  if (!el) return;

  el.innerHTML =
    '<span class="pc-stat-chip blocked">⛔ 拦截 <span class="pc-stat-val">' + (stats.blocked || 0) + '</span></span>' +
    '<span class="pc-stat-chip warned">⚠️ 告警 <span class="pc-stat-val">' + (stats.total_alerts || 0) + '</span></span>' +
    '<span class="pc-stat-chip checks">📝 检查 <span class="pc-stat-val">' + (stats.perm_checks || 0) + '</span></span>' +
    '<span class="pc-stat-chip tools">🔧 工具 <span class="pc-stat-val">' + (stats.tools || 0) + '</span></span>';
}

// ════════════════════════════════════════════════════════════
//  渲染：权限开关
// ════════════════════════════════════════════════════════════

function renderToggles(toggles) {
  if (!toggles) return;
  const el = document.getElementById('pc-toggles');
  if (!el) return;

  const labels = {
    'window_monitor': '🪟 窗口监控',
    'sensor': '📊 传感器',
    'network_access': '🌐 网络访问',
    'file_write': '📁 文件写入',
    'dangerous_ops': '⚡ 危险操作',
  };

  el.innerHTML = Object.entries(toggles).map(([key, val]) =>
    '<div class="pc-toggle-item" onclick="togglePermissionSwitch(\'' + key + '\')">' +
      '<span class="pc-toggle-label">' + (labels[key] || key) + '</span>' +
      '<span class="pc-toggle-switch ' + (val ? 'on' : '') + '"></span>' +
    '</div>'
  ).join('') || '<div class="pc-empty">无可用开关</div>';
}

async function togglePermissionSwitch(key) {
  try {
    const data = await app.post('/api/permission/toggle', { key: key });
    if (data.ok) {
      // 更新本地状态
      if (_pcState.status && _pcState.status.toggles) {
        _pcState.status.toggles[key] = data.enabled;
      }
      renderToggles(_pcState.status?.toggles);
      app.showToast(data.enabled ? '✅ ' + key + ' 已开启' : '⛔ ' + key + ' 已关闭', 'info');
    }
  } catch (e) {
    app.showToast('❌ 切换失败', 'error');
  }
}

// ════════════════════════════════════════════════════════════
//  渲染：紧急横幅
// ════════════════════════════════════════════════════════════

function renderEmergencyBanner(emergency) {
  const el = document.getElementById('pc-emergency-banner');
  if (!el) return;

  if (emergency && emergency.stopped) {
    el.className = 'pc-emergency-banner active';
    el.textContent = '🚨 紧急停止已触发 — 智能体已停止处理请求';
    _pcState.emergencyBanner = true;
  } else if (emergency && emergency.paused) {
    el.className = 'pc-emergency-banner active';
    el.textContent = '⏸ 智能体已暂停 — 点击"恢复"继续';
    el.style.borderColor = '#d2992244';
    el.style.color = '#d29922';
    el.style.background = '#d2992211';
    _pcState.emergencyBanner = true;
  } else {
    el.className = 'pc-emergency-banner';
    _pcState.emergencyBanner = false;
  }
}

// ════════════════════════════════════════════════════════════
//  渲染：访问日志
// ════════════════════════════════════════════════════════════

function renderAccessLog(logs) {
  const el = document.getElementById('pc-access-list');
  if (!el) return;

  if (!logs || logs.length === 0) {
    el.innerHTML = '<div class="pc-empty">暂无访问记录</div>';
    return;
  }

  el.innerHTML = logs.map(l => {
    const time = (l.time || '').substring(11, 19);
    const typeIcons = {
      'file': '📁',
      'window': '🪟',
      'sensor': '📊',
      'network': '🌐',
    };
    const icon = typeIcons[l.type] || '📋';
    const permClass = l.permission === 'blocked' ? 'blocked' :
                      l.permission === 'requires_consent' ? 'pending' : 'allowed';
    const permLabel = l.permission === 'blocked' ? '已拦截' :
                      l.permission === 'requires_consent' ? '待授权' : '已授权';

    return '<div class="pc-access-item">' +
      '<span class="pc-ai-time">' + time + '</span>' +
      '<span class="pc-ai-type">' + icon + '</span>' +
      '<span class="pc-ai-target" title="' + app.escapeHtml(l.target || '') + '">' + app.escapeHtml(l.target || '') + '</span>' +
      '<span class="pc-ai-perm ' + permClass + '">' + permLabel + '</span>' +
    '</div>';
  }).join('');
}

// ════════════════════════════════════════════════════════════
//  渲染：工具能力列表
// ════════════════════════════════════════════════════════════

function renderToolList(tools) {
  const el = document.getElementById('pc-tool-list');
  if (!el) return;

  if (!tools || tools.length === 0) {
    el.innerHTML = '<div class="pc-empty">暂无已注册工具</div>';
    return;
  }

  el.innerHTML = tools.map(t =>
    '<span class="pc-tool-tag ' + (t.level || 'allowed') + '" title="' + app.escapeHtml(t.description || '') + '">' +
      app.escapeHtml(t.name) +
    '</span>'
  ).join('');
}

// ════════════════════════════════════════════════════════════
//  渲染：安全统计
// ════════════════════════════════════════════════════════════

function renderGuardStats(stats) {
  const el = document.getElementById('pc-guard-stats');
  if (!el) return;
  el.textContent = '关键词: ' + ((stats.keywords && (stats.keywords.critical + stats.keywords.warning)) || 0) + ' 条';
}

function renderPermStats(stats) {
  const el = document.getElementById('pc-perm-stats');
  if (!el) return;
  if (!stats) {
    el.textContent = '-';
    return;
  }
  el.textContent = '待确认: ' + (stats.pending_confirm || 0) + ' | 备份: ' + (stats.backup_count || 0);
}

// ════════════════════════════════════════════════════════════
//  交互：紧急控制
// ════════════════════════════════════════════════════════════

async function emergencyAction(action) {
  const confirmMessages = {
    'stop': '⚠️ 确定要紧急停止智能体吗？这将终止当前所有操作。',
    'pause': '',
    'network_block': '',
    'reset': '🔄 确定要重置操作追踪器吗？这会清除所有状态。',
    'cancel': '⏹ 确定要取消当前操作吗？',
  };

  if (confirmMessages[action] && !confirm(confirmMessages[action])) {
    return;
  }

  try {
    const data = await app.post('/api/permission/emergency', { action: action });

    if (data.ok) {
      app.showToast(data.message || '✅ 操作成功', 'info');
      // 刷新状态
      loadPermissionStatus();
      loadPermissionAccessLog();
    } else {
      app.showToast('❌ ' + (data.error || '操作失败'), 'error');
    }
  } catch (e) {
    app.showToast('❌ 请求失败', 'error');
  }
}

// ════════════════════════════════════════════════════════════
//  交互：访问日志筛选
// ════════════════════════════════════════════════════════════

function setAccessFilter(type) {
  _pcState.accessFilter = type;

  // 更新标签高亮
  document.querySelectorAll('.pc-filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === type);
  });

  loadPermissionAccessLog();
}

// ════════════════════════════════════════════════════════════
//  加载完整统计（展开详细时调用）
// ════════════════════════════════════════════════════════════

function expandPermissionStats() {
  loadPermissionStats();

  const toolSection = document.getElementById('pc-tools-section');
  if (toolSection) {
    toolSection.style.display = toolSection.style.display === 'none' ? 'block' : 'none';
  }
}
