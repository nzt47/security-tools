// ════════════════════════════════════════════════════════════
// 历史会话管理模块
// ════════════════════════════════════════════════════════════

let _historyData = [];

async function loadHistory() {
  // 判断当前是侧边栏视图还是详情视图
  const isDetail = document.getElementById('detail-history')?.classList.contains('active');
  const listId = isDetail ? 'detail-history-list' : 'history-list';
  const list = document.getElementById(listId);
  if (!list) return;

  list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const sort = (document.getElementById('detail-history-sort')?.value || document.getElementById('history-sort')?.value || 'newest');
    const searchQ = (document.getElementById('detail-history-search')?.value || document.getElementById('history-search')?.value || '').trim().toLowerCase();
    let data = await apiGet('/api/history');
    _historyData = data.map((entry) => ({ ...entry, index: entry._real_index }));
    if (sort === 'oldest') _historyData.reverse();
    if (searchQ) {
      _historyData = _historyData.filter(e =>
        (e.user || '').toLowerCase().includes(searchQ) || (e.lingxi || '').toLowerCase().includes(searchQ)
      );
    }

    // 更新计数
    const countEl = document.getElementById('detail-history-count');
    if (countEl) countEl.textContent = `${_historyData.length} 条记录`;

    if (_historyData.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">暂无历史记录</div>';
      return;
    }

    // 详情视图使用更大的卡片
    if (isDetail) {
      list.innerHTML = _historyData.map((entry) => `
        <div class="sidebar-card" style="padding:12px 16px">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <strong style="font-size:14px;color:#c9d1d9">👤 ${escapeHtml(entry.user || '').substring(0, 60)}</strong>
            <span class="badge ${entry.mode || 'info'}">${entry.mode || 'normal'}</span>
          </div>
          <div style="font-size:13px;color:#8b949e;margin-bottom:8px">🤖 ${escapeHtml(entry.lingxi || '').substring(0, 120)}</div>
          <div class="sidebar-card-actions">
            <button onclick="showHistoryDetail(${entry.index})">📖 全文</button>
            <button onclick="deleteHistory(${entry.index})" style="color:var(--danger-color)">🗑 删除</button>
          </div>
        </div>
      `).join('');
    } else {
      // 侧边栏视图使用小卡片（现有逻辑）
      list.innerHTML = _historyData.map((entry) => `
        <div class="sidebar-card">
          <div class="sidebar-card-header">
            <span class="sidebar-card-title">${escapeHtml(entry.user || '').substring(0, 30)}</span>
            <span class="badge ${entry.mode || 'info'}">${entry.mode || 'normal'}</span>
          </div>
          <div class="sidebar-card-sub">${escapeHtml(entry.lingxi || '').substring(0, 60)}</div>
          <div class="sidebar-card-actions">
            <button onclick="showHistoryDetail(${entry.index})">📖 详情</button>
            <button onclick="deleteHistory(${entry.index})" style="color:var(--danger-color)">🗑 删除</button>
          </div>
        </div>
      `).join('');
    }
  } catch(e) {
    list.innerHTML = '<div class="sidebar-empty">加载历史失败</div>';
  }
}

function filterHistory() {
  loadHistory();
}

function showHistoryDetail(index) {
  const entry = _historyData.find(e => e.index === index);
  if (!entry) return;
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML = `
    <div class="sidebar-confirm-box" style="max-width:500px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">📖 对话详情</p>
      <div style="background:#0d1117;border-radius:6px;padding:10px;margin-bottom:10px">
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px">👤 用户:</div>
        <div style="font-size:13px;color:#c9d1d9;white-space:pre-wrap">${escapeHtml(entry.user)}</div>
      </div>
      <div style="background:#0d1117;border-radius:6px;padding:10px;margin-bottom:12px">
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px">🤖 灵犀:</div>
        <div style="font-size:13px;color:#c9d1d9;white-space:pre-wrap">${escapeHtml(entry.lingxi)}</div>
      </div>
      <div class="sidebar-confirm-actions">
        <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove()">关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function deleteHistory(index) {
  const confirmed = await showConfirm('确定要删除这条对话记录吗？');
  if (!confirmed) return;
  try {
    await apiDelete(`/api/history/${index}`);
    showToast('已删除');
    loadHistory();
  } catch(e) {
    showToast('删除失败: ' + e.message, 'error');
  }
}

async function clearAllHistory() {
  const confirmed = await showConfirm('确定要清空所有历史记录吗？');
  if (!confirmed) return;
  try {
    await apiPost('/api/history/clear');
    showToast('已清空所有历史记录');
    loadHistory();
  } catch(e) {
    showToast('清空失败', 'error');
  }
}
