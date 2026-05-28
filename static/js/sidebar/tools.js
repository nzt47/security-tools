// ════════════════════════════════════════════════════════════
// 工具集成模块
// ════════════════════════════════════════════════════════════

async function loadTools() {
  const list = document.getElementById('tools-list');
  const count = document.getElementById('tools-count');
  list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const tools = await apiGet('/api/tools/config');
    count.textContent = `已注册 ${tools.length} 个工具`;
    renderTools(tools);
  } catch(e) {
    list.innerHTML = '<div class="sidebar-empty">加载工具列表失败</div>';
    count.textContent = '';
  }
}

function renderTools(tools) {
  const list = document.getElementById('tools-list');
  if (!tools || tools.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">暂无已注册的工具</div>';
    return;
  }
  list.innerHTML = tools.map(t => `
    <div class="sidebar-card">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title">🔧 ${escapeHtml(t.name)}</span>
        <label class="toggle-switch">
          <input type="checkbox" ${t.enabled !== false ? 'checked' : ''} onchange="toggleTool('${t.name}')">
          <span class="slider"></span>
        </label>
      </div>
      <div class="sidebar-card-sub">${escapeHtml(t.description)}</div>
      <div style="font-size:11px;color:#484f58;margin-top:4px">
        调用 ${t.call_count || 0} 次${t.last_used ? ' | 上次: ' + t.last_used : ''}
      </div>
    </div>
  `).join('');
}

async function toggleTool(name) {
  try {
    // 获取当前开关状态
    const tools = await apiGet('/api/tools/config');
    const tool = tools.find(t => t.name === name);
    if (!tool) return;
    const r = await apiPost('/api/tools/toggle', { name, enabled: !tool.enabled });
    if (r.ok) {
      showToast(r.enabled ? `「${name}」已授权` : `「${name}」已禁用`);
      loadTools();
    }
  } catch(e) {
    showToast('操作失败', 'error');
  }
}
