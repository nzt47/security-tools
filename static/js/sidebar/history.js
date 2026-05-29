// ════════════════════════════════════════════════════════════
// 历史会话管理模块
// ════════════════════════════════════════════════════════════

let _historyData = [];

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="view-loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const sort = document.getElementById('history-sort').value;
    const searchQ = document.getElementById('history-search').value.trim().toLowerCase();
    let data = await app.get('/api/history');
    _historyData = data.map((entry) => ({ ...entry, index: entry._real_index }));
    if (sort === 'oldest') _historyData.reverse();
    if (searchQ) {
      _historyData = _historyData.filter(e =>
        (e.user || '').toLowerCase().includes(searchQ) ||
        (e.lingxi || '').toLowerCase().includes(searchQ)
      );
    }
    renderHistory();
  } catch(e) {
    list.innerHTML = '<div class="view-empty">加载历史失败</div>';
  }
}

function renderHistory() {
  const list = document.getElementById('history-list');
  if (_historyData.length === 0) {
    list.innerHTML = '<div class="view-empty">暂无历史记录</div>';
    return;
  }
  list.innerHTML = _historyData.map((entry, i) => `
    <div class="view-card">
      <div class="view-card-header">
        <span class="view-card-title">${app.escapeHtml(entry.user || '').substring(0, 30)}</span>
        <span class="badge ${entry.mode || 'info'}">${entry.mode || 'normal'}</span>
      </div>
      <div class="view-card-sub">${app.escapeHtml(entry.lingxi || '').substring(0, 60)}</div>
      <div class="view-card-actions">
        <button onclick="showHistoryDetail(${entry.index})">📖 详情</button>
        <button onclick="deleteHistory(${entry.index})" style="color:var(--danger-color)">🗑 删除</button>
      </div>
    </div>
  `).join('');
}

function filterHistory() {
  loadHistory();
}

function showHistoryDetail(index) {
  const entry = _historyData.find(e => e.index === index);
  if (!entry) return;
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box" style="max-width:500px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">📖 对话详情</p>
      <div style="background:#0d1117;border-radius:6px;padding:10px;margin-bottom:10px">
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px">👤 用户:</div>
        <div style="font-size:13px;color:#c9d1d9;white-space:pre-wrap">${app.escapeHtml(entry.user)}</div>
      </div>
      <div style="background:#0d1117;border-radius:6px;padding:10px;margin-bottom:12px">
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px">🤖 灵犀:</div>
        <div style="font-size:13px;color:#c9d1d9;white-space:pre-wrap">${app.escapeHtml(entry.lingxi)}</div>
      </div>
      <div class="sidebar-confirm-actions">
        <button class="btn-sm" onclick="this.closest('.confirm-overlay').remove()">关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function deleteHistory(index) {
  const confirmed = await app.showConfirm('确定要删除这条对话记录吗？');
  if (!confirmed) return;
  try {
    await app.del(`/api/history/${index}`);
    app.showToast('已删除');
    loadHistory();
  } catch(e) {
    app.showToast('删除失败: ' + e.message, 'error');
  }
}
