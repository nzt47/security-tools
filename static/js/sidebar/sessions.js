// ════════════════════════════════════════════════════════════
// 会话侧边栏管理模块
// ════════════════════════════════════════════════════════════

let _sessionsData = { sessions: [], current_id: null };
let _requestSeq = 0;

async function loadSessions() {
    const seq = ++_requestSeq;
    try {
        const data = await app.get('/api/sessions');
        if (seq !== _requestSeq) return; // 丢弃过期请求
        _sessionsData = data;
        renderSessions();
        updateChatHeader();
    } catch(e) {
        console.error('加载会话失败:', e);
    }
}

function renderSessions() {
    const list = document.getElementById('sessions-list');
    if (!list) return;
    if (!_sessionsData.sessions || _sessionsData.sessions.length === 0) {
        list.innerHTML = '<div style="padding:16px;text-align:center;color:#8b949e;font-size:12px">暂无会话</div>';
        return;
    }
    list.innerHTML = _sessionsData.sessions.map(s => {
        const isActive = s.id === _sessionsData.current_id;
        const time = s.updated_at ? formatSessionTime(s.updated_at) : '';
        return `
            <div class="session-item ${isActive ? 'active' : ''}" data-id="${s.id}">
                <div class="session-item-title" title="${app.escapeHtml(s.title || '未命名')}">${app.escapeHtml(s.title || '未命名')}</div>
                <div class="session-item-meta">${s.message_count || 0} 条</div>
                <div class="session-item-actions">
                    <button onclick="event.stopPropagation(); renameSession('${s.id}')" title="重命名">✎</button>
                    <button class="btn-delete" onclick="event.stopPropagation(); deleteSession('${s.id}')" title="删除">✕</button>
                </div>
            </div>
        `;
    }).join('');

    list.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', () => switchSession(el.dataset.id));
    });
}

function formatSessionTime(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return diffMin + '分钟前';
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return diffHour + '小时前';
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function updateChatHeader() {
    const header = document.getElementById('chat-header-title');
    if (!header) return;
    const current = _sessionsData.sessions.find(s => s.id === _sessionsData.current_id);
    if (current) {
        header.textContent = '💬 ' + (current.title || '对话');
    }
}

async function switchSession(sessionId) {
    if (sessionId === _sessionsData.current_id) return;
    closeSessionsDropdown();
    try {
        await app.post('/api/sessions/current', { session_id: sessionId });
        _sessionsData.current_id = sessionId;
        renderSessions();
        updateChatHeader();
        loadSessionMessages(sessionId);
    } catch(e) {
        app.showToast('切换会话失败', 'error');
    }
}

async function loadSessionMessages(sessionId) {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    container.innerHTML = '<div class="view-loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
    try {
        const messages = await app.get(`/api/sessions/${sessionId}/messages`);
        // 判断开关状态
        const thinkingOn = typeof getDisplayPref === 'function' ? getDisplayPref('thinking') : true;
        const toolsOn = typeof getDisplayPref === 'function' ? getDisplayPref('toolcalls') : true;
        let html = '';
        for (const m of messages) {
            const ts = m.timestamp ? formatMsgTime(m.timestamp) : '';
            const content = app.escapeHtml(m.content || '');
            if (m.role === 'user') {
                html += `<div class="msg user">${content}<span class="msg-time">${ts}</span></div>`;
            } else if (m.role === 'assistant') {
                // 恢复 tool_steps
                if (m.tool_steps && m.tool_steps.length > 0 && toolsOn) {
                    for (const step of m.tool_steps) {
                        if (step.type === 'tool_call') {
                            const args = step.args || {};
                            const query = args.query || args.url || JSON.stringify(args);
                            html += `<div class="msg tool-step"><span class="tool-step-icon">🔧</span> ${app.escapeHtml(step.tool)} <span class="tool-step-status running">⋯ 进行中</span><div class="tool-step-args">${app.escapeHtml(String(query).substring(0, 100))}</div></div>`;
                        } else if (step.type === 'tool_result') {
                            html += `<div class="msg tool-step"><span class="tool-step-icon">✅</span> ${app.escapeHtml(step.tool)} <span class="tool-step-status ${step.status}">${step.status === 'success' ? '✓ 完成' : '✗ 失败'}</span><div class="tool-step-summary">${app.escapeHtml(step.summary || '')}</div></div>`;
                        }
                    }
                }
                // 恢复 reasoning（thought 框）
                let displayContent = content;
                if (m.reasoning && thinkingOn) {
                    displayContent = `<div class="thought-block">💭 <span class="thought-label">Thought</span><div class="thought-content">${app.escapeHtml(m.reasoning)}</div></div>\n` + content;
                }
                // 生成 .meta（历史消息不含模式标签，仅有时间）
                const metaDisplay = thinkingOn ? '' : 'none';
                html += `<div class="msg Yunshu">${displayContent}<div class="meta" data-has-mode="false" style="display:${metaDisplay}"><span class="msg-time">${ts}</span></div></div>`;
            }
        }
        container.innerHTML = html;
        container.scrollTop = container.scrollHeight;
    } catch(e) {
        container.innerHTML = '<div class="view-empty">加载消息失败</div>';
    }
}

function formatMsgTime(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return '';
        return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
    } catch(e) {
        return '';
    }
}

async function createNewSession() {
    try {
        const session = await app.post('/api/sessions', {});
        _sessionsData.current_id = session.id;
        await loadSessions();
        const container = document.getElementById('chat-messages');
        if (container) container.innerHTML = '';
        updateChatHeader();
    } catch(e) {
        app.showToast('创建会话失败', 'error');
    }
}

async function deleteSession(sessionId) {
    const confirmed = await app.showConfirm('确定要删除此会话及其所有消息吗？');
    if (!confirmed) return;
    try {
        await app.del(`/api/sessions/${sessionId}`);
        if (_sessionsData.current_id === sessionId) {
            _sessionsData.current_id = null;
            const container = document.getElementById('chat-messages');
            if (container) container.innerHTML = '';
        }
        await loadSessions();
        // 如果没有当前会话，自动创建新会话
        if (!_sessionsData.current_id) {
            await createNewSession();
        }
        app.showToast('会话已删除');
    } catch(e) {
        app.showToast('删除失败', 'error');
    }
}

async function renameSession(sessionId) {
    const session = _sessionsData.sessions.find(s => s.id === sessionId);
    const currentTitle = session ? session.title : '';
    const newTitle = prompt('输入新标题:', currentTitle);
    if (!newTitle || newTitle === currentTitle) return;
    try {
        await app.put(`/api/sessions/${sessionId}/rename`, { title: newTitle });
        await loadSessions();
        app.showToast('已重命名');
    } catch(e) {
        app.showToast('重命名失败', 'error');
    }
}

// ── 下拉菜单开关 ──
function toggleSessionsDropdown() {
    const dropdown = document.getElementById('sessions-dropdown');
    if (!dropdown) return;
    const isOpen = dropdown.style.display !== 'none';
    dropdown.style.display = isOpen ? 'none' : 'flex';
    // 箭头动画
    const left = document.getElementById('chat-header-left');
    if (left) left.classList.toggle('open', !isOpen);
}

function closeSessionsDropdown() {
    const dropdown = document.getElementById('sessions-dropdown');
    if (dropdown) dropdown.style.display = 'none';
}

// 点击外部关闭下拉菜单
document.addEventListener('click', function(e) {
    const header = document.getElementById('chat-header');
    const dropdown = document.getElementById('sessions-dropdown');
    if (!header || !dropdown) return;
    if (!header.contains(e.target)) {
        dropdown.style.display = 'none';
    }
});

// 初始化：加载会话列表，然后加载当前会话的消息
document.addEventListener('DOMContentLoaded', async () => {
    await loadSessions();
    // 加载当前会话的对话历史，确保页面刷新后聊天区有内容
    if (_sessionsData.current_id) {
        loadSessionMessages(_sessionsData.current_id);
    }
});
