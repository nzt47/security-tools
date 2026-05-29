// ════════════════════════════════════════════════════════════
// 灵犀 · 记忆管理 — 记忆 + 窗口活动
// ════════════════════════════════════════════════════════════

// ── 子页切换 ──
function switchMemoryTab(tabName) {
  document.querySelectorAll('.memory-subtab').forEach(function(t) { t.classList.remove('active'); });
  let tab = document.querySelector('.memory-subtab[data-memory-tab="' + tabName + '"]');
  if (tab) tab.classList.add('active');

  document.querySelectorAll('.memory-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.window-panel').forEach(function(p) { p.classList.remove('active'); });

  if (tabName === 'memory') {
    let panel = document.getElementById('memory-panel-content');
    if (panel) panel.classList.add('active');
    loadMemoryData();
  } else if (tabName === 'window') {
    let panel = document.getElementById('window-panel-content');
    if (panel) panel.classList.add('active');
    loadWindowActivity();
  }
}

// ── 加载记忆数据 ──
async function loadMemory() {
  switchMemoryTab('memory');
}

async function loadMemoryData() {
  try {
    let r = await fetch('/api/memory/overview');
    let d = await r.json();
    renderMemoryOverview(d);
    renderMemoryContent(d);
  } catch(e) { console.error('Memory load error:', e); }
}

function renderMemoryOverview(d) {
  let el = document.getElementById('memory-overview');
  if (!el) return;
  el.innerHTML =
    '<div style="display:flex;gap:6px;margin-bottom:10px">' +
      '<div class="view-card" style="flex:1;text-align:center">' +
        '<div style="font-size:18px;font-weight:700;color:#c9d1d9">' + (d.message_count||0) + '</div>' +
        '<div style="font-size:9px;color:#8b949e">短期记忆</div>' +
      '</div>' +
      '<div class="view-card" style="flex:1;text-align:center">' +
        '<div style="font-size:18px;font-weight:700;color:#3fb950">v' + (d.summary_version||0) + '</div>' +
        '<div style="font-size:9px;color:#8b949e">摘要版本</div>' +
      '</div>' +
    '</div>';
}

function renderMemoryContent(d) {
  let el = document.getElementById('memory-content');
  if (!el) return;

  let msgs = d.recent_messages || [];
  let msgHtml = msgs.length > 0
    ? '<div style="font-size:9px;color:#8b949e;margin-bottom:4px">📋 最近消息</div>' +
      msgs.slice(0, 10).map(function(m, i) {
        return '<div class="view-card" style="padding:6px 8px">' +
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<span class="badge ' + (m.role==='user'?'info':'on') + '" style="font-size:8px">' + (m.role||'?') + '</span>' +
            '<button class="btn-sm" style="font-size:9px;padding:1px 6px" onclick="delet eMemory(' + i + ')">✕</button>' +
          '</div>' +
          '<div style="font-size:10px;color:#c9d1d9;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + (m.content||'').substring(0, 80) + '</div>' +
        '</div>';
      }).join('')
    : '<div class="view-empty">暂无消息</div>';

  let summaryText = d.summary_text || '';
  let summaryHtml = summaryText
    ? '<div style="font-size:9px;color:#8b949e;margin:8px 0 4px">📄 长期摘要</div>' +
      '<div class="view-card" style="font-size:10px;color:#c9d1d9;line-height:1.4;max-height:80px;overflow-y:auto">' + summaryText.substring(0, 300) + '</div>'
    : '';

  let logs = d.log_stats || {};
  let logHtml = Object.keys(logs).length > 0
    ? '<div style="font-size:9px;color:#8b949e;margin:8px 0 4px">📊 日志统计</div>' +
      Object.entries(logs).map(function(e) {
        return '<div style="font-size:10px;color:#8b949e;display:flex;justify-content:space-between;padding:2px 0"><span>' + e[0] + '</span><span style="color:#c9d1d9">' + e[1] + ' 次</span></div>';
      }).join('')
    : '';

  el.innerHTML = msgHtml + summaryHtml + logHtml;
}

// ── 窗口活动 ──
let _windowRefreshTimer = null;

async function loadWindowActivity() {
  try {
    let r = await fetch('/api/memory/windows/current');
    let current = await r.json();
    renderCurrentWindow(current);

    let r2 = await fetch('/api/memory/windows/events?limit=50');
    let events = await r2.json();
    renderWindowEvents(events.events || []);

    let r3 = await fetch('/api/memory/windows/stats');
    let stats = await r3.json();
    renderWindowStats(stats.apps || []);

    let r4 = await fetch('/api/memory/windows/config');
    let config = await r4.json();
    renderWindowStatus(config.enabled !== false);
  } catch(e) { console.error('Window activity load error:', e); }

  if (_windowRefreshTimer) clearInterval(_windowRefreshTimer);
  _windowRefreshTimer = setInterval(function() {
    let panel = document.getElementById('window-panel-content');
    if (panel && panel.classList.contains('active')) {
      refreshWindowData();
    } else {
      clearInterval(_windowRefreshTimer);
      _windowRefreshTimer = null;
    }
  }, 10000);
}

async function refreshWindowData() {
  try {
    let r = await fetch('/api/memory/windows/current');
    let current = await r.json();
    renderCurrentWindow(current);
    let r2 = await fetch('/api/memory/windows/events?limit=50');
    let events = await r2.json();
    renderWindowEvents(events.events || []);
  } catch(e) {}
}

function renderCurrentWindow(current) {
  let el = document.getElementById('window-current');
  if (!el) return;
  if (current && current.process) {
    el.innerHTML =
      '<div class="wc-label">🪟 ' + (current.is_idle ? '空闲中' : '当前活跃窗口') + '</div>' +
      '<div class="wc-title">' + (current.title || current.process) + '</div>' +
      '<div class="wc-process">' + current.process + ' · 已持续 ' + formatDuration(current.elapsed_sec || 0) + '</div>';
  } else {
    el.innerHTML =
      '<div class="wc-label">🪟 当前活跃窗口</div>' +
      '<div class="wc-title" style="color:#8b949e">无数据</div>' +
      '<div class="wc-process">等待窗口事件...</div>';
  }
}

function renderWindowEvents(events) {
  let el = document.getElementById('window-events');
  if (!el) return;
  if (!events || events.length === 0) {
    el.innerHTML = '<div class="view-empty">暂无切换事件</div>';
    return;
  }
  let colors = ['#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#f85149', '#8b949e'];
  let colorIdx = 0;
  let procColors = {};
  function getColor(proc) {
    if (!proc) return colors[5];
    if (!procColors[proc]) { procColors[proc] = colors[(colorIdx++) % colors.length]; }
    return procColors[proc];
  }

  el.innerHTML = events.slice(0, 50).map(function(ev) {
    let d = ev.data || {};
    let time = (ev.timestamp || '').substring(11, 16) || '--:--';
    let isIdle = d.action === 'idle_start' || d.action === 'idle_end';
    let fromName = (d.from_process || '?').replace('.exe','');
    let toName = (d.to_process || '?').replace('.exe','');
    return '<div class="window-event-item' + (isIdle ? ' we-idle' : '') + '">' +
      '<span class="we-time">' + time + '</span>' +
      '<span style="color:#8b949e">' + fromName + '</span>' +
      '<span class="we-arrow">→</span>' +
      '<span class="we-to" style="color:' + getColor(d.to_process) + '">' + toName + '</span>' +
      '<span class="we-duration">' + formatDuration(d.duration_sec || 0) + '</span>' +
    '</div>';
  }).join('');
}

function renderWindowStats(apps) {
  let el = document.getElementById('window-stats');
  if (!el) return;
  if (!apps || apps.length === 0) {
    el.innerHTML = '<div class="view-empty">暂无统计数据</div>';
    return;
  }
  let barColors = ['#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#f85149', '#8b949e'];
  el.innerHTML = apps.slice(0, 10).map(function(a, i) {
    let pct = a.percentage || 0;
    return '<div class="window-stat-item">' +
      '<span class="ws-name" title="' + (a.process||'') + '">' + (a.title || a.process || '?') + '</span>' +
      '<span class="ws-bar-bg"><span class="ws-bar-fill" style="width:' + pct + '%;background:' + barColors[i % barColors.length] + '"></span></span>' +
      '<span class="ws-pct">' + pct + '%</span>' +
    '</div>';
  }).join('');
}

function renderWindowStatus(running) {
  let el = document.getElementById('window-status');
  if (!el) return;
  el.innerHTML =
    '<div class="window-status-indicator">' +
      '<span class="window-status-dot' + (running ? '' : ' stopped') + '"></span>' +
      '<span>' + (running ? '监控运行中' : '监控已停止') + '</span>' +
    '</div>' +
    '<label class="toggle-switch" style="position:relative;width:28px;height:16px;flex-shrink:0">' +
      '<input type="checkbox" ' + (running ? 'checked' : '') + ' onchange="toggleWindowMonitor(this.checked)">' +
      '<span class="slider" style="position:absolute;inset:0;background:#30363d;border-radius:8px;cursor:pointer"></span>' +
      '<span style="position:absolute;width:12px;height:12px;left:' + (running ? '14px' : '2px') + ';bottom:2px;background:#8b949e;border-radius:50%;transition:0.2s"></span>' +
    '</label>';
}

async function toggleWindowMonitor(enabled) {
  try {
    await fetch('/api/memory/windows/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: enabled}),
    });
    renderWindowStatus(enabled);
    app.showToast(enabled ? '窗口监控已开启' : '窗口监控已停止', 'success');
  } catch(e) {
    app.showToast('操作失败', 'error');
  }
}

function showWindowConfig() {
  let html =
    '<p>配置窗口监控参数</p>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">轮询间隔</label>' +
      '<select id="cfg-interval" class="btn-sm" style="width:100%">' +
        '<option value="0.5">0.5 秒</option><option value="1" selected>1 秒</option>' +
        '<option value="2">2 秒</option><option value="5">5 秒</option>' +
      '</select>' +
    '</div>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">最大事件数</label>' +
      '<select id="cfg-maxevents" class="btn-sm" style="width:100%">' +
        '<option value="100">100</option><option value="200">200</option>' +
        '<option value="500" selected>500</option><option value="1000">1000</option>' +
      '</select>' +
    '</div>' +
    '<div class="form-group" style="margin-bottom:10px">' +
      '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">空闲超时</label>' +
      '<select id="cfg-idle" class="btn-sm" style="width:100%">' +
        '<option value="180">3 分钟</option><option value="300" selected>5 分钟</option>' +
        '<option value="600">10 分钟</option><option value="1800">30 分钟</option>' +
      '</select>' +
    '</div>';

  let overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML =
    '<div class="confirm-box">' + html +
      '<div class="sidebar-confirm-actions">' +
        '<button class="btn-sm" onclick="this.closest(\'.confirm-overlay\').remove()">取消</button>' +
        '<button class="btn-sm primary" id="cfg-save-btn" onclick="saveWindowConfig()">保存</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
}

async function saveWindowConfig() {
  let config = {
    poll_interval_sec: parseFloat(document.getElementById('cfg-interval').value),
    max_events: parseInt(document.getElementById('cfg-maxevents').value),
    idle_timeout_sec: parseInt(document.getElementById('cfg-idle').value),
  };
  try {
    let r = await fetch('/api/memory/windows/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config),
    });
    let result = await r.json();
    if (result.ok) {
      document.querySelector('.confirm-overlay').remove();
      app.showToast('配置已保存', 'success');
    } else {
      app.showToast('保存失败: ' + (result.error || ''), 'error');
    }
  } catch(e) {
    app.showToast('保存失败', 'error');
  }
}

async function clearWindowEvents() {
  let confirmed = await app.showConfirm('确定清空所有窗口事件记录？');
  if (!confirmed) return;
  try {
    await fetch('/api/memory/windows/clear', {method: 'POST'});
    renderWindowEvents([]);
    renderWindowStats([]);
    app.showToast('窗口事件已清空', 'success');
  } catch(e) {
    app.showToast('操作失败', 'error');
  }
}

function formatDuration(sec) {
  sec = Math.round(sec || 0);
  if (sec < 60) return sec + '秒';
  if (sec < 3600) return Math.floor(sec/60) + '分' + (sec%60) + '秒';
  return Math.floor(sec/3600) + '时' + Math.floor((sec%3600)/60) + '分';
}

// ── 记忆管理功能 ──
function showAddMemory() {
  let overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML =
    '<div class="confirm-box">' +
      '<p>添加记忆条目</p>' +
      '<div class="form-group" style="margin-bottom:10px">' +
        '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">内容</label>' +
        '<textarea id="new-memory-content" style="width:100%;padding:6px 8px;border-radius:4px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px;resize:none;box-sizing:border-box" rows="3" placeholder="输入记忆内容..."></textarea>' +
      '</div>' +
      '<div class="form-group" style="margin-bottom:10px">' +
        '<label style="display:block;font-size:11px;color:#8b949e;margin-bottom:4px">优先级</label>' +
        '<select id="new-memory-priority" style="width:100%;padding:4px 8px;border-radius:4px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px">' +
          '<option value="low">低</option><option value="normal" selected>普通</option><option value="high">高</option>' +
        '</select>' +
      '</div>' +
      '<div class="sidebar-confirm-actions">' +
        '<button class="btn-sm" onclick="this.closest(\'.confirm-overlay\').remove()">取消</button>' +
        '<button class="btn-sm primary" onclick="confirmAddMemory()">添加</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);
}

async function confirmAddMemory() {
  let content = document.getElementById('new-memory-content').value.trim();
  if (!content) { app.showToast('请输入内容', 'error'); return; }
  let priority = document.getElementById('new-memory-priority').value;
  try {
    await fetch('/api/memory/manual', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: content, priority: priority}),
    });
    document.querySelector('.confirm-overlay').remove();
    app.showToast('记忆已添加', 'success');
    loadMemoryData();
  } catch(e) { app.showToast('添加失败', 'error'); }
}

async function delet eMemory(index) {
  let confirmed = await app.showConfirm('确定删除此记忆？');
  if (!confirmed) return;
  try {
    await fetch('/api/memory/' + index, {method: 'DELETE'});
    app.showToast('记忆已删除', 'success');
    loadMemoryData();
  } catch(e) { app.showToast('删除失败', 'error'); }
}

async function triggerCompression() {
  try {
    let r = await fetch('/api/memory/compress', {method: 'POST'});
    let result = await r.json();
    app.showToast(result.ok ? '压缩完成' : '压缩失败', result.ok ? 'success' : 'error');
    loadMemoryData();
  } catch(e) { app.showToast('压缩失败', 'error'); }
}
