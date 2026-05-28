// ════════════════════════════════════════════════════════════
// 灵犀 · 系统全景仪表盘
// ════════════════════════════════════════════════════════════

let _panoActiveSection = 'pipeline';
let _panoDetailState = { 1: false, 2: false, 3: false, 4: false };

// ── 左侧导航切换 ──
function switchPanoSection(section) {
  _panoActiveSection = section;
  document.querySelectorAll('.pano-nav-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`.pano-nav-item[data-pano-section="${section}"]`).classList.add('active');

  document.querySelectorAll('.pano-content > div[id^="pano-section-"]').forEach(el => el.style.display = 'none');
  const target = document.getElementById(`pano-section-${section}`);
  if (target) target.style.display = 'block';
}

// ── 流水线点击滚动到对应卡片 ──
function scrollToPhase(phase) {
  const card = document.getElementById(`pano-card-${phase}`);
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // 切换到架构全景视图
  switchPanoSection('pipeline');
}

// ── 卡片详情展开/折叠 ──
function togglePanoDetail(phase) {
  const detail = document.getElementById(`pano-detail-${phase}`);
  const btn = document.querySelector(`#pano-card-${phase} .expand-btn`);
  if (!detail) return;
  const isOpen = _panoDetailState[phase];
  if (isOpen) {
    detail.style.display = 'none';
    _panoDetailState[phase] = false;
    if (btn) btn.textContent = '展开 ▸';
  } else {
    detail.style.display = 'block';
    _panoDetailState[phase] = true;
    if (btn) btn.textContent = '收起 ▾';
    loadPanoDetail(phase);
  }
}

// ── 加载全景数据 ──
async function loadPanorama() {
  try {
    const r = await fetch('/api/panorama');
    const d = await r.json();

    // ── 全局健康条 ──
    const healthMap = {};
    (d.health || []).forEach(m => {
      const key = m.sensor_name || '';
      healthMap[key] = m;
    });

    function setHealthItem(elId, sensorKey) {
      const item = document.getElementById(elId);
      if (!item) return;
      const data = healthMap[sensorKey];
      if (data) {
        const valEl = item.querySelector('.hi-value');
        if (valEl) valEl.textContent = data.value + (data.unit || '');
        item.className = `health-item status-${data.severity || 'normal'}`;
      }
    }
    setHealthItem('health-cpu', 'cpu_usage');
    setHealthItem('health-memory', 'memory_usage');
    setHealthItem('health-disk', 'disk_usage');
    setHealthItem('health-battery', 'battery');
    setHealthItem('health-network', 'network');

    // ── 阶段一指标 ──
    document.getElementById('pano-cpu-val').textContent = healthMap['cpu_usage'] ? healthMap['cpu_usage'].value + '%' : '-';
    document.getElementById('pano-mem-val').textContent = healthMap['memory_usage'] ? healthMap['memory_usage'].value + '%' : '-';
    document.getElementById('pano-sensor-val').textContent = '📡 ' + (d.sensor_on||0) + '/' + (d.sensor_total||0);

    // ── 阶段二指标 ──
    const cogEl = document.getElementById('pano-cog-status');
    if (cogEl) {
      if (d.cognitive_summary) {
        cogEl.textContent = '活跃';
        cogEl.style.color = '#3fb950';
      } else {
        cogEl.textContent = '待机';
        cogEl.style.color = '#8b949e';
      }
    }
    const rejectEl = document.getElementById('pano-reject-status');
    if (rejectEl) {
      if (d.can_accept) {
        rejectEl.innerHTML = '✓ 可执行';
        rejectEl.style.color = '#3fb950';
      } else {
        rejectEl.innerHTML = '✗ 拒绝中';
        rejectEl.style.color = '#f85149';
      }
    }

    // ── 阶段三指标 ──
    document.getElementById('pano-msg-count').textContent = d.message_count != null ? d.message_count : '-';
    document.getElementById('pano-log-count').textContent = d.log_count != null ? d.log_count : '-';
    document.getElementById('pano-summary-ver').textContent = d.summary_version || '无';
    document.getElementById('pano-compress-info').textContent = Math.round((d.compress_threshold||0.8)*100) + '% / ' + (d.token_limit||4096);

    // ── 阶段四指标 ──
    document.getElementById('pano-mode').textContent = d.mode_label || '-';
    document.getElementById('pano-tools').textContent = (d.tool_count || 0) + ' 个';
    document.getElementById('pano-reflections').textContent = (d.reflection_count || 0) + ' 条';
    const llmEl = document.getElementById('pano-llm');
    if (llmEl) {
      llmEl.textContent = d.llm_configured ? '已连接' : '未配置';
      llmEl.style.color = d.llm_configured ? '#3fb950' : '#8b949e';
    }

    // ── 流水线节点状态着色 ──
    const pipeNodes = document.querySelectorAll('.pipe-node .pn-status');
    if (pipeNodes.length >= 4) {
      // 阶段1
      if (healthMap['cpu_usage'] && healthMap['cpu_usage'].severity === 'critical') {
        pipeNodes[0].style.color = '#f85149';
      }
      // 阶段2
      if (healthMap['memory_usage'] && healthMap['memory_usage'].severity !== 'normal') {
        pipeNodes[1].style.color = '#d29922';
      }
    }

    // ── 系统总览 ──
    document.getElementById('pano-session').textContent = d.session_id || '-';
    document.getElementById('pano-interactions').textContent = (d.interaction_count || 0) + ' 次';
    if (d.started_at) {
      const secs = Math.floor((Date.now() - new Date(d.started_at)) / 1000);
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      document.getElementById('pano-uptime').textContent = m + ' 分 ' + s + ' 秒';
    }
    document.getElementById('pano-sensor-total').textContent = (d.sensor_on||0) + '/' + (d.sensor_total||0);

    const badge2 = document.getElementById('pano-mode-badge-2');
    if (badge2) {
      badge2.textContent = d.mode_label || '正常';
      badge2.className = 'status-badge ' + (d.mode || 'normal');
    }

    // ── 事件流 ──
    loadEvents(d);

    // ── 加载已展开的详情 ──
    for (const [phase, isOpen] of Object.entries(_panoDetailState)) {
      if (isOpen) loadPanoDetail(parseInt(phase));
    }
  } catch(e) {
    console.error('Panorama load error:', e);
  }
}

// ── 加载阶段详情（已展开时） ──
async function loadPanoDetail(phase) {
  try {
    const r = await fetch('/api/panorama');
    const d = await r.json();

    if (phase === 1) {
      const cats = d.sensor_categories || [];
      document.getElementById('pano-sensor-categories').innerHTML = cats.length
        ? cats.map(function(c) { return '<div style="margin-bottom:4px"><strong>' + c.name + '</strong> (' + c.count + ')</div>'; }).join('')
        : '暂无数据';
      const tags = d.tag_dimensions || [];
      document.getElementById('pano-tag-grid').innerHTML = tags.length
        ? tags.map(function(t) { return '<div>' + t.label + ': ' + t.values.join('、') + '</div>'; }).join('')
        : '暂无标签';
    }
    else if (phase === 2) {
      document.getElementById('pano-translate-rules').innerHTML =
        (d.translate_rules || []).map(function(r) { return '<div><strong>' + r.name + '</strong>: ' + r.message + '</div>'; }).join('') || '暂无翻译规则';
      document.getElementById('pano-prompt-preview').textContent = d.prompt_template || '暂无模板';
    }
    else if (phase === 3) {
      document.getElementById('pano-summary-text').textContent = d.summary_text || '（无摘要）';
      const logs = d.log_stats || {};
      const logHtml = Object.entries(logs).map(function(e) { return e[0] + ': ' + e[1] + ' 次'; }).join(' | ');
      document.getElementById('pano-log-stats').innerHTML = logHtml || '暂无日志';
    }
    else if (phase === 4) {
      const modes = d.behavior_modes || [];
      document.getElementById('pano-mode-list').innerHTML = modes.length
        ? modes.map(function(m) { return '<span style="display:inline-block;padding:2px 8px;margin:2px;background:#0d1117;border-radius:4px;font-size:11px;color:' + m.color + '">' + m.label + '</span>'; }).join('')
        : '暂无模式';
      document.getElementById('pano-tool-list').innerHTML =
        (d.tool_list || []).map(function(t) { return '<div style="font-size:11px;color:#8b949e">🔧 ' + t.name + ': ' + t.desc + '</div>'; }).join('') || '无工具';
    }
  } catch(e) {
    console.error('Pano detail load error:', e);
  }
}

// ── 事件流 ──
function loadEvents(d) {
  const body = document.getElementById('pano-events-body');
  if (!body) return;
  const events = [];

  // 从现有数据构建事件
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  if (d.mode) {
    events.push({ time: timeStr, icon: '🎯', text: '行为模式: ' + (d.mode_label || d.mode), tag: 'info', tagClass: 'ok' });
  }
  if (d.llm_configured) {
    events.push({ time: timeStr, icon: '🧠', text: 'LLM 已连接', tag: 'ok', tagClass: 'ok' });
  } else {
    events.push({ time: timeStr, icon: '⚠️', text: 'LLM 未配置', tag: 'warn', tagClass: 'warn' });
  }
  if (d.health) {
    const warnings = d.health.filter(function(h) { return h.severity === 'warning' || h.severity === 'critical'; });
    warnings.forEach(function(w) {
      events.push({ time: timeStr, icon: '🔴', text: (w.description || w.sensor_name) + ': ' + w.value + (w.unit||''), tag: w.severity === 'critical' ? '异常' : '告警', tagClass: w.severity === 'critical' ? 'err' : 'warn' });
    });
  }

  if (events.length === 0) {
    body.innerHTML = '<div class="pano-empty">暂无事件</div>';
    return;
  }

  body.innerHTML = events.map(function(e) {
    return '<div class="event-item">' +
      '<span class="ei-time">' + e.time + '</span>' +
      '<span class="ei-icon">' + e.icon + '</span>' +
      '<span class="ei-text">' + e.text + '</span>' +
      '<span class="ei-tag ' + e.tagClass + '">' + e.tag + '</span>' +
    '</div>';
  }).join('');
}
