// ════════════════════════════════════════════════════════════
// 云枢 · 右侧状态面板 - 指标 + 进度条 + 事件流
// ════════════════════════════════════════════════════════════

// ── 面板折叠切换 ──
function initStatusPanel() {
  const toggleBtn = document.getElementById('panel-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const appEl = document.getElementById('app');
      const collapsed = appEl.classList.toggle('panel-collapsed');
      app.setState('panelCollapsed', collapsed);
      toggleBtn.textContent = collapsed ? '▶' : '◀';
    });

    // 初始状态更新按钮文字
    if (app.state.panelCollapsed) {
      toggleBtn.textContent = '▶';
    }
  }

  // 立即执行首次更新
  updateStatusPanel();
}

async function updateStatusPanel() {
  try {
    const data = await app.get('/api/health');
    const healthMap = {};
    data.forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function setProgress(elId, barId, sensorKey, unit) {
      const valEl = document.getElementById(elId);
      const barEl = document.getElementById(barId);
      if (!valEl || !barEl) return;
      const d = healthMap[sensorKey];
      if (d) {
        const pct = Math.min(parseFloat(d.value) || 0, 100);
        valEl.textContent = pct + (unit || '');
        valEl.className = 'sp-value ' + (d.severity || 'normal');
        barEl.style.width = pct + '%';
        barEl.className = 'sp-bar-fill ' + (d.severity || 'normal');
      } else {
        valEl.textContent = '-';
        valEl.className = 'sp-value';
        barEl.style.width = '0%';
        barEl.className = 'sp-bar-fill';
      }
    }

    setProgress('sp-cpu', 'sp-cpu-bar', 'cpu_usage', '%');
    setProgress('sp-memory', 'sp-mem-bar', 'memory_usage', '%');
    setProgress('sp-disk', 'sp-disk-bar', 'disk_usage', '%');
    setProgress('sp-battery', 'sp-batt-bar', 'battery', '%');

    function setText(elId, sensorKey) {
      const el = document.getElementById(elId);
      if (!el) return;
      const d = healthMap[sensorKey];
      if (d) {
        el.textContent = d.value + (d.unit || '');
        el.className = 'sp-value ' + (d.severity || 'normal');
      } else {
        el.textContent = '-';
        el.className = 'sp-value';
      }
    }

    setText('sp-network', 'network');

    // 传感器计数
    try {
      const sensors = await app.get('/api/sensors');
      const on = sensors.filter(s => s.enabled).length;
      const spSensors = document.getElementById('sp-sensors');
      if (spSensors) {
        spSensors.textContent = on + '/' + sensors.length;
        spSensors.className = 'sp-value normal';
      }
    } catch(e) {}

    // 模式
    try {
      const mode = await app.get('/api/mode');
      const spMode = document.getElementById('sp-mode');
      if (spMode) {
        spMode.textContent = mode.label || mode.mode || '-';
        spMode.className = 'sp-value normal';
      }
    } catch(e) {}

    // 运行信息
    try {
      const pano = await app.get('/api/panorama');
      setTextById('sp-session', pano.session || '-');
      setTextById('sp-interactions', pano.interactions != null ? String(pano.interactions) : '-');
      setTextById('sp-uptime', pano.uptime || '-');
    } catch(e) {}

    updateEvents(healthMap);
  } catch(e) { console.error('Status panel error:', e); }
}

function setTextById(elId, text) {
  const el = document.getElementById(elId);
  if (el) {
    el.textContent = text;
    el.className = 'sp-value';
  }
}

function updateEvents(healthMap) {
  const el = document.getElementById('sp-events');
  if (!el) return;
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  const events = [];
  Object.entries(healthMap).forEach(([key, m]) => {
    if (m.severity === 'warning' || m.severity === 'critical') {
      events.push({
        time: timeStr,
        text: (m.description || key) + ': ' + m.value + (m.unit||''),
        severity: m.severity
      });
    }
  });

  if (events.length === 0) {
    el.innerHTML = '<div style="text-align:center;padding:10px;color:var(--text-muted);font-size:12px">系统正常</div>';
    return;
  }

  el.innerHTML = events.map(e =>
    '<div class="sp-event">' +
      '<span class="sp-event-time">' + e.time + '</span>' +
      '<span class="sp-event-text" style="color:' + (e.severity==='critical'?'var(--danger)':'var(--warning)') + '">⚠ ' + e.text + '</span>' +
    '</div>'
  ).join('');
}

// 订阅 tick 事件自动刷新
app.on('tick', updateStatusPanel);
