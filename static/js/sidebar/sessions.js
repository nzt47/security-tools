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
        container.innerHTML = messages.map(m => {
            if (m.role === 'user') {
                return '<div class="message user-msg"><div class="msg-label">👤 你</div><div class="msg-content">' + app.escapeHtml(m.content || '') + '</div></div>';
            } else if (m.role === 'assistant') {
                return '<div class="message bot-msg"><div class="msg-label">🤖 云枢</div><div class="msg-content">' + app.escapeHtml(m.content || '') + '</div></div>';
            }
            return '';
        }).join('');
        container.scrollTop = container.scrollHeight;
    } catch(e) {
        container.innerHTML = '<div class="view-empty">加载消息失败</div>';
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

// 切换会话后关闭下拉菜单
const origSwitchSession = switchSession;
async function switchSession(sessionId) {
    await origSwitchSession(sessionId);
    closeSessionsDropdown();
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
});
