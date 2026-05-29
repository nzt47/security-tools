// ════════════════════════════════════════════════════════════
// 灵犀 · 系统全景仪表盘
// ════════════════════════════════════════════════════════════

// ── 左侧导航切换 ──
function switchPanoSection(section) {
  document.querySelectorAll('.pano-nav-item').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`.pano-nav-item[data-pano-section="${section}"]`);
  if (navItem) navItem.classList.add('active');

  // 隐藏仪表盘
  const dashboard = document.getElementById('pano-section-dashboard');
  if (dashboard) dashboard.style.display = 'none';

  // 隐藏所有详情视图
  document.querySelectorAll('.pano-detail-view').forEach(el => el.style.display = 'none');

  // 显示目标面板
  if (section === 'dashboard') {
    if (dashboard) dashboard.style.display = 'block';
  } else {
    const target = document.getElementById('pano-section-' + section);
    if (target) {
      target.style.display = 'block';
      target.classList.add('active');
    }
  }

  loadPanorama();
}

// ── 加载全景数据 ──
async function loadPanorama() {
  try {
    const r = await fetch('/api/panorama');
    const d = await r.json();

    const healthMap = {};
    (d.health || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    // ── 仪表盘总览：4 阶段卡片指标 ──
    setText('pano-cpu-val', healthMap['cpu_usage'] ? healthMap['cpu_usage'].value + '%' : '-');
    setText('pano-mem-val', healthMap['memory_usage'] ? healthMap['memory_usage'].value + '%' : '-');
    setText('pano-sensor-val', '📡 ' + (d.sensor_on||0) + '/' + (d.sensor_total||0));
    setText('pano-battery-val', healthMap['battery'] ? healthMap['battery'].value + '%' : '-');

    // 认知
    const cogEl = document.getElementById('pano-cog-status');
    if (cogEl) {
      cogEl.textContent = d.cognitive_summary ? '活跃' : '待机';
      cogEl.style.color = d.cognitive_summary ? '#3fb950' : '#8b949e';
    }
    const rejectEl = document.getElementById('pano-reject-status');
    if (rejectEl) {
      rejectEl.textContent = d.can_accept ? '✓ 可执行' : '✗ 拒绝中';
      rejectEl.style.color = d.can_accept ? '#3fb950' : '#f85149';
    }

    // 记忆
    setText('pano-msg-count', d.message_count != null ? d.message_count : '-');
    setText('pano-log-count', d.log_count != null ? d.log_count : '-');
    setText('pano-summary-ver', d.summary_version || '无');
    setText('pano-compress-info', Math.round((d.compress_threshold||0.8)*100) + '% / ' + (d.token_limit||4096));

    // 行动
    setText('pano-mode', d.mode_label || '-');
    setText('pano-tools', (d.tool_count || 0) + ' 个');
    setText('pano-reflections', (d.reflection_count || 0) + ' 条');
    const llmEl = document.getElementById('pano-llm');
    if (llmEl) {
      llmEl.textContent = d.llm_configured ? '已连接' : '未配置';
      llmEl.style.color = d.llm_configured ? '#3fb950' : '#8b949e';
    }

    // ── 系统总览条 ──
    setText('pano-session', d.session_id || '-');
    setText('pano-interactions', (d.interaction_count || 0) + ' 次');
    setText('pano-sensor-total', (d.sensor_on||0) + '/' + (d.sensor_total||0));
    if (d.started_at) {
      const secs = Math.floor((Date.now() - new Date(d.started_at)) / 1000);
      setText('pano-uptime', Math.floor(secs/60) + ' 分 ' + (secs%60) + ' 秒');
    }
    const badge2 = document.getElementById('pano-mode-badge-2');
    if (badge2) {
      badge2.textContent = d.mode_label || '正常';
      badge2.className = 'status-badge ' + (d.mode || 'normal');
    }

    // ── 事件流 ──
    loadEvents(d);

    // ── 加载单层详情（如果当前在某详情视图） ──
    const activeDetail = document.querySelector('.pano-detail-view.active');
    if (activeDetail) {
      const sectionId = activeDetail.id;
      if (sectionId.includes('phase')) {
        const phase = parseInt(sectionId.replace('pano-section-phase', ''));
        if (phase >= 1 && phase <= 4) loadPhaseDetail(phase, d);
      } else if (sectionId === 'pano-section-trace') {
        loadTraceDetail(d);
      } else if (sectionId === 'pano-section-system') {
        loadSystemDetail(d);
      }
    }

  } catch(e) { console.error('Panorama load error:', e); }
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ── 加载阶段详情 ──
function loadPhaseDetail(phase, d) {
  if (!d) return;

  if (phase === 1) {
    // 构建 sensor_name → tags 映射（优先从 sensor_categories，其次从 health 读数）
    var sensorTagMap = {};
    (d.sensor_categories || []).forEach(function(c) {
      (c.sensors || []).forEach(function(s) {
        if (s.key && s.tags && s.tags.length > 0) sensorTagMap[s.key] = s.tags;
      });
    });
    // health 数据中的标签作为补充
    (d.health || []).forEach(function(m) {
      if (m.sensor_name && m.tags && !sensorTagMap[m.sensor_name]) sensorTagMap[m.sensor_name] = m.tags;
    });

    // 存储到全局变量供 onclick 使用
    window._sensorTagMap = sensorTagMap;
    window._tagDimensions = d.tag_dimensions || [];

    const healthEl = document.getElementById('pano-health');
    if (healthEl) {
      healthEl.innerHTML = (d.health || []).map(m => {
        let cls = 'pm-norm';
        if (m.severity === 'warning') cls = 'pm-warn';
        else if (m.severity === 'critical') cls = 'pm-crit';
        return `<div class="pano-metric ${cls}"><div class="pm-val">${m.value}${m.unit||''}</div><div class="pm-label">${m.description||m.sensor_name}</div></div>`;
      }).join('');
    }
    setText('pano-sensor-count', (d.sensor_on||0) + '/' + (d.sensor_total||0));

    var catsEl = document.getElementById('pano-sensor-categories');
    if (catsEl) {
      catsEl.innerHTML = (d.sensor_categories || []).map(function(c) {
        var sensorsHtml = (c.sensors || []).map(function(s) {
          return '<span class="pano-sensor-chip' + (s.enabled ? '' : ' disabled') + '" data-sensor-key="' + (s.key||'') + '" onclick="highlightSensorDimensions(\'' + (s.key||'') + '\')" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;background:#0d1117;border-radius:3px;font-size:10px;margin:2px;cursor:pointer" title="点击查看所属维度"><span style="width:5px;height:5px;border-radius:50%;background:' + (s.enabled?'#3fb950':'#30363d') + '"></span>' + (s.name||'') + '</span>';
        }).join('');
        return '<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:10px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><strong style="font-size:13px;color:#c9d1d9">' + c.name + '</strong><span style="font-size:11px;color:#8b949e">' + c.count + ' 个</span></div><div style="font-size:10px;color:#8b949e;margin-bottom:4px">📌 ' + c.source + '</div><div style="display:flex;flex-wrap:wrap;gap:2px">' + sensorsHtml + '</div></div>';
      }).join('') || '<div class="pano-text">暂无数据</div>';
    }

    var tagsEl = document.getElementById('pano-tag-grid');
    if (tagsEl) {
      tagsEl.innerHTML = (d.tag_dimensions || []).map(function(t, idx) {
        var valsHtml = t.values.map(function(v) {
          return '<span class="tag-val" data-dim-idx="' + idx + '" data-tag-val="' + v + '">' + v + '</span>';
        }).join('');
        return '<div class="pano-tag-item" data-dim-idx="' + idx + '"><span class="tag-dim">' + t.label + '</span><span class="tag-vals">' + valsHtml + '</span></div>';
      }).join('');
    }
  }
  else if (phase === 2) {
    var cogEl2 = document.getElementById('pano-cognitive');
    if (cogEl2) cogEl2.textContent = d.cognitive_summary || '暂无感知数据';
    var rejectEl2 = document.getElementById('pano-reject-status-detail');
    if (rejectEl2) {
      rejectEl2.innerHTML = d.can_accept ? '<span style="font-size:11px;color:#3fb950">✓ 可执行任务</span>' : '<span style="font-size:11px;color:#f85149">✗ 拒绝中</span>';
    }
    const trEl = document.getElementById('pano-translate-rules');
    if (trEl) trEl.innerHTML = (d.translate_rules || []).map(r => `<div style="margin-bottom:4px"><strong>${r.name}</strong>: ${r.message}</div>`).join('') || '暂无翻译规则';
    const ppEl = document.getElementById('pano-prompt-preview');
    if (ppEl) ppEl.textContent = d.prompt_template || '暂无模板';
  }
  else if (phase === 3) {
    setText('pano-summary-ver-detail', d.summary_version || '无');
    setText('pano-msg-count-detail', d.message_count != null ? d.message_count + ' 条' : '-');
    setText('pano-log-count-detail', d.log_count != null ? d.log_count + ' 条' : '-');
    const stEl = document.getElementById('pano-summary-text');
    if (stEl) stEl.textContent = d.summary_text || '（无摘要）';
    const lsEl = document.getElementById('pano-log-stats');
    if (lsEl) {
      const logs = d.log_stats || {};
      lsEl.innerHTML = Object.entries(logs).map(([k,v]) => `${k}: ${v} 次`).join(' | ') || '暂无日志';
    }
    const cidEl = document.getElementById('pano-compress-info-detail');
    if (cidEl) cidEl.innerHTML = `压缩触发阈值: ${d.compress_threshold || '-'} | Token 限制: ${d.token_limit || '-'}`;
  }
  else if (phase === 4) {
    setText('pano-mode-detail', d.mode_label || '-');
    setText('pano-tools-detail', (d.tool_count || 0) + ' 个');
    setText('pano-reflections-detail', (d.reflection_count || 0) + ' 条');
    const llmEl2 = document.getElementById('pano-llm-detail');
    if (llmEl2) {
      llmEl2.textContent = d.llm_configured ? '已连接' : '未配置';
      llmEl2.style.color = d.llm_configured ? '#3fb950' : '#8b949e';
    }
    const mlEl = document.getElementById('pano-mode-list');
    if (mlEl) {
      mlEl.innerHTML = (d.behavior_modes || []).map(m => `<div class="pano-mode-item"><span class="mm-name" style="color:${m.color}">${m.label}</span><span class="mm-desc">${m.desc}</span></div>`).join('');
    }
    const tlEl = document.getElementById('pano-tool-list');
    if (tlEl) tlEl.innerHTML = (d.tool_list || []).map(t => `<div style="font-size:11px;color:#8b949e">🔧 ${t.name}: ${t.desc}</div>`).join('') || '无工具';
    const permEl = document.getElementById('pano-perm-info');
    if (permEl) {
      const perm = d.permission_info || {};
      permEl.innerHTML = `检查次数: ${perm.check_count||0} | 已备份: ${perm.backup_count||0} 个文件 | 备份目录: ${perm.backup_dir||'-'}`;
    }
  }
}

// ── 传感器维度高亮 ──
var _selectedSensor = null;

function highlightSensorDimensions(sensorKey) {
  // 切换选中状态
  if (_selectedSensor === sensorKey) {
    _selectedSensor = null;
    clearDimensionHighlight();
    return;
  }
  _selectedSensor = sensorKey;

  // 清除旧的选中/高亮
  document.querySelectorAll('.pano-sensor-chip.selected').forEach(function(el) { el.classList.remove('selected'); });
  document.querySelectorAll('.tag-val.highlighted').forEach(function(el) { el.classList.remove('highlighted'); });
  document.querySelectorAll('.pano-tag-item.has-match').forEach(function(el) { el.classList.remove('has-match'); });

  // 标记当前选中的传感器
  var chip = document.querySelector('.pano-sensor-chip[data-sensor-key="' + sensorKey + '"]');
  if (chip) chip.classList.add('selected');

  // 获取该传感器的标签
  var sensorTagMap = window._sensorTagMap || {};
  var sensorTags = sensorTagMap[sensorKey] || [];

  // 前缀匹配（health sensor_name 可能不同）
  if (sensorTags.length === 0) {
    Object.keys(sensorTagMap).forEach(function(name) {
      if (name.indexOf(sensorKey + '_') === 0 || name === sensorKey) {
        sensorTags = sensorTagMap[name];
      }
    });
  }

  if (sensorTags.length === 0) return;

  // 高亮每个维度中匹配的具体值
  sensorTags.forEach(function(tagVal) {
    var matches = document.querySelectorAll('.tag-val[data-tag-val="' + tagVal + '"]');
    matches.forEach(function(el) {
      el.classList.add('highlighted');
      // 同时标记父级维度
      var dimItem = el.closest('.pano-tag-item');
      if (dimItem) dimItem.classList.add('has-match');
    });
  });
}

function clearDimensionHighlight() {
  _selectedSensor = null;
  document.querySelectorAll('.pano-sensor-chip.selected').forEach(function(el) { el.classList.remove('selected'); });
  document.querySelectorAll('.tag-val.highlighted').forEach(function(el) { el.classList.remove('highlighted'); });
  document.querySelectorAll('.pano-tag-item.has-match').forEach(function(el) { el.classList.remove('has-match'); });
}

function loadTraceDetail(d) {
  const body = document.getElementById('pano-trace-body');
  if (!body) return;
  if (d && d.last_trace && d.last_trace.length) {
    body.innerHTML = d.last_trace.map(t =>
      `<div class="trace-step"><span class="ts-phase ts-p${t.phase}">${t.phase_label}</span><span class="ts-icon">${t.icon}</span><span class="ts-text">${t.text}</span></div>`
    ).join('');
  } else {
    body.innerHTML = '<div class="pano-text sub">暂无交互记录</div>';
  }
}

function loadSystemDetail(d) {
  if (!d) return;
  setText('pano-session-detail', d.session_id || '-');
  setText('pano-interactions-detail', (d.interaction_count || 0) + ' 次');
  setText('pano-sensor-total-detail', (d.sensor_on||0) + '/' + (d.sensor_total||0));
  if (d.started_at) {
    const secs = Math.floor((Date.now() - new Date(d.started_at)) / 1000);
    setText('pano-uptime-detail', Math.floor(secs/60) + ' 分 ' + (secs%60) + ' 秒');
  }
  const badge = document.getElementById('pano-mode-badge-detail');
  if (badge) {
    badge.textContent = d.mode_label || '正常';
    badge.className = 'status-badge ' + (d.mode || 'normal');
  }
}

// ── 事件流 ──
function loadEvents(d) {
  const body = document.getElementById('pano-events-body');
  if (!body) return;
  const events = [];
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
    d.health.filter(h => h.severity === 'warning' || h.severity === 'critical').forEach(w => {
      events.push({ time: timeStr, icon: '🔴', text: (w.description || w.sensor_name) + ': ' + w.value + (w.unit||''), tag: w.severity === 'critical' ? '异常' : '告警', tagClass: w.severity === 'critical' ? 'err' : 'warn' });
    });
  }

  if (events.length === 0) {
    body.innerHTML = '<div class="pano-empty">暂无事件</div>';
    return;
  }
  body.innerHTML = events.map(e =>
    '<div class="event-item"><span class="ei-time">' + e.time + '</span><span class="ei-icon">' + e.icon + '</span><span class="ei-text">' + e.text + '</span><span class="ei-tag ' + e.tagClass + '">' + e.tag + '</span></div>'
  ).join('');
}

// 通过事件总线实现全景自动刷新
app.on('tick', () => {
  const panoView = document.getElementById('view-panorama');
  if (panoView && panoView.classList.contains('active')) {
    loadPanorama();
  }
});
