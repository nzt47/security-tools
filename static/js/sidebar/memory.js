// ════════════════════════════════════════════════════════════
// 记忆管理模块
// ════════════════════════════════════════════════════════════

async function loadMemory() {
  const isDetail = document.getElementById('detail-memory')?.classList.contains('active');
  const overviewId = isDetail ? 'detail-memory-overview' : 'memory-overview';
  const contentId = isDetail ? 'detail-memory-content' : 'memory-content';

  try {
    const data = await apiGet('/api/memory/overview');
    const recent = data.recent_messages || [];

    const overviewEl = document.getElementById(overviewId);
    if (overviewEl) {
      overviewEl.innerHTML = `
        <div class="sidebar-card" style="text-align:center;padding:14px">
          <div style="font-size:${isDetail ? '28px' : '20px'};font-weight:700;color:#58a6ff">${recent.length}</div>
          <div style="font-size:${isDetail ? '12px' : '10px'};color:#8b949e">短期消息</div>
        </div>
        <div class="sidebar-card" style="text-align:center;padding:14px">
          <div style="font-size:${isDetail ? '28px' : '20px'};font-weight:700;color:#3fb950">${data.summary_version || '无'}</div>
          <div style="font-size:${isDetail ? '12px' : '10px'};color:#8b949e">摘要版本</div>
        </div>
        <div class="sidebar-card" style="text-align:center;padding:14px">
          <div style="font-size:${isDetail ? '28px' : '20px'};font-weight:700;color:#d29922">${data.message_count || 0}</div>
          <div style="font-size:${isDetail ? '12px' : '10px'};color:#8b949e">总消息</div>
        </div>
        <div class="sidebar-card" style="text-align:center;padding:14px">
          <div style="font-size:${isDetail ? '28px' : '20px'};font-weight:700;color:#bc8cff">${Object.keys(data.log_stats || {}).length || 0}</div>
          <div style="font-size:${isDetail ? '12px' : '10px'};color:#8b949e">日志类型</div>
        </div>
      `;
    }

    const contentEl = document.getElementById(contentId);
    if (!contentEl) return;

    let html = '<div style="font-size:12px;color:#8b949e;margin-bottom:6px">📌 短期记忆</div>';
    if (recent.length === 0) {
      html += '<div class="sidebar-empty">暂无短期记忆</div>';
    } else {
      for (const msg of recent) {
        const role = msg.role === 'user' ? '👤' : '🤖';
        html += `<div class="sidebar-card"${isDetail ? ' style="padding:12px 16px"' : ''}>
          <div class="sidebar-card-header">
            <span class="sidebar-card-title${isDetail ? ';font-size:14px' : ''}">${role} ${escapeHtml(msg.content || '').substring(0, isDetail ? 80 : 40)}</span>
          </div>
          <div class="sidebar-card-sub${isDetail ? ';font-size:13px' : ''}">${escapeHtml(msg.content || '').substring(0, isDetail ? 120 : 60)}</div>
          <div class="sidebar-card-actions">
            <button onclick="deleteMemory(${msg.index})" style="color:var(--danger-color)">🗑 删除</button>
          </div>
        </div>`;
      }
    }
    if (data.summary_text) {
      html += '<div style="font-size:12px;color:#8b949e;margin:10px 0 6px">📦 长期摘要</div>';
      html += `<div class="sidebar-card"><div class="sidebar-card-sub${isDetail ? ';font-size:13px' : ''}">${escapeHtml(data.summary_text)}</div></div>`;
    }
    contentEl.innerHTML = html;
  } catch(e) {
    const el = document.getElementById(contentId);
    if (el) el.innerHTML = '<div class="sidebar-empty">加载记忆失败</div>';
  }
}

function showAddMemory() {
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML = `
    <div class="sidebar-confirm-box" style="max-width:400px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">+ 手动添加记忆</p>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">记忆内容</label>
        <textarea class="sidebar-search" id="memory-content-input" rows="3" placeholder="输入想记住的内容..." style="resize:vertical;margin-bottom:0"></textarea>
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">优先级</label>
        <select class="sidebar-search" id="memory-priority" style="margin-bottom:0">
          <option value="low">低</option>
          <option value="normal" selected>普通</option>
          <option value="high">高</option>
        </select>
      </div>
      <div class="sidebar-confirm-actions" style="margin-top:12px">
        <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove()">取消</button>
        <button class="btn-sm primary" onclick="confirmAddMemory()">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function confirmAddMemory() {
  const content = document.getElementById('memory-content-input').value.trim();
  const priority = document.getElementById('memory-priority').value;
  if (!content) {
    showToast('请输入记忆内容', 'error');
    return;
  }
  try {
    const r = await apiPost('/api/memory/manual', { content, priority });
    if (r.ok) {
      showToast('记忆已添加');
      document.querySelector('.sidebar-confirm-overlay').remove();
      loadMemory();
    }
  } catch(e) {
    showToast('添加失败', 'error');
  }
}

async function deleteMemory(index) {
  const confirmed = await showConfirm('确定删除这条记忆吗？');
  if (!confirmed) return;
  try {
    await apiDelete(`/api/memory/${index}`);
    showToast('已删除');
    loadMemory();
  } catch(e) {
    showToast('删除失败', 'error');
  }
}

async function triggerCompression() {
  try {
    const r = await apiPost('/api/memory/compress');
    if (r.ok) {
      showToast('记忆压缩已触发');
      loadMemory();
    }
  } catch(e) {
    showToast('压缩触发失败', 'error');
  }
}
