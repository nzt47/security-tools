// ════════════════════════════════════════════════════════════
// 历史会话管理模块 — 以对话方式展示
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
        (e.Yunshu || '').toLowerCase().includes(searchQ)
      );
    }
    renderHistory();
  } catch(e) {
    list.innerHTML = '<div class="view-empty">加载历史失败</div>';
  }
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch(e) {
    return isoStr;
  }
}

function formatDate(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const y = d.getFullYear(), m = pad(d.getMonth()+1), day = pad(d.getDate());
    const h = pad(d.getHours()), mi = pad(d.getMinutes());
    // 同一天只显示时间
    if (y === now.getFullYear() && m === pad(now.getMonth()+1) && day === pad(now.getDate())) {
      return `${h}:${mi}`;
    }
    return `${y}-${m}-${day} ${h}:${mi}`;
  } catch(e) {
    return isoStr;
  }
}

function renderHistory() {
  const list = document.getElementById('history-list');
  if (_historyData.length === 0) {
    list.innerHTML = '<div class="view-empty">暂无历史记录</div>';
    return;
  }
  // 以对话风格渲染每条记录
  list.innerHTML = _historyData.map((entry, i) => {
    const userTime = formatDate(entry.timestamp);
    const asstTime = entry.timestamp ? formatDate(entry.timestamp) : '';
    // 裁剪过长内容用于预览
    const userText = entry.user || '';
    const asstText = entry.Yunshu || '';
    const userPreview = userText.length > 200 ? userText.substring(0, 200) + '...' : userText;
    const asstPreview = asstText.length > 200 ? asstText.substring(0, 200) + '...' : asstText;
    return `
      <div class="history-chat-group" data-index="${i}">
        <div class="hc-row user">
          <div class="hc-avatar">👤</div>
          <div class="hc-bubble user">
            <div class="hc-bubble-text">${app.escapeHtml(userPreview)}</div>
            <div class="hc-meta">
              <span class="hc-time">${userTime}</span>
              <span class="hc-copy" data-text="${app.escapeHtml(userText)}" title="复制">📋</span>
            </div>
          </div>
        </div>
        <div class="hc-row yunshu">
          <div class="hc-avatar">🤖</div>
          <div class="hc-bubble yunshu">
            <div class="hc-bubble-text">${app.escapeHtml(asstPreview)}</div>
            <div class="hc-meta">
              <span class="hc-time">${asstTime}</span>
              <span class="hc-copy" data-text="${app.escapeHtml(asstText)}" title="复制">📋</span>
              <span class="hc-actions">
                <button class="hc-btn" onclick="showHistoryDetail(${entry.index})" title="查看完整对话">📖</button>
                <button class="hc-btn hc-btn-del" onclick="deleteHistory(${entry.index})" title="删除">🗑</button>
              </span>
            </div>
          </div>
        </div>
      </div>`;
  }).join('');
}

function filterHistory() {
  loadHistory();
}

function showHistoryDetail(index) {
  const entry = _historyData.find(e => e.index === index);
  if (!entry) return;

  const userTime = formatDate(entry.timestamp);
  const asstTime = formatDate(entry.timestamp);

  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box hc-detail-modal">
      <div class="hc-detail-header">
        <span>💬 对话详情</span>
        <span class="modal-close" onclick="this.closest('.confirm-overlay').remove()">&times;</span>
      </div>
      <div class="hc-detail-body">
        <div class="hc-row user">
          <div class="hc-avatar">👤</div>
          <div class="hc-bubble user">
            <div class="hc-bubble-text">${app.escapeHtml(entry.user)}</div>
            <div class="hc-meta"><span class="hc-time">${userTime}</span></div>
          </div>
        </div>
        <div class="hc-row yunshu">
          <div class="hc-avatar">🤖</div>
          <div class="hc-bubble yunshu">
            <div class="hc-bubble-text">${app.escapeHtml(entry.Yunshu)}</div>
            <div class="hc-meta"><span class="hc-time">${asstTime}</span></div>
          </div>
        </div>
      </div>
      <div class="hc-detail-footer">
        <span style="font-size:11px;color:#484f58">模式: ${entry.mode || 'normal'}</span>
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
