// ════════════════════════════════════════════════════════════
// 灵犀 · 全局应用对象 — 事件总线 + 状态管理 + 视图管理
// ════════════════════════════════════════════════════════════

const app = {
  // ── 事件总线 ──
  _handlers: {},

  on(event, fn) {
    (this._handlers[event] = this._handlers[event] || []).push(fn);
  },

  off(event, fn) {
    const list = this._handlers[event];
    if (list) this._handlers[event] = list.filter(f => f !== fn);
  },

  emit(event, data) {
    (this._handlers[event] || []).forEach(fn => fn(data));
  },

  // ── 全局状态 ──
  state: {
    panelCollapsed: sessionStorage.getItem('panelCollapsed') === 'true',
    navCollapsed: window.innerWidth < 768,
  },

  setState(key, val) {
    this.state[key] = val;
    sessionStorage.setItem(key, String(val));
    this.emit('state:' + key, val);
  },

  // ── 视图管理 ──
  _views: {},

  registerView(id, { load, keepAlive }) {
    this._views[id] = { loaded: false, load, keepAlive };
  },

  switchView(id) {
    // 隐藏所有视图
    document.querySelectorAll('.view').forEach(v => {
      v.classList.remove('active');
    });

    // 显示目标视图
    const target = document.getElementById('view-' + id);
    if (!target) return;

    target.classList.add('active');

    // 如需懒加载
    const view = this._views[id];
    if (view && !view.loaded) {
      view.load && view.load();
      if (view.keepAlive !== false) view.loaded = true;
    }

    // 更新导航高亮
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    const navBtn = document.querySelector('.nav-btn[data-view="' + id + '"]');
    if (navBtn) navBtn.classList.add('active');

    this.emit('view:changed', id);
  },

  // ── API 工具 ──
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error('GET ' + url + ' failed: ' + r.status);
    return r.json();
  },

  async post(url, data) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!r.ok) throw new Error('POST ' + url + ' failed: ' + r.status);
    return r.json();
  },

  async del(url) {
    const r = await fetch(url, { method: 'DELETE' });
    if (!r.ok) throw new Error('DELETE ' + url + ' failed: ' + r.status);
    return r.json();
  },

  // ── HTML 转义 ──
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  },

  // ── Toast ──
  showToast(message, type) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast ' + (type || 'success');
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
  },

  // ── 确认弹窗 ──
  showConfirm(message) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'confirm-overlay';
      overlay.innerHTML =
        '<div class="confirm-box">' +
          '<p>' + message + '</p>' +
          '<div class="confirm-actions">' +
            '<button class="btn-sm" onclick="this.closest(\'.confirm-overlay\').remove(); app._confirmResolve(false)">取消</button>' +
            '<button class="btn-sm danger" onclick="this.closest(\'.confirm-overlay\').remove(); app._confirmResolve(true)">确定</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
      this._confirmResolve = resolve;
    });
  },

  // ── 初始化 ──
  init() {
    // 应用状态面板折叠
    if (this.state.panelCollapsed) {
      document.getElementById('app').classList.add('panel-collapsed');
    }
    // 应用导航栏折叠
    if (this.state.navCollapsed) {
      document.getElementById('nav').classList.add('collapsed');
      document.getElementById('app').classList.add('nav-collapsed');
    }
    // 默认激活对话视图
    this.switchView('chat');
  },
};
