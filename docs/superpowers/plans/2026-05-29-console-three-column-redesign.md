# 云枢控制台三栏布局重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将云枢主界面从耦合的内联布局重构为 CSS Grid 三栏解耦架构，所有管理模块嵌入主内容区，状态面板支持折叠和进度条显示。

**Architecture:** CSS Grid 三栏（导航栏 200px | 主内容 flex | 状态面板 220px），CSS 变量控制所有尺寸，事件总线解耦组件通信，管理视图与对话/全景平级切换。

**Tech Stack:** Vanilla HTML/CSS/JS, Flask (Jinja2), CSS Grid, 无额外依赖

---

## 文件结构

```
Create:
  static/css/base.css            — CSS 变量、reset、通用样式
  static/css/nav.css             — 左侧导航栏样式
  static/css/status-panel.css    — 右侧状态面板样式（进度条+事件流）
  static/css/views.css           — 管理视图通用样式
  static/css/modals.css          — 设置弹窗样式（从内联提取）
  static/js/app.js               — 全局 app 对象、事件总线、状态管理
  static/js/nav.js               — 导航栏交互逻辑

Rewrite:
  static/css/layout.css          — 原文件改为 Grid 三栏骨架
  static/css/responsive.css      — 重写所有 media queries
  templates/index.html           — DOM 结构重构

Modify:
  static/css/sidebar.css         — 精简为仅保留通用组件（卡片/开关/搜索等）
  static/css/panorama.css        — 移除冗余，精简
  static/js/sidebar/sidebar.js   — 拆分：status-panel 逻辑 + 通用工具保留
  static/js/sidebar/history.js   — 适配新视图选择器（#view-history 而非浮层）
  static/js/sidebar/skills.js    — 同上
  static/js/sidebar/tools.js     — 同上
  static/js/sidebar/personality.js — 同上
  static/js/sidebar/memory.js    — 同上
  static/js/panorama.js          — 适配新 DOM 结构

Keep (no changes):
  app_server.py, config.py, main.py, all data files, sensor/ modules
```

---

### Task 1: 创建 base.css — CSS 变量与全局 reset

**Files:**
- Create: `static/css/base.css`

- [ ] **Step 1: 编写 base.css**

```css
/* ════════════════════════════════════════
   云枢 · 基础变量与全局样式
   ════════════════════════════════════════ */

:root {
  /* 布局尺寸 */
  --nav-w: 200px;
  --nav-collapsed-w: 48px;
  --panel-w: 220px;
  --topbar-h: 44px;

  /* 颜色 */
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #1c2333;
  --bg-hover: #21262d;
  --border-color: #30363d;
  --text-primary: #c9d1d9;
  --text-secondary: #8b949e;
  --text-muted: #484f58;
  --accent: #58a6ff;
  --success: #3fb950;
  --warning: #d29922;
  --danger: #f85149;
  --purple: #bc8cff;

  /* 字体 */
  --font-sans: -apple-system, 'Segoe UI', sans-serif;
  --font-mono: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  overflow: hidden;
  font-family: var(--font-sans);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
}

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 3px; }

/* 通用工具类 */
.hidden { display: none !important; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/base.css
git commit -m "feat: add base.css with CSS variables and global reset"
```

---

### Task 2: 重写 layout.css — CSS Grid 三栏骨架

**Files:**
- Rewrite: `static/css/layout.css`

- [ ] **Step 1: 编写 layout.css**

```css
/* ════════════════════════════════════════
   云枢 · CSS Grid 三栏布局骨架
   ════════════════════════════════════════ */

#app {
  display: grid;
  grid-template-columns: var(--nav-w) 1fr var(--panel-w);
  grid-template-rows: var(--topbar-h) 1fr;
  height: 100vh;
  overflow: hidden;
}

/* 状态面板折叠 */
#app.panel-collapsed {
  grid-template-columns: var(--nav-w) 1fr 0px;
}

/* 导航栏折叠 */
#app.nav-collapsed {
  grid-template-columns: var(--nav-collapsed-w) 1fr var(--panel-w);
}

#app.nav-collapsed.panel-collapsed {
  grid-template-columns: var(--nav-collapsed-w) 1fr 0px;
}

/* 四个 grid 区域 */
#topbar {
  grid-column: 1 / -1;
  grid-row: 1;
}

#nav {
  grid-column: 1;
  grid-row: 2;
  overflow: hidden;
}

#content {
  grid-column: 2;
  grid-row: 2;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

#status-panel {
  grid-column: 3;
  grid-row: 2;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
```

- [ ] **Step 2: Commit**

```bash
git add static/css/layout.css
git commit -m "refactor: rewrite layout.css with CSS Grid three-column skeleton"
```

---

### Task 3: 创建 nav.css — 左侧导航栏样式

**Files:**
- Create: `static/css/nav.css`

- [ ] **Step 1: 编写 nav.css**

```css
/* ════════════════════════════════════════
   云枢 · 左侧导航栏
   ════════════════════════════════════════ */

#nav {
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: width 0.2s ease;
}

/* 品牌区 */
.nav-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  height: var(--topbar-h);
}

.nav-logo { font-size: 20px; }
.nav-title { font-size: 15px; font-weight: 700; white-space: nowrap; }
.nav-sub { font-size: 10px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 导航项列表容器 */
.nav-items {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

/* 导航按钮 */
.nav-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
  border-left: 3px solid transparent;
  flex-shrink: 0;
  font-family: inherit;
}

.nav-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.nav-btn.active {
  background: var(--bg-tertiary);
  color: var(--accent);
  border-left-color: var(--accent);
}

.nav-btn .nav-icon { font-size: 16px; width: 24px; text-align: center; flex-shrink: 0; }
.nav-btn .nav-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 分隔线 */
.nav-divider {
  height: 1px;
  background: var(--border-color);
  margin: 4px 14px;
  flex-shrink: 0;
}

.nav-section-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 6px 14px 2px;
  flex-shrink: 0;
}

/* 折叠状态：hover 时展开 */
#nav.collapsed { width: var(--nav-collapsed-w); }
#nav.collapsed:hover { width: var(--nav-w); }
#nav.collapsed .nav-title,
#nav.collapsed .nav-sub,
#nav.collapsed .nav-section-label,
#nav.collapsed .nav-btn .nav-label { display: none; }
#nav.collapsed .nav-brand { justify-content: center; padding: 10px 4px; }
#nav.collapsed .nav-btn { justify-content: center; padding: 8px 0; border-left: none; border-radius: 6px; width: 40px; margin: 0 auto; }
#nav.collapsed .nav-btn .nav-icon { width: auto; font-size: 18px; }
#nav.collapsed .nav-btn.active { border-left: none; outline: 2px solid var(--accent); }
#nav.collapsed .nav-items { align-items: center; padding: 4px; }
#nav.collapsed .nav-divider { margin: 6px 8px; }

/* 折叠按钮 */
.nav-collapse-btn {
  width: 100%;
  padding: 6px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
  border-top: 1px solid var(--border-color);
  text-align: center;
}

.nav-collapse-btn:hover { color: var(--text-secondary); }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/nav.css
git commit -m "feat: add nav.css with collapsible navigation styles"
```

---

### Task 4: 创建 status-panel.css — 右侧状态面板

**Files:**
- Create: `static/css/status-panel.css`

- [ ] **Step 1: 编写 status-panel.css**

```css
/* ════════════════════════════════════════
   云枢 · 右侧状态面板
   ════════════════════════════════════════ */

#status-panel {
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-color);
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
  overflow-y: auto;
}

/* 折叠按钮 */
.panel-toggle {
  position: absolute;
  left: -16px;
  top: 50%;
  width: 16px;
  height: 48px;
  border: 1px solid var(--border-color);
  border-right: none;
  background: var(--bg-secondary);
  color: var(--text-muted);
  font-size: 8px;
  cursor: pointer;
  border-radius: 4px 0 0 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
}

.panel-toggle:hover { color: var(--accent); }

#status-panel {
  position: relative;
}

/* 区块标题 */
.sp-section-title {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 2px;
}

/* 指标行：标签 + 进度条 + 数值 */
.sp-metric-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
}

.sp-metric-row .sp-label {
  color: var(--text-secondary);
  font-size: 10px;
  min-width: 36px;
}

.sp-metric-row .sp-bar {
  flex: 1;
  height: 5px;
  background: var(--bg-primary);
  border-radius: 3px;
  overflow: hidden;
}

.sp-metric-row .sp-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.sp-metric-row .sp-bar-fill.normal { background: var(--success); }
.sp-metric-row .sp-bar-fill.warning { background: var(--warning); }
.sp-metric-row .sp-bar-fill.critical { background: var(--danger); }

.sp-metric-row .sp-value {
  font-weight: 700;
  font-size: 10px;
  min-width: 32px;
  text-align: right;
}

.sp-metric-row .sp-value.normal { color: var(--success); }
.sp-metric-row .sp-value.warning { color: var(--warning); }
.sp-metric-row .sp-value.critical { color: var(--danger); }

/* 纯文本指标 */
.sp-text-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2px 0;
  font-size: 11px;
}

.sp-text-row .sp-label { color: var(--text-secondary); }
.sp-text-row .sp-value { color: var(--text-primary); font-weight: 500; }

/* 事件流区域 */
.sp-events-section { flex: 1; display: flex; flex-direction: column; min-height: 0; }

.sp-events {
  flex: 1;
  overflow-y: auto;
  font-size: 9px;
  color: var(--text-secondary);
  line-height: 1.6;
  min-height: 40px;
}

.sp-event {
  padding: 2px 0;
  border-bottom: 1px solid var(--bg-hover);
  display: flex;
  align-items: center;
  gap: 4px;
}

.sp-event .sp-event-time { color: var(--text-muted); font-size: 8px; min-width: 32px; }
.sp-event .sp-event-text { color: var(--text-secondary); font-size: 9px; }

/* 区块分隔 */
.sp-section + .sp-section { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--bg-hover); }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/status-panel.css
git commit -m "feat: add status-panel.css with progress bars and event stream"
```

---

### Task 5: 创建 views.css — 管理视图通用样式

**Files:**
- Create: `static/css/views.css`

- [ ] **Step 1: 编写 views.css**

```css
/* ════════════════════════════════════════
   云枢 · 管理视图通用样式
   ════════════════════════════════════════ */

/* 所有视图默认隐藏 */
.view { display: none; flex-direction: column; flex: 1; min-height: 0; }
.view.active { display: flex; }

/* 视图主体（滚动区域） */
.view-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
}

/* 视图标题 */
.view-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 工具栏 */
.view-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  align-items: center;
}

/* 列表卡片（从 sidebar.css 迁移） */
.view-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 6px;
}

.view-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.view-card-title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.view-card-sub { font-size: 11px; color: var(--text-secondary); line-height: 1.4; }

.view-card-actions {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}

.view-card-actions button {
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border-color);
  background: transparent;
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
  font-family: inherit;
}

.view-card-actions button:hover { background: var(--bg-hover); color: var(--text-primary); }

/* 搜索框 */
.view-search {
  width: 100%;
  max-width: 300px;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
}

.view-search:focus { border-color: var(--accent); }
.view-search::placeholder { color: var(--text-muted); }

/* 通用操作按钮 */
.btn-sm {
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
}

.btn-sm:hover { background: var(--bg-hover); }

.btn-sm.primary {
  background: #238636;
  border-color: #238636;
  color: #fff;
}

.btn-sm.primary:hover { background: #2ea043; }

.btn-sm.danger {
  color: var(--danger);
  border-color: var(--danger);
}

.btn-sm.danger:hover {
  background: var(--danger);
  color: #fff;
}

/* 空状态 */
.view-empty {
  text-align: center;
  padding: 30px 10px;
  color: var(--text-muted);
  font-size: 13px;
}

/* 加载状态 */
.view-loading {
  text-align: center;
  padding: 20px;
  color: var(--text-secondary);
  font-size: 13px;
}

/* 标签徽标 */
.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
}

.badge.on { background: var(--success); color: #fff; }
.badge.off { background: var(--danger); color: #fff; }
.badge.info { background: var(--accent); color: #fff; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/views.css
git commit -m "feat: add views.css with admin view common styles"
```

---

### Task 6: 创建 modals.css — 弹窗样式（从内联提取）

**Files:**
- Create: `static/css/modals.css`

- [ ] **Step 1: 编写 modals.css**

```css
/* ════════════════════════════════════════
   云枢 · 弹窗与模态框样式
   ════════════════════════════════════════ */

.modal-overlay {
  position: fixed;
  inset: 0;
  background: #00000080;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 8px 32px #00000080;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
}

.modal-header h3 { margin: 0; font-size: 16px; color: var(--text-primary); }

.modal-close {
  font-size: 24px;
  cursor: pointer;
  color: var(--text-secondary);
  line-height: 1;
}

.modal-close:hover { color: var(--text-primary); }

.modal-body { padding: 20px; }

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border-color);
}

/* 表单 */
.form-group { margin-bottom: 14px; }

.form-group label {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.form-group input,
.form-group select {
  width: 100%;
  padding: 8px 12px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

.form-group input:focus,
.form-group select:focus { border-color: var(--accent); }

.model-hint { font-size: 11px; color: var(--text-secondary); margin-bottom: 4px; }

/* 配置状态 */
.cfg-status {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  display: none;
}

.cfg-status.ok {
  display: block;
  background: var(--success);
  color: #fff;
}

.cfg-status.err {
  display: block;
  background: var(--danger);
  color: #fff;
}

/* 按钮 */
.btn-primary {
  padding: 7px 18px;
  border-radius: 6px;
  border: none;
  background: #238636;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
}

.btn-primary:hover { background: #2ea043; }

.btn-secondary {
  padding: 7px 18px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
}

.btn-secondary:hover { background: var(--bg-hover); }

/* Toast 提示 */
.toast {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 13px;
  z-index: 9999;
  animation: toast-in 0.3s ease;
}

.toast.success { background: var(--success); color: #fff; }
.toast.error { background: var(--danger); color: #fff; }

@keyframes toast-in {
  from { opacity: 0; transform: translateX(-50%) translateY(20px); }
  to { opacity: 1; transform: translateX(-50%) translateY(0); }
}

/* 确认弹窗 */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
}

.confirm-box {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px;
  max-width: 360px;
  width: 90%;
}

.confirm-box p { font-size: 14px; color: var(--text-primary); margin: 0 0 16px; line-height: 1.5; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 8px; }
```

- [ ] **Step 2: Commit**

```bash
git add static/css/modals.css
git commit -m "refactor: extract modal styles from inline to modals.css"
```

---

### Task 7: 创建 app.js — 事件总线与全局状态

**Files:**
- Create: `static/js/app.js`

- [ ] **Step 1: 编写 app.js**

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · 全局应用对象 — 事件总线 + 状态管理 + 视图管理
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
```

- [ ] **Step 2: Commit**

```bash
git add static/js/app.js
git commit -m "feat: add app.js with event bus, state management, and view system"
```

---

### Task 8: 创建 nav.js — 导航栏交互

**Files:**
- Create: `static/js/nav.js`

- [ ] **Step 1: 编写 nav.js**

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · 左侧导航栏交互
// ════════════════════════════════════════════════════════════

function initNav() {
  const nav = document.getElementById('nav');
  if (!nav) return;

  // 折叠/展开
  const toggleBtn = document.getElementById('nav-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const collapsed = nav.classList.toggle('collapsed');
      document.getElementById('app').classList.toggle('nav-collapsed', collapsed);
      app.setState('navCollapsed', collapsed);
      toggleBtn.textContent = collapsed ? '▶' : '◀';
    });
  }

  // 点击导航项
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      if (!view) return;
      if (view === 'settings') {
        showSettings();
        return;
      }
      if (view === 'refresh') {
        refreshAll();
        return;
      }
      app.switchView(view);
    });
  });

  // 窗口 resize 自动折叠
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (window.innerWidth < 768) {
        nav.classList.add('collapsed');
        document.getElementById('app').classList.add('nav-collapsed');
      } else if (!app.state.navCollapsed) {
        nav.classList.remove('collapsed');
        document.getElementById('app').classList.remove('nav-collapsed');
      }
    }, 200);
  });
}

function refreshAll() {
  app.emit('refresh');
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/nav.js
git commit -m "feat: add nav.js with navigation interaction logic"
```

---

### Task 9: 重构 index.html — DOM 结构

**Files:**
- Modify: `templates/index.html` (全局重写 DOM 结构)

这是最大改动。用干净的 Grid 三栏替换当前结构。

- [ ] **Step 1: 编写新 HTML 骨架（替换 #app 内部）**

保留内容：
- inline `<style>` 中非布局的样式（全景样式、对话气泡等）→ 随后迁移到 CSS 文件
- 对话视图 DOM（`#chat-messages`、`#chat-input-area`）
- 全景视图 DOM（`#panorama-view` 内全部内容）
- 5 个管理视图 DOM（`#history-view`、`#skills-view` 等）
- 设置弹窗 DOM
- inline `<script>` 中的聊天逻辑、设置逻辑、安全告警轮询
- 所有 `<script src>` 引用（路径更新）

新的 DOM 结构：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>云枢 · 数字生命体</title>
<link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/nav.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/status-panel.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/views.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/modals.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/panorama.css') }}?v=20260529">
<link rel="stylesheet" href="{{ url_for('static', filename='css/responsive.css') }}?v=20260529">
</head>
<body>
<div id="app">
  <!-- 顶栏 -->
  <header id="topbar">
    <span class="nav-logo">🤖</span>
    <div style="flex:1;min-width:0">
      <div class="nav-title">云枢</div>
      <div class="nav-sub">数字生命体 · 感知-认知-行动闭环</div>
    </div>
    <div style="display:flex;gap:4px">
      <button class="btn-sm" onclick="showSettings()" title="设置">⚙</button>
      <button class="btn-sm" onclick="refreshAll()" title="刷新">⟳</button>
      <button class="btn-sm danger" onclick="clearChat()" title="清空对话">✕</button>
    </div>
  </header>

  <!-- 左侧导航栏 -->
  <nav id="nav">
    <div class="nav-brand">
      <span class="nav-logo">🤖</span>
      <div class="nav-title">云枢</div>
    </div>
    <div class="nav-items">
      <button class="nav-btn active" data-view="chat"><span class="nav-icon">💬</span><span class="nav-label">对话</span></button>
      <button class="nav-btn" data-view="panorama"><span class="nav-icon">🗺</span><span class="nav-label">全景</span></button>

      <div class="nav-divider"></div>
      <div class="nav-section-label">管理</div>

      <button class="nav-btn" data-view="history"><span class="nav-icon">🕐</span><span class="nav-label">历史会话</span></button>
      <button class="nav-btn" data-view="skills"><span class="nav-icon">🔧</span><span class="nav-label">技能管理</span></button>
      <button class="nav-btn" data-view="tools"><span class="nav-icon">🛠</span><span class="nav-label">工具集成</span></button>
      <button class="nav-btn" data-view="personality"><span class="nav-icon">🎭</span><span class="nav-label">人格配置</span></button>
      <button class="nav-btn" data-view="memory"><span class="nav-icon">🧠</span><span class="nav-label">记忆管理</span></button>

      <div class="nav-divider"></div>

      <button class="nav-btn" data-view="settings"><span class="nav-icon">⚙</span><span class="nav-label">设置</span></button>
      <button class="nav-btn" data-view="refresh"><span class="nav-icon">⟳</span><span class="nav-label">刷新</span></button>
    </div>
    <button class="nav-collapse-btn" id="nav-toggle-btn">◀</button>
  </nav>

  <!-- 主内容区 -->
  <main id="content">
    <!-- 对话视图 -->
    <div id="view-chat" class="view active" style="flex-direction:column">
      <!-- 内联 style 稍后迁移到 chat.css -->
      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div id="chat-messages" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px"></div>
        <div id="chat-input-area" style="display:flex;gap:8px;padding:12px 16px;background:var(--bg-secondary);border-top:1px solid var(--border-color);flex-shrink:0">
          <textarea id="chat-input" rows="1" placeholder="和云枢说话..."
            onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"
            style="flex:1;padding:10px 14px;border-radius:20px;border:1px solid var(--border-color);background:var(--bg-primary);color:var(--text-primary);font-size:14px;outline:none;resize:none;font-family:inherit;max-height:80px"></textarea>
          <button id="send-btn" onclick="sendMessage()" style="padding:8px 20px;border-radius:20px;border:none;background:#238636;color:#fff;font-size:14px;cursor:pointer;white-space:nowrap">发送</button>
        </div>
      </div>
    </div>

    <!-- 全景视图 -->
    <div id="view-panorama" class="view">
      [全景 DOM 保持不变，从当前 index.html #panorama-view 完整复制到这里]
    </div>

    <!-- 管理视图 -->
    <div id="view-history" class="view">
      <div class="view-body">
        <div class="view-title">🕐 历史会话</div>
        <div class="view-toolbar">
          <input class="view-search" id="history-search" placeholder="搜索历史记录..." oninput="filterHistory()">
          <select id="history-sort" class="btn-sm" onchange="loadHistory()">
            <option value="newest">最新优先</option>
            <option value="oldest">最早优先</option>
          </select>
        </div>
        <div id="history-list"></div>
      </div>
    </div>

    <div id="view-skills" class="view">
      <div class="view-body">
        <div class="view-title">🔧 技能管理</div>
        <div class="view-toolbar">
          <button class="btn-sm primary" onclick="showAddSkill()">+ 添加技能</button>
        </div>
        <div id="skills-list"></div>
      </div>
    </div>

    <div id="view-tools" class="view">
      <div class="view-body">
        <div class="view-title">🛠 工具集成</div>
        <div id="tools-count" style="font-size:12px;color:var(--text-secondary);margin-bottom:8px"></div>
        <div id="tools-list"></div>
      </div>
    </div>

    <div id="view-personality" class="view">
      <div class="view-body">
        <div class="view-title">🎭 人格配置</div>
        <div id="personality-presets"></div>
        <div id="personality-sliders"></div>
        <div class="view-toolbar" style="margin-top:12px">
          <button class="btn-sm primary" onclick="savePersonality()">💾 保存配置</button>
          <button class="btn-sm" onclick="resetPersonality()">↺ 恢复默认</button>
        </div>
      </div>
    </div>

    <div id="view-memory" class="view">
      <div class="view-body">
        <div class="view-title">🧠 记忆管理</div>
        <div class="view-toolbar">
          <button class="btn-sm primary" onclick="showAddMemory()">+ 手动添加</button>
          <button class="btn-sm" onclick="triggerCompression()">⚡ 触发压缩</button>
        </div>
        [memory subtabs 和内部 DOM 从当前 index.html 完整复制]
      </div>
    </div>
  </main>

  <!-- 右侧状态面板 -->
  <aside id="status-panel">
    <button class="panel-toggle" id="panel-toggle-btn" title="切换状态面板">◀</button>
    <div class="sp-section">
      <div class="sp-section-title">系统状态</div>
      <div class="sp-metric-row" id="sp-cpu-row">
        <span class="sp-label">CPU</span>
        <div class="sp-bar"><div class="sp-bar-fill" id="sp-cpu-bar" style="width:0%"></div></div>
        <span class="sp-value" id="sp-cpu">-</span>
      </div>
      <div class="sp-metric-row" id="sp-mem-row">
        <span class="sp-label">内存</span>
        <div class="sp-bar"><div class="sp-bar-fill" id="sp-mem-bar" style="width:0%"></div></div>
        <span class="sp-value" id="sp-memory">-</span>
      </div>
      <div class="sp-metric-row" id="sp-disk-row">
        <span class="sp-label">磁盘</span>
        <div class="sp-bar"><div class="sp-bar-fill" id="sp-disk-bar" style="width:0%"></div></div>
        <span class="sp-value" id="sp-disk">-</span>
      </div>
      <div class="sp-metric-row" id="sp-batt-row">
        <span class="sp-label">电池</span>
        <div class="sp-bar"><div class="sp-bar-fill" id="sp-batt-bar" style="width:0%"></div></div>
        <span class="sp-value" id="sp-battery">-</span>
      </div>
      <div class="sp-text-row"><span class="sp-label">网络</span><span class="sp-value" id="sp-network">-</span></div>
      <div class="sp-text-row"><span class="sp-label">传感器</span><span class="sp-value" id="sp-sensors">-</span></div>
      <div class="sp-text-row"><span class="sp-label">模式</span><span class="sp-value" id="sp-mode">-</span></div>
    </div>
    <div class="sp-section">
      <div class="sp-section-title">运行信息</div>
      <div class="sp-text-row"><span class="sp-label">会话</span><span class="sp-value" id="sp-session">-</span></div>
      <div class="sp-text-row"><span class="sp-label">交互</span><span class="sp-value" id="sp-interactions">-</span></div>
      <div class="sp-text-row"><span class="sp-label">运行</span><span class="sp-value" id="sp-uptime">-</span></div>
    </div>
    <div class="sp-section sp-events-section">
      <div class="sp-section-title">实时事件流</div>
      <div class="sp-events" id="sp-events">
        <div class="view-empty">系统正常</div>
      </div>
    </div>
  </aside>
</div>

<!-- 设置弹窗（不变） -->
<div id="settings-modal" class="modal-overlay" style="display:none" onclick="if(event.target===this)hideSettings()">
  [保持现有 settings-modal DOM 不变]
</div>

<!-- 追加内联全景样式 + inline script（保持现有聊天/设置/告警逻辑）-->
<style>
  [全景样式从当前 index.html 的 #panorama-view 相关 style 迁移到这里]
</style>

<script>
// 保持现有的 inline script（chat, settings, checkApiToken, safety alerts, setInterval）
// 但修改以下函数引用：
//   updateStatusPanel → app 不再引用 window.updateStatusPanel
//   addMessage → 不变
//   sendMessage → 不变
//   clearChat → 不变
//   showSettings/hideSettings/saveConfig → 不变
//   checkSafetyAlerts → 不变
//   loadPanorama → 不变

// 移除旧的 updateStatusPanel 函数（由 status-panel.js 接管）
// 移除旧的 switchNav 函数（由 app.switchView 接管）
// 移除 initSidebarResize、toggleSidebar、showSidebar（不再需要）
// 移除 loadSidebarModule（不再需要）

app.init();
initNav();

document.getElementById('chat-input').focus();

// 定时刷新（使用事件总线）
setInterval(() => {
  app.emit('tick');
  checkSafetyAlerts();
  // 全景自动刷新由 panorama.js 通过 app.on('tick') 处理
}, 10000);
</script>

<script src="{{ url_for('static', filename='js/app.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/nav.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/status-panel.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/history.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/skills.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/tools.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/personality.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/sidebar/memory.js') }}?v=20260529"></script>
<script src="{{ url_for('static', filename='js/panorama.js') }}?v=20260529"></script>
</body>
</html>
```

需要特别注意的迁移：
- 全景 DOM 从 `#panorama-view` → `#view-panorama`
- 管理视图从 `#history-view` (class `tab-view`) → `#view-history` (class `view`)
- 对话视图从 `#chat-view` (class `tab-view`) → `#view-chat` (class `view`)
- 删除 `#sidebar`、`#sidebar-show-btn`、`#body-row`、`#main` 旧容器
- 设置弹窗保持不变

- [ ] **Step 2: Commit**

```bash
git add templates/index.html
git commit -m "refactor: rewrite index.html with CSS Grid three-column layout"
```

---

### Task 10: 重写 status-panel.js — 状态面板逻辑（从 sidebar.js 拆分）

**Files:**
- Create: `static/js/sidebar/status-panel.js`
- Modify: `static/js/sidebar/sidebar.js` (移除状态面板相关函数)

- [ ] **Step 1: 编写 status-panel.js**

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · 右侧状态面板 - 指标 + 进度条 + 事件流
// ════════════════════════════════════════════════════════════

// ── 面板折叠切换 ──
function initStatusPanel() {
  const toggleBtn = document.getElementById('panel-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const appEl = document.getElementById('app');
      const collapsed = appEl.classList.toggle('panel-collapsed');
      app.setState('panelCollapsed', collapsed);
      toggleBtn.textContent = collapsed ? '▶' : '◀';
    });

    // 初始状态更新按钮文字
    if (app.state.panelCollapsed) {
      toggleBtn.textContent = '▶';
    }
  }

  // 立即执行首次更新
  updateStatusPanel();
}

async function updateStatusPanel() {
  try {
    const data = await app.get('/api/health');
    const healthMap = {};
    data.forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function setProgress(elId, barId, sensorKey, unit) {
      const valEl = document.getElementById(elId);
      const barEl = document.getElementById(barId);
      if (!valEl || !barEl) return;
      const d = healthMap[sensorKey];
      if (d) {
        const pct = Math.min(parseFloat(d.value) || 0, 100);
        valEl.textContent = pct + (unit || '');
        valEl.className = 'sp-value ' + (d.severity || 'normal');
        barEl.style.width = pct + '%';
        barEl.className = 'sp-bar-fill ' + (d.severity || 'normal');
      } else {
        valEl.textContent = '-';
        valEl.className = 'sp-value';
        barEl.style.width = '0%';
        barEl.className = 'sp-bar-fill';
      }
    }

    setProgress('sp-cpu', 'sp-cpu-bar', 'cpu_usage', '%');
    setProgress('sp-memory', 'sp-mem-bar', 'memory_usage', '%');
    setProgress('sp-disk', 'sp-disk-bar', 'disk_usage', '%');
    setProgress('sp-battery', 'sp-batt-bar', 'battery', '%');

    function setText(elId, sensorKey) {
      const el = document.getElementById(elId);
      if (!el) return;
      const d = healthMap[sensorKey];
      if (d) {
        el.textContent = d.value + (d.unit || '');
        el.className = 'sp-value ' + (d.severity || 'normal');
      } else {
        el.textContent = '-';
        el.className = 'sp-value';
      }
    }

    setText('sp-network', 'network');

    // 传感器计数
    try {
      const sensors = await app.get('/api/sensors');
      const on = sensors.filter(s => s.enabled).length;
      const spSensors = document.getElementById('sp-sensors');
      if (spSensors) {
        spSensors.textContent = on + '/' + sensors.length;
        spSensors.className = 'sp-value normal';
      }
    } catch(e) {}

    // 模式
    try {
      const mode = await app.get('/api/mode');
      const spMode = document.getElementById('sp-mode');
      if (spMode) {
        spMode.textContent = mode.label || mode.mode || '-';
        spMode.className = 'sp-value normal';
      }
    } catch(e) {}

    // 运行信息
    try {
      const pano = await app.get('/api/panorama');
      setTextById('sp-session', pano.session || '-');
      setTextById('sp-interactions', pano.interactions != null ? String(pano.interactions) : '-');
      setTextById('sp-uptime', pano.uptime || '-');
    } catch(e) {}

    updateEvents(healthMap);
  } catch(e) { console.error('Status panel error:', e); }
}

function setTextById(elId, text) {
  const el = document.getElementById(elId);
  if (el) {
    el.textContent = text;
    el.className = 'sp-value';
  }
}

function updateEvents(healthMap) {
  const el = document.getElementById('sp-events');
  if (!el) return;
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  const events = [];
  Object.entries(healthMap).forEach(([key, m]) => {
    if (m.severity === 'warning' || m.severity === 'critical') {
      events.push({
        time: timeStr,
        text: (m.description || key) + ': ' + m.value + (m.unit||''),
        severity: m.severity
      });
    }
  });

  if (events.length === 0) {
    el.innerHTML = '<div class="view-empty">系统正常</div>';
    return;
  }

  el.innerHTML = events.map(e =>
    '<div class="sp-event">' +
      '<span class="sp-event-time">' + e.time + '</span>' +
      '<span class="sp-event-text" style="color:' + (e.severity==='critical'?'var(--danger)':'var(--warning)') + '">⚠ ' + e.text + '</span>' +
    '</div>'
  ).join('');
}

// 订阅 tick 事件自动刷新
app.on('tick', updateStatusPanel);
```

- [ ] **Step 2: 精简 sidebar.js — 移除已拆分的函数**

从 `static/js/sidebar/sidebar.js` 中移除：
- `updateStatusPanel()` 全部代码
- `updateStatusEvents()` 全部代码
- `toggleSidebar()` 全部代码
- `initSidebarResize()` 全部代码
- `showSidebar()` 全部代码
- `switchNav()` 全部代码
- `loadSidebarModule()` 全部代码

保留：
- `showToast()` → 改为调用 `app.showToast()`
- `showConfirm()` → 改为调用 `app.showConfirm()`
- `escapeHtml()` → 改为调用 `app.escapeHtml()`
- `apiGet()` / `apiPost()` / `apiDelete()` → 改为调用 `app.get/post/del()`
- `refreshAll()` → 保留，内容改为 `app.emit('refresh')`

实际上，sidebar.js 已无存在的必要，历史/技能/工具/人格/记忆 各模块直接使用 `app.get/post/del` 和 `app.showConfirm/toast/escapeHtml`。sidebar.js 可连带删除，将其导出函数迁移到各视图文件中。

- [ ] **Step 3: Commit**

```bash
git add static/js/sidebar/status-panel.js static/js/sidebar/sidebar.js
git commit -m "refactor: split status-panel.js from sidebar.js, add progress bar support"
```

---

### Task 11: 适配管理视图 JS — 从浮层切换到主内容区

**Files:**
- Modify: `static/js/sidebar/history.js`
- Modify: `static/js/sidebar/skills.js`
- Modify: `static/js/sidebar/tools.js`
- Modify: `static/js/sidebar/personality.js`
- Modify: `static/js/sidebar/memory.js`

各文件的修改模式相同：

- [ ] **Step 1: 以 history.js 为例适配**

改动清单：
1. `apiGet('/api/history')` → `app.get('/api/history')`
2. `showConfirm()` → `app.showConfirm()`
3. `showToast()` → `app.showToast()`
4. `escapeHtml()` → `app.escapeHtml()`
5. 选择器 `#history-list` 不变（DOM ID 一致）
6. 删除 JS 文件中的 `sidebar-card` / `sidebar-card-header` 等 class 引用 → 改为 `view-card`
7. 删除 `sidebar-empty` → 改为 `view-empty`
8. 删除 `sidebar-search` → 改为 `view-search`
9. 删除 `loading` → 改为 `view-loading`
10. 删除 `sidebar-confirm-overlay` / `sidebar-confirm-box` → 改为 `confirm-overlay` / `confirm-box`

```javascript
// 修改后的 history.js
let _historyData = [];

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="view-loading">加载中...</div>';
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
      <div class="view-card-sub">${app.escapeHtml(entry.Yunshu || '').substring(0, 60)}</div>
      <div class="view-card-actions">
        <button onclick="showHistoryDetail(${entry.index})">📖 详情</button>
        <button onclick="deleteHistory(${entry.index})" style="color:var(--danger)">🗑 删除</button>
      </div>
    </div>
  `).join('');
}

function filterHistory() { loadHistory(); }

function showHistoryDetail(index) {
  const entry = _historyData.find(e => e.index === index);
  if (!entry) return;
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box" style="max-width:500px">
      <p style="font-size:13px;font-weight:600;color:var(--accent);margin-bottom:8px">📖 对话详情</p>
      <div style="background:var(--bg-primary);border-radius:6px;padding:10px;margin-bottom:10px">
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px">👤 用户:</div>
        <div style="font-size:13px;color:var(--text-primary);white-space:pre-wrap">${app.escapeHtml(entry.user)}</div>
      </div>
      <div style="background:var(--bg-primary);border-radius:6px;padding:10px;margin-bottom:12px">
        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px">🤖 云枢:</div>
        <div style="font-size:13px;color:var(--text-primary);white-space:pre-wrap">${app.escapeHtml(entry.Yunshu)}</div>
      </div>
      <div class="confirm-actions">
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
    await app.del('/api/history/' + index);
    app.showToast('已删除');
    loadHistory();
  } catch(e) {
    app.showToast('删除失败: ' + e.message, 'error');
  }
}
```

其余 4 个模块（skills、tools、personality、memory）同理：
- 替换 `apiGet/apiPost/apiDelete` → `app.get/post/del`
- 替换 `showConfirm/showToast/escapeHtml` → `app.showConfirm/showToast/escapeHtml`
- 替换 CSS class `sidebar-card*` → `view-card*`
- 替换 `sidebar-empty` → `view-empty`
- 替换 `sidebar-search` → `view-search`
- 替换 `loading` → `view-loading`

- [ ] **Step 2: Commit**

```bash
git add static/js/sidebar/history.js static/js/sidebar/skills.js static/js/sidebar/tools.js static/js/sidebar/personality.js static/js/sidebar/memory.js
git commit -m "refactor: adapt admin view JS to use app API and new CSS classes"
```

---

### Task 12: 适配 panorama.js — 新 DOM 结构

**Files:**
- Modify: `static/js/panorama.js`

- [ ] **Step 1: 适配 panorama.js**

改动清单：
1. 所有 `document.getElementById('panorama-view')` → `document.getElementById('view-panorama')`
2. `switchPanoSection()` 中 `querySelectorAll('.pano-detail-view')` → 不变（DOM ID 一致）
3. `loadPanorama()` 中 `setText()` → 可使用 `app.get()` 但保留现有逻辑也可
4. 检查是否通过 `#pano-section-dashboard` 等 ID 操作 DOM → 这些 ID 不变
5. 全景 DOM 已从 `#panorama-view` 迁移到 `#view-panorama`，JS 选择器需要对应更新

```javascript
// 修改 switchPanoSection 中的选择器
function switchPanoSection(section) {
  document.querySelectorAll('.pano-nav-item').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`.pano-nav-item[data-pano-section="${section}"]`);
  if (navItem) navItem.classList.add('active');

  const dashboard = document.getElementById('pano-section-dashboard');
  if (dashboard) dashboard.style.display = 'none';

  document.querySelectorAll('.pano-detail-view').forEach(el => el.style.display = 'none');

  if (section === 'dashboard') {
    if (dashboard) dashboard.style.display = 'block';
  } else {
    const target = document.getElementById('pano-section-' + section);
    if (target) {
      target.style.display = 'block';
      target.classList.add('active');
    }
  }

  loadPanorama();
}

// loadPanorama 中的 setText 辅助
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}
```

- [ ] **Step 2: 注册全景视图自动刷新**

在 `init()` 或通过 `app.on('tick')` 控制全景刷新：

```javascript
// 在 panorama.js 末尾添加
app.on('tick', () => {
  const panoView = document.getElementById('view-panorama');
  if (panoView && panoView.classList.contains('active')) {
    loadPanorama();
  }
});
```

- [ ] **Step 3: Commit**

```bash
git add static/js/panorama.js
git commit -m "refactor: adapt panorama.js for new view IDs and event bus tick"
```

---

### Task 13: 重写 responsive.css — 所有断点

**Files:**
- Modify: `static/css/responsive.css`

- [ ] **Step 1: 编写 responsive.css**

```css
/* ════════════════════════════════════════
   云枢 · 响应式布局断点
   ════════════════════════════════════════ */

/* 1000-1200px: 状态面板变窄 */
@media (max-width: 1200px) {
  :root { --panel-w: 180px; }
}

/* 768-1000px: 状态面板隐藏，可浮动显示 */
@media (max-width: 1000px) {
  #app { grid-template-columns: var(--nav-w) 1fr; }
  #status-panel { display: none; }
  #status-panel.show-floating {
    display: flex;
    position: fixed;
    right: 0;
    top: 0;
    bottom: 0;
    width: 220px;
    z-index: 60;
    box-shadow: -4px 0 16px rgba(0,0,0,0.3);
  }
}

/* < 768px: 导航栏自动折叠 */
@media (max-width: 768px) {
  :root { --nav-w: var(--nav-collapsed-w); }
  #nav { width: var(--nav-collapsed-w); }
  #nav .nav-title,
  #nav .nav-sub,
  #nav .nav-section-label,
  #nav .nav-btn .nav-label { display: none; }
  #nav .nav-brand { justify-content: center; padding: 10px 4px; }
  #nav .nav-btn { justify-content: center; padding: 8px 0; border-left: none; border-radius: 6px; width: 40px; margin: 0 auto; }
  #nav .nav-btn .nav-icon { width: auto; font-size: 18px; }
  #nav .nav-btn.active { border-left: none; outline: 2px solid var(--accent); }
  #nav .nav-items { align-items: center; padding: 4px; }
  #nav .nav-divider { margin: 6px 8px; }
  #nav .nav-collapse-btn { display: none; }

  #app { grid-template-columns: var(--nav-collapsed-w) 1fr; }
}

/* < 600px: 全景导航折叠为顶部横排 */
@media (max-width: 600px) {
  .pano-nav {
    flex-direction: row;
    overflow-x: auto;
    padding: 6px 8px;
    gap: 4px;
  }
  .pano-nav .pano-nav-item {
    white-space: nowrap;
    flex-shrink: 0;
    font-size: 11px;
    padding: 6px 10px;
  }
  .pano-content { padding: 8px; }
  .pano-dashboard-grid,
  .pano-dashboard-bottom {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add static/css/responsive.css
git commit -m "refactor: rewrite responsive.css with new grid breakpoints"
```

---

### Task 14: 精简 sidebar.css — 移除导航栏样式（已迁移到 nav.css）

**Files:**
- Modify: `static/css/sidebar.css`

- [ ] **Step 1: 从 sidebar.css 移除已迁移的样式**

删除：
- `.sidebar-header`、`.sidebar-toggle`、`.sidebar-logo`、`.sidebar-title`、`.sidebar-sub`、`.sidebar-brand-text`（导航品牌区→nav.css）
- `.sidebar-nav`、`.sidebar-nav-btn`、`.sidebar-nav-btn.active`、`.sidebar-nav-btn .nav-icon`、`.sidebar-nav-btn .nav-label`（导航按钮→nav.css）
- `.sidebar-divider`、`.sidebar-section-label`（分隔线→nav.css）
- `.sidebar-spacer`、`.sidebar-footer`、`.sidebar-footer-btn`（页脚→nav.css 折叠按钮）
- `.sidebar-resize-handle`、`#sidebar.collapsed` 系列、`#sidebar.hidden` 系列、`.sidebar-show-btn`（折叠/隐藏逻辑→nav.css）

保留：
- `.toggle-switch`（开关组件）
- `.slider-group`（滑动条组件）
- `.badge`（状态徽标 → views.css 已重建，可删除冗余）
- `.sidebar-toast` → `.toast`（已在 modals.css）
- `.sidebar-confirm-overlay` → `.confirm-overlay`（已在 modals.css）
- `.sidebar-confirm-box` → `.confirm-box`（已在 modals.css）
- `.sidebar-empty` → `.view-empty`（已在 views.css）
- `.sidebar-card` 系列 → `.view-card`（已在 views.css）
- `.btn-sm`（已在 views.css）
- 窗口监控样式（`.window-*`、`.memory-*` 系列）— 保留，因为这些样式没有被迁移

最终 sidebar.css 保留内容：
- `.toggle-switch`、`.slider-group`（通用组件）
- 全部 `.window-*` 样式（窗口监控）
- 全部 `.memory-*` 样式（记忆子页标签）
- `.view-body`（已在 views.css 重建，可删除）

- [ ] **Step 2: Commit**

```bash
git add static/css/sidebar.css
git commit -m "refactor: remove migrated nav/layout styles from sidebar.css"
```

---

### Task 15: 注册视图 — 更新 index.html inline script

**Files:**
- Modify: `templates/index.html` (inline script 段)

- [ ] **Step 1: 在 inline script 中注册所有视图**

将 `app.init()` 前或后的位置，添加视图注册：

```javascript
// 注册视图（keepAlive 保留 DOM 避免重复加载）
app.registerView('chat', { keepAlive: true });
app.registerView('panorama', { keepAlive: true });
app.registerView('history', { load: loadHistory, keepAlive: true });
app.registerView('skills', { load: loadSkills, keepAlive: true });
app.registerView('tools', { load: loadTools, keepAlive: true });
app.registerView('personality', { load: loadPersonality, keepAlive: true });
app.registerView('memory', { load: loadMemory, keepAlive: true });

app.init();
initNav();
initStatusPanel();
```

- [ ] **Step 2: 删除旧的内联函数**

从 inline script 中删除：
- `updateStatusPanel()` 函数定义（由 status-panel.js 接管）
- `updateStatusEvents()` 函数定义
- `switchNav()` 函数定义
- `toggleSidebar()`、`showSidebar()`、`initSidebarResize()` 函数定义
- `loadSidebarModule()` 函数定义
- `apiGet()`、`apiPost()`、`apiDelete()` 函数定义（由 app.js 接管）

保留的 inline 函数：
- `checkApiToken()`、`_apiHeaders()`、`apiFetch()`（令牌管理）
- `showNotice()`（通知条）
- `showSettings()`、`hideSettings()`、`updateModelHint()`、`saveConfig()`（设置弹窗）
- `sendMessage()`、`addMessage()`、`clearChat()`（聊天逻辑）
- `checkSafetyAlerts()`（安全告警）

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "refactor: register views with app.js, clean up old inline functions"
```

---

### Task 16: 精简 panorama.css — 移除冗余

**Files:**
- Modify: `static/css/panorama.css`

- [ ] **Step 1: 审查并精简 panorama.css**

删除已由 base.css/views.css 覆盖的样式：
- 滚动条样式
- `.status-badge` 系列
- `.btn-sm` 系列
- `.loading` 样式

保留所有全景特有的样式：
- `.pano-nav`、`.pano-nav-item`、`.pano-nav-indicator`
- `.pano-content`、`.pano-detail-view`
- `.pano-pipeline`、`.pipe-node`、`.pipe-arrow`
- `.pano-dashboard-grid`、`.pano-card`、`.pano-card-header`、`.pano-card-body`
- `.pano-metrics`、`.pano-metric`
- `.pano-cat-grid`、`.pano-tag-grid`、`.pano-sensor-list`
- `.pano-mode-grid`、`.pano-mode-item`
- `.pano-pre`、`.pano-text`、`.pano-stat`
- `.pano-divider`、`.detail-section-title`
- `.pano-sysbar`、`.pano-sys-grid`
- `.pano-events`、`.pano-events-header`、`.pano-events-body`

- [ ] **Step 2: Commit**

```bash
git add static/css/panorama.css
git commit -m "refactor: remove redundant styles from panorama.css"
```

---

### Task 17: 集成测试 — 运行并验证

**Files:**
- 无文件改动

- [ ] **Step 1: 启动服务器并验证**

```bash
python main.py 2>/dev/null &
sleep 2
# 检查服务器是否启动
curl -s http://localhost:5000/api/health | head -c 200
```

- [ ] **Step 2: 验证页面加载**

```bash
# 验证 HTML 能正常返回
curl -s http://localhost:5000/ | head -c 500
```

- [ ] **Step 3: 手动验证清单**

1. 页面加载无 JS 报错
2. 左侧导航栏点击切换所有视图（对话、全景、5 个管理模块）
3. 右侧状态面板显示指标和进度条
4. 点击面板折叠按钮 → 状态面板隐藏，主内容扩展 → 再次点击恢复
5. 管理视图（历史/技能/工具/人格/记忆）数据正常加载
6. 全景视图数据正常
7. 发送聊天消息正常
8. 设置弹窗功能正常
9. 缩放浏览器窗口到 < 768px → 导航栏自动折叠
10. 缩放浏览器窗口到 < 1000px → 状态面板自动隐藏

- [ ] **Step 4: 修复发现的问题并提交最终 commit**

```bash
git add -A
git commit -m "fix: resolve layout issues from integration testing"
```
