// ════════════════════════════════════════════════════════════
// 工具集成模块
// ════════════════════════════════════════════════════════════

async function loadTools() {
  const isDetail = document.getElementById('detail-tools')?.classList.contains('active');
  const listId = isDetail ? 'detail-tools-list' : 'tools-list';
  const list = document.getElementById(listId);
  const count = document.getElementById(isDetail ? 'detail-tools-count' : 'tools-count');
  if (!list) return;

  list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const tools = await apiGet('/api/tools/config');
    if (count) count.textContent = `已注册 ${tools.length} 个工具`;
    if (!tools || tools.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">暂无已注册的工具</div>';
      return;
    }
    list.innerHTML = tools.map(t => `
      <div class="sidebar-card${isDetail ? '" style="padding:12px 16px' : ''}">
        <div class="sidebar-card-header">
          <span class="sidebar-card-title${isDetail ? '" style="font-size:14px' : ''}">🔧 ${escapeHtml(t.name)}</span>
          <label class="toggle-switch">
            <input type="checkbox" ${t.enabled !== false ? 'checked' : ''} onchange="toggleTool('${t.name}')">
            <span class="slider"></span>
          </label>
        </div>
        <div class="sidebar-card-sub${isDetail ? '" style="font-size:13px' : ''}">${escapeHtml(t.description)}</div>
        <div style="font-size:11px;color:#484f58;margin-top:4px">
          调用 ${t.call_count || 0} 次${t.last_used ? ' | 上次: ' + t.last_used : ''}
        </div>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = '<div class="sidebar-empty">加载工具列表失败</div>';
    if (count) count.textContent = '';
  }
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
