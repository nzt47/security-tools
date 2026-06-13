// ════════════════════════════════════════════════════════════
// 云枢 · 健康看板
// ════════════════════════════════════════════════════════════

const HEALTH_API = '/api/heartbeat';
let historyData = [];
let currentFilter = '';

document.addEventListener('DOMContentLoaded', () => {
  loadStatus();
  loadTasks();
  loadHistory();
  setInterval(() => loadStatus(false), 30000);
});

async function loadStatus(showLoading = true) {
  try {
    const [heartbeat, status] = await Promise.all([
      fetch(HEALTH_API).then(r => r.json()),
      fetch(HEALTH_API + '/status').then(r => r.json()),
    ]);
    updateStatusBar(status, heartbeat);
    updateChecks(heartbeat);
    loadHeartbeatHistory();
  } catch (e) {
    console.error('加载心跳数据失败:', e);
  }
}

function updateStatusBar(status, heartbeat) {
  const overall = status.status || 'unknown';
  const dot = document.getElementById('overall-status-dot');
  dot.className = 'status-dot ' + (overall === 'healthy' ? 'dot-healthy' : overall === 'degraded' ? 'dot-degraded' : 'dot-unhealthy');
  document.getElementById('overall-status').textContent = overall === 'healthy' ? '健康' : overall === 'degraded' ? '亚健康' : '异常';
  document.getElementById('status-timestamp').textContent = status.timestamp ? '上次: ' + status.timestamp : '';
  document.getElementById('total-checks').textContent = status.total_checks ?? '-';
  document.getElementById('healthy-checks').textContent = status.healthy_checks ?? '-';
  const sys = heartbeat.checks?.system || {};
  const cpu = sys.cpu != null ? sys.cpu + '%' : '-';
  const mem = sys.memory != null ? sys.memory + '%' : '-';
  document.getElementById('cpu-memory').textContent = cpu + ' / ' + mem;
  document.getElementById('disk-usage').textContent = sys.disk != null ? '磁盘: ' + sys.disk + '%' : '';
}

function updateChecks(heartbeat) {
  const checks = heartbeat.checks || {};
  const setVal = (id, text, status) => {
    const el = document.getElementById(id);
    if (!el) return;
    const dot = status === 'ok' ? '<span class="status-dot dot-ok"></span>' :
                status === 'warn' ? '<span class="status-dot dot-warn"></span>' :
                '<span class="status-dot dot-error"></span>';
    el.innerHTML = dot + ' ' + text;
  };
  const sys = checks.system || {};
  setVal('check-cpu', sys.cpu != null ? sys.cpu + '%' : 'N/A', sys.status);
  setVal('check-memory', sys.memory != null ? sys.memory + '%' : 'N/A', sys.status);
  setVal('check-disk', sys.disk != null ? sys.disk + '%' : 'N/A', sys.status);
  const llm = checks.llm || {};
  const llmText = llm.status === 'ok' ? (llm.model || '已连接') : (llm.message || llm.error || '未配置');
  setVal('check-llm', llmText, llm.status);
  const memSys = checks.memory || {};
  setVal('check-memory-system', memSys.message || memSys.error || 'N/A', memSys.status);
  const sched = checks.scheduler || {};
  const schedText = sched.running ? '运行中 (' + (sched.tasks ?? 0) + ' 任务)' : '已停止';
  setVal('check-scheduler', schedText, sched.status);
  const thr = checks.threads || {};
  setVal('check-threads', thr.total != null ? thr.total + ' 线程' : 'N/A', thr.status);
}

async function loadHeartbeatHistory() {
  try {
    const resp = await fetch(HEALTH_API + '/history?limit=60');
    const data = await resp.json();
    historyData = (data.history || []).reverse();
    renderChart();
  } catch (e) {
    console.error('加载心跳历史失败:', e);
  }
}

function renderChart() {
  const canvas = document.getElementById('heartbeat-chart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const container = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const rect = container.getBoundingClientRect();
  canvas.width = (rect.width - 32) * dpr;
  canvas.height = 200 * dpr;
  canvas.style.width = (rect.width - 32) + 'px';
  canvas.style.height = '200px';
  ctx.scale(dpr, dpr);
  const w = canvas.width / dpr;
  const h = canvas.height / dpr;
  ctx.clearRect(0, 0, w, h);
  if (historyData.length < 2) {
    ctx.fillStyle = '#8b949e';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('数据不足，等待更多心跳采集...', w / 2, h / 2);
    return;
  }
  const pad = { top: 20, bottom: 30, left: 40, right: 20 };
  const chartW = w - pad.left - pad.right;
  const chartH = h - pad.top - pad.bottom;
  let maxVal = 100;
  const allVals = historyData.flatMap(d => [d.cpu ?? 0, d.memory ?? 0]);
  maxVal = Math.max(100, ...allVals) * 1.1;
  const toX = (i) => pad.left + (i / (historyData.length - 1)) * chartW;
  const toY = (v) => pad.top + chartH - (v / maxVal) * chartH;
  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 1;
  for (let pct = 0; pct <= 100; pct += 25) {
    const y = toY(pct);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
    ctx.fillStyle = '#484f58';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(pct + '%', pad.left - 4, y + 3);
  }
  ctx.fillStyle = '#484f58';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(historyData.length / 6));
  for (let i = 0; i < historyData.length; i += step) {
    const d = historyData[i];
    const label = d.timestamp ? d.timestamp.slice(11, 16) : '';
    ctx.fillText(label, toX(i), h - 5);
  }
  function drawLine(data, color, getVal) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    data.forEach((d, i) => {
      const x = toX(i);
      const y = toY(getVal(d));
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  drawLine(historyData, '#58a6ff', d => d.cpu ?? 0);
  drawLine(historyData, '#bc8cff', d => d.memory ?? 0);
  ctx.strokeStyle = '#f8514955';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(pad.left, toY(90));
  ctx.lineTo(w - pad.right, toY(90));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = '#f8514955';
  ctx.font = '10px sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText('90% 阈值', pad.left + 4, toY(90) - 2);
  historyData.forEach((d, i) => {
    if (d.status === 'unhealthy' || d.status === 'degraded') {
      ctx.fillStyle = '#f85149';
      ctx.beginPath();
      ctx.arc(toX(i), toY(Math.max(d.cpu ?? 0, d.memory ?? 0)), 4, 0, Math.PI * 2);
      ctx.fill();
    }
  });
  ctx.fillStyle = '#8b949e';
  ctx.font = '12px sans-serif';
  ctx.textAlign = 'left';
  ctx.fillRect(w - 140, 8, 12, 3);
  ctx.fillStyle = '#58a6ff';
  ctx.fillText('CPU', w - 125, 12);
  ctx.fillRect(w - 90, 8, 12, 3);
  ctx.fillStyle = '#bc8cff';
  ctx.fillText('内存', w - 75, 12);
}

async function loadTasks() {
  try {
    const resp = await fetch('/api/scheduler/tasks');
    const data = await resp.json();
    const tasks = data.tasks || [];
    const list = document.getElementById('task-list');
    if (tasks.length === 0) {
      list.innerHTML = '<div class="empty-state">暂无计划任务</div>';
      return;
    }
    list.innerHTML = tasks.map(t => `
      <div class="task-item">
        <div class="info">
          <div class="name">${escHtml(t.name)}</div>
          <div class="meta">
            ${t.type === 'system_command' ? '命令: ' + escHtml(t.command || '') : t.type}
            ${t.interval_sec ? ' · 间隔: ' + t.interval_sec + 's' : ''}
            ${t.last_run ? ' · 上次: ' + t.last_run : ''}
          </div>
        </div>
        <div class="actions">
          <label class="toggle-switch small">
            <input type="checkbox" ${t.enabled ? 'checked' : ''} onchange="toggleTask('${escHtml(t.task_id)}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn" onclick="executeNow('${escHtml(t.task_id)}')">▶ 执行</button>
          <button class="btn btn-danger" onclick="deleteTask('${escHtml(t.task_id)}')">✕</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error('加载任务列表失败:', e);
  }
}

async function createTask() {
  const name = document.getElementById('task-name').value.trim();
  const command = document.getElementById('task-command').value.trim();
  const interval = parseInt(document.getElementById('task-interval').value) || 300;
  if (!name || !command) { alert('请填写任务名称和命令'); return; }
  try {
    const resp = await fetch('/api/scheduler/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, command, interval_sec: interval }),
    });
    const result = await resp.json();
    if (result.ok) {
      document.getElementById('task-name').value = '';
      document.getElementById('task-command').value = '';
      loadTasks();
    } else {
      alert('创建失败: ' + (result.error || '未知错误'));
    }
  } catch (e) {
    alert('创建失败: ' + e.message);
  }
}

async function toggleTask(taskId, enabled) {
  try {
    await fetch('/api/scheduler/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: taskId, enabled }),
    });
  } catch (e) {
    console.error('切换任务状态失败:', e);
  }
}

async function executeNow(taskId) {
  try {
    const resp = await fetch('/api/scheduler/execute-now', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: taskId }),
    });
    const result = await resp.json();
    if (result.ok) {
      loadHistory();
    }
  } catch (e) {
    console.error('执行任务失败:', e);
  }
}

async function deleteTask(taskId) {
  if (!confirm('确定删除此任务？')) return;
  try {
    await fetch('/api/scheduler/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: taskId }),
    });
    loadTasks();
  } catch (e) {
    console.error('删除任务失败:', e);
  }
}

async function loadHistory() {
  try {
    const filterParam = currentFilter ? '&type=' + currentFilter : '';
    const resp = await fetch('/api/scheduler/history?limit=100' + filterParam);
    const data = await resp.json();
    const history = data.history || [];
    const list = document.getElementById('history-list');
    if (history.length === 0) {
      list.innerHTML = '<div class="empty-state">暂无执行记录</div>';
      return;
    }
    list.innerHTML = history.map(h => {
      const typeTag = h.type === 'heartbeat' ? '' : '<span class="badge badge-' + (h.status === 'success' ? 'success' : h.status === 'failed' ? 'failed' : 'running') + '">' + h.status + '</span>';
      const duration = h.duration_ms != null ? (h.duration_ms < 1000 ? h.duration_ms + 'ms' : (h.duration_ms / 1000).toFixed(1) + 's') : '';
      return '<div class="history-item"><div class="info"><div><strong>' + escHtml(h.name) + '</strong> ' + typeTag + '</div><div class="meta">' + (h.start_time ? h.start_time.slice(0, 19) : '') + (h.type ? ' · ' + h.type : '') + '</div>' + (h.output ? '<div class="meta" style="color:#8b949e">' + escHtml(h.output.slice(0, 100)) + '</div>' : '') + (h.error ? '<div class="meta" style="color:var(--red)">' + escHtml(h.error.slice(0, 100)) + '</div>' : '') + '</div><span class="meta">' + duration + '</span></div>';
    }).join('');
  } catch (e) {
    console.error('加载执行历史失败:', e);
  }
}

function filterHistory(filter) {
  currentFilter = filter === 'success' ? 'system_command' : filter === 'failed' ? 'system_command' : filter;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === filter));
  loadHistory();
}

function escHtml(s) {
  if (!s) return '';
  var div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

window.addEventListener('resize', function() {
  clearTimeout(window._chartResizeTimer);
  window._chartResizeTimer = setTimeout(renderChart, 200);
});
