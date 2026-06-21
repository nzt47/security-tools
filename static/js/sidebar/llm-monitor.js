// ════════════════════════════════════════════════════════════
// 云枢 · LLM 通信监控视图
// 查看每次 LLM 调用的完整收发内容 + Token 统计
// ════════════════════════════════════════════════════════════

let lmRecords = [];
let lmOffset = 0;
const LM_PAGE_SIZE = 30;
let lmSearchTimer = null;

// ════════════════════════════════════════════════════════════
//  主加载
// ════════════════════════════════════════════════════════════

async function loadLLMMonitor() {
  try {
    const [stats, records] = await Promise.all([
      app.get('/api/llm-monitor/stats'),
      app.get('/api/llm-monitor/records?limit=' + LM_PAGE_SIZE),
    ]);
    renderStats(stats);
    renderRecords(records);
  } catch (e) {
    document.getElementById('lm-records-list').textContent = '加载失败: ' + e.message;
  }
}

// ════════════════════════════════════════════════════════════
//  统计
// ════════════════════════════════════════════════════════════

function renderStats(stats) {
  if (!stats) return;
  setText('lm-stat-total', stats.total || 0);
  setText('lm-stat-req-tok', formatNum((stats.total_request_tokens || 0)));
  setText('lm-stat-res-tok', formatNum((stats.total_response_tokens || 0)));
  setText('lm-stat-avg-ms', stats.avg_duration_ms || 0);
  setText('lm-stat-cost', '$' + (stats.estimated_cost_usd || 0).toFixed(6));
  setText('lm-max-records', stats.max_records || 500);

  // Badge
  const badge = document.getElementById('lm-stats-badge');
  if (badge) badge.textContent = stats.total + ' 条记录 | ' + formatNum(stats.total_tokens || 0) + ' tokens';

  // Recording status
  const status = document.getElementById('lm-recording-status');
  if (status) {
    if (stats.enabled) {
      status.textContent = '● 监控中';
      status.style.color = 'var(--success)';
    } else {
      status.textContent = '○ 已暂停';
      status.style.color = 'var(--text-muted)';
    }
  }

  // Toggle sync
  const toggle = document.getElementById('lm-enabled-toggle');
  if (toggle) toggle.checked = stats.enabled !== false;
}

// ════════════════════════════════════════════════════════════
//  记录列表
// ════════════════════════════════════════════════════════════

function renderRecords(data) {
  const records = data.records || [];
  const total = data.total || 0;
  const container = document.getElementById('lm-records-list');
  if (!container) return;

  if (records.length === 0) {
    container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-muted)">暂无 LLM 通信记录</div>';
    document.getElementById('lm-load-more').style.display = 'none';
    return;
  }

  let html = '';
  for (const r of records) {
    const isError = r.error ? true : false;
    const sourceIcon = r.source === 'summarize' ? '📝' : r.source === 'tool_calling' ? '🔧' : '💬';
    const color = isError ? 'var(--danger)' : 'var(--text-primary)';

    // 请求摘要（截取用户消息的前100字）
    const reqPreview = previewMessages(r.messages, 120);

    // 响应摘要
    const resPreview = r.response_text ? r.response_text.slice(0, 120) + (r.response_text.length > 120 ? '...' : '') : '';
    const tcCount = r.tool_calls ? r.tool_calls.length : 0;
    const hasReasoning = r.reasoning ? r.reasoning.length > 0 : false;

    html += `<div class="lm-record" data-id="${r.id}" style="margin-bottom:6px;border:1px solid var(--border-color);border-radius:6px;background:var(--bg-secondary);overflow:hidden">
      <div class="lm-record-header" onclick="lmToggleDetail('${r.id}')" style="display:flex;align-items:center;gap:8px;padding:7px 10px;cursor:pointer;user-select:none">
        <span style="flex-shrink:0">${sourceIcon}</span>
        <span style="font-size:11px;color:var(--text-secondary);flex-shrink:0">${r.timestamp_str || ''}</span>
        <span style="font-size:10px;color:var(--accent);flex-shrink:0;background:var(--bg-primary);padding:0 6px;border-radius:3px">${r.model || ''}</span>
        <span class="lm-badge ${r.source}" style="flex-shrink:0">${r.source || 'chat'}</span>
        <span style="font-size:10px;color:var(--text-muted);flex-shrink:0">▲${formatNum(r.request_tokens||0)} ▾${formatNum(r.response_tokens||0)}</span>
        ${tcCount ? `<span style="font-size:10px;color:var(--purple)">🛠${tcCount}</span>` : ''}
        ${hasReasoning ? '<span style="font-size:10px;color:var(--warning)">💭</span>' : ''}
        ${isError ? '<span style="font-size:10px;color:var(--danger)">❌</span>' : ''}
        <span style="font-size:10px;color:var(--text-muted);margin-left:auto">${r.duration_ms ? r.duration_ms + 'ms' : ''}</span>
        <span class="lm-expand-icon" style="font-size:10px;color:var(--text-muted)">▶</span>
      </div>
      <div class="lm-record-detail" id="lm-detail-${r.id}" style="display:none;border-top:1px solid var(--border-color)">
        <!-- 请求看板 -->
        <div class="lm-panel">
          <div class="lm-panel-header">
            <span class="lm-panel-title">📤 发送到 LLM（${formatNum(r.request_tokens||0)} tokens）</span>
            <button class="btn-sm" style="font-size:9px;padding:1px 8px" onclick="event.stopPropagation();lmCopy('lm-req-${r.id}')">📋 复制</button>
          </div>
          <div class="lm-panel-body" id="lm-req-${r.id}">
            ${renderRequestContent(r)}
          </div>
        </div>
        <!-- 响应看板 -->
        <div class="lm-panel">
          <div class="lm-panel-header">
            <span class="lm-panel-title">📥 LLM 返回（${formatNum(r.response_tokens||0)} tokens）</span>
            <button class="btn-sm" style="font-size:9px;padding:1px 8px" onclick="event.stopPropagation();lmCopy('lm-res-${r.id}')">📋 复制</button>
          </div>
          <div class="lm-panel-body" id="lm-res-${r.id}">
            ${renderResponseContent(r)}
          </div>
        </div>
      </div>
    </div>`;
  }

  container.innerHTML = html;

  // Load more
  const loadMore = document.getElementById('lm-load-more');
  if (records.length < total) {
    loadMore.style.display = 'block';
  } else {
    loadMore.style.display = 'none';
  }

  lmOffset = records.length;
  lmRecords = records;
}

function renderRequestContent(r) {
  let html = '';

  // System prompt
  if (r.system_prompt) {
    html += `<div class="lm-msg lm-msg-system"><span class="lm-role">system</span><div class="lm-content">${app.escapeHtml(truncateMid(r.system_prompt, 800))}</div></div>`;
  }

  // Messages
  if (r.messages && r.messages.length) {
    for (const m of r.messages) {
      const role = m.role || 'unknown';
      const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
      const tcHtml = m.tool_calls ? renderToolCallsInMsg(m.tool_calls) : '';
      html += `<div class="lm-msg lm-msg-${role}"><span class="lm-role">${role}</span><div class="lm-content">${app.escapeHtml(truncateMid(content || '', 600))}</div>${tcHtml}</div>`;
    }
  }

  // Tools definitions
  if (r.tools && r.tools.length) {
    const toolNames = r.tools.map(t => t.function?.name || t.name || '?').join(', ');
    html += `<div class="lm-msg lm-msg-system"><span class="lm-role">tools(定义)</span><div class="lm-content" style="color:var(--purple)">${r.tools.length} 个工具定义: ${app.escapeHtml(toolNames)}</div></div>`;
  }

  if (!html) html = '<div style="color:var(--text-muted);padding:8px">（无请求内容）</div>';
  return html;
}

function renderResponseContent(r) {
  let html = '';

  // Reasoning
  if (r.reasoning) {
    html += `<div class="lm-msg lm-msg-reasoning"><span class="lm-role">💭 reasoning</span><div class="lm-content" style="color:var(--warning)">${app.escapeHtml(truncateMid(r.reasoning, 1000))}</div></div>`;
  }

  // Response text
  if (r.response_text) {
    html += `<div class="lm-msg lm-msg-assistant"><span class="lm-role">assistant</span><div class="lm-content">${app.escapeHtml(truncateMid(r.response_text, 2000))}</div></div>`;
  }

  // Tool calls
  if (r.tool_calls && r.tool_calls.length) {
    for (const tc of r.tool_calls) {
      const fn = tc.function || {};
      html += `<div class="lm-msg lm-msg-toolcall"><span class="lm-role">🛠 ${fn.name || 'tool_call'}</span><div class="lm-content" style="color:var(--accent);font-family:monospace;font-size:10px">${app.escapeHtml(truncateMid(fn.arguments || '{}', 500))}</div></div>`;
    }
  }

  // Error
  if (r.error) {
    html += `<div class="lm-msg" style="border-left-color:var(--danger)"><span class="lm-role" style="color:var(--danger)">❌ error</span><div class="lm-content" style="color:var(--danger)">${app.escapeHtml(r.error)}</div></div>`;
  }

  if (!html) html = '<div style="color:var(--text-muted);padding:8px">（无响应内容）</div>';
  return html;
}

function renderToolCallsInMsg(tcs) {
  if (!tcs || !tcs.length) return '';
  let html = '<div style="margin-top:4px;padding:4px 8px;background:var(--bg-primary);border-radius:4px">';
  for (const tc of tcs) {
    const fn = tc.function || {};
    html += `<div style="font-size:10px;color:var(--purple)">🛠 ${app.escapeHtml(fn.name || '?')}(${app.escapeHtml(truncateMid(fn.arguments || '', 200))})</div>`;
  }
  html += '</div>';
  return html;
}

// ════════════════════════════════════════════════════════════
//  交互
// ════════════════════════════════════════════════════════════

function lmToggleDetail(id) {
  const detail = document.getElementById('lm-detail-' + id);
  if (!detail) return;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'block';

  // Update expand icon
  const header = detail.parentElement.querySelector('.lm-record-header');
  const icon = header ? header.querySelector('.lm-expand-icon') : null;
  if (icon) icon.textContent = isOpen ? '▶' : '▼';
}

async function lmRefresh() {
  const source = document.getElementById('lm-source-filter')?.value || '';
  const url = '/api/llm-monitor/records?limit=' + LM_PAGE_SIZE + '&source=' + source;
  try {
    const [stats, records] = await Promise.all([
      app.get('/api/llm-monitor/stats'),
      app.get(url),
    ]);
    renderStats(stats);
    renderRecords(records);
  } catch (e) {
    // silent
  }
}

async function lmLoadMore() {
  const source = document.getElementById('lm-source-filter')?.value || '';
  const url = '/api/llm-monitor/records?limit=' + LM_PAGE_SIZE + '&offset=' + lmOffset + '&source=' + source;
  try {
    const data = await app.get(url);
    const records = data.records || [];
    const container = document.getElementById('lm-records-list');
    if (!container) return;

    for (const r of records) {
      // Append single record (simplified - just re-render full list for now)
    }
    // Simplified: just refresh the whole list
    lmRefresh();
  } catch (e) {
    // silent
  }
}

async function lmClear() {
  if (!confirm('确定清除所有 LLM 通信记录吗？')) return;
  try {
    await app.post('/api/llm-monitor/clear');
    lmRefresh();
  } catch (e) {
    app.showToast('清除失败', 'error');
  }
}

async function lmToggleEnabled(enabled) {
  try {
    await app.post('/api/llm-monitor/toggle', { enabled });
    const status = document.getElementById('lm-recording-status');
    if (status) {
      status.textContent = enabled ? '● 监控中' : '○ 已暂停';
      status.style.color = enabled ? 'var(--success)' : 'var(--text-muted)';
    }
  } catch (e) {
    app.showToast('操作失败', 'error');
  }
}

function lmDelayedSearch() {
  clearTimeout(lmSearchTimer);
  lmSearchTimer = setTimeout(() => lmRefresh(), 300);
}

function lmCopy(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const text = el.textContent || el.innerText;
  navigator.clipboard.writeText(text).then(() => {
    app.showToast('已复制到剪贴板');
  }).catch(() => {
    // fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  });
}

// ════════════════════════════════════════════════════════════
//  辅助
// ════════════════════════════════════════════════════════════

function previewMessages(messages, maxLen) {
  if (!messages || !messages.length) return '';
  // 找到第一条 user 消息
  for (const m of messages) {
    if (m.role === 'user' && m.content) {
      const text = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
      return text.slice(0, maxLen) + (text.length > maxLen ? '...' : '');
    }
  }
  // fallback: 第一条消息
  const first = messages[0];
  const text = typeof first?.content === 'string' ? first.content : '';
  return text.slice(0, maxLen);
}

function truncateMid(text, maxLen) {
  if (!text || text.length <= maxLen) return text || '';
  const half = Math.floor(maxLen / 2);
  return text.slice(0, half) + '\n\n... [中间省略 ' + (text.length - maxLen) + ' 字符] ...\n\n' + text.slice(-half);
}

function formatNum(n) {
  n = parseInt(n) || 0;
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return n.toString();
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
