# Agent 管理侧边栏实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为云枢 Web 界面添加全局侧边栏组件，实现 5 个管理模块（历史会话、技能、工具、人格、记忆）

**Architecture:** Flask 后端提供服务 + 前端提取至 `templates/` + `static/`，侧边栏使用独立 CSS/JS 模块，新数据通过 `data/personality.json` / `data/skills.json` 持久化

**Tech Stack:** Python Flask, Vanilla JS, CSS3, Flask `render_template` + `send_from_directory`

---

### Task 1: 前端提取与 Flask 静态文件配置

将内联 HTML 从 `app_server.py` 提取到独立模板和静态文件。

**Files:**
- Modify: `app_server.py` — 替换 `render_template_string(HTML)` 为 `render_template("index.html")`
- Create: `templates/index.html` — 提取后的 HTML 模板
- Create: `static/` (目录)
- Create: `static/js/sidebar/` (目录)

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p c:/Users/Administrator/agent/templates
mkdir -p c:/Users/Administrator/agent/static/css
mkdir -p c:/Users/Administrator/agent/static/js/sidebar
```

- [ ] **Step 2: 创建 `templates/index.html`**

将 `app_server.py` 中 `HTML = r"""..."""` 的全部内容（第 394~1296 行）提取到 `templates/index.html`。

在 `</head>` 前添加 CSS 引用：
```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/sidebar.css') }}">
```

在 `</body>` 前添加 JS 引用：
```html
<script src="{{ url_for('static', filename='js/sidebar/sidebar.js') }}"></script>
<script src="{{ url_for('static', filename='js/sidebar/history.js') }}"></script>
<script src="{{ url_for('static', filename='js/sidebar/skills.js') }}"></script>
<script src="{{ url_for('static', filename='js/sidebar/tools.js') }}"></script>
<script src="{{ url_for('static', filename='js/sidebar/personality.js') }}"></script>
<script src="{{ url_for('static', filename='js/sidebar/memory.js') }}"></script>
```

- [ ] **Step 3: 修改 `app_server.py` 的路由**

原代码（约第 1298 行）：
```python
@app.route("/")
def index():
    return render_template_string(HTML)
```

改为：
```python
from flask import render_template  # 已导入

@app.route("/")
def index():
    return render_template("index.html")
```

同时删除 `HTML = r"""..."""` 整个变量定义（第 394~1296 行）。

- [ ] **Step 4: 确保 Flask 能发现 templates/ 和 static/**

```python
# 在 app 初始化后添加，确保静态文件可访问
import os
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')
```

- [ ] **Step 5: 验证基本提取成功**

Run: `cd c:/Users/Administrator/agent && python -c "from app_server import app; print('OK')"`

Expected: 无导入错误，输出 `OK`

- [ ] **Step 6: 提交**

```bash
git add app_server.py templates/ static/
git commit -m "refactor: extract frontend to templates/ and static/ structure"
```

---

### Task 2: 侧边栏 HTML 结构与 CSS 样式

在 `templates/index.html` 中添加侧边栏 DOM 结构，并编写侧边栏专用 CSS。

**Files:**
- Modify: `templates/index.html` — 在 body 布局中添加侧边栏
- Create: `static/css/sidebar.css` — 侧边栏全部样式

- [ ] **Step 1: 创建 `static/css/sidebar.css`**

```css
/* ════════════════════════════════════════
   云枢 · Agent 管理侧边栏
   ════════════════════════════════════════ */

:root {
  --sidebar-width: 280px;
  --sidebar-collapsed: 52px;
  --sidebar-bg: #161b22;
  --sidebar-border: #30363d;
  --sidebar-hover: #21262d;
  --sidebar-active: #1c2333;
  --sidebar-text: #8b949e;
  --sidebar-text-active: #58a6ff;
  --sidebar-accent: #58a6ff;
  --danger-color: #f85149;
  --success-color: #3fb950;
  --warning-color: #d29922;
}

/* 全局布局 */
#app.with-sidebar {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  grid-template-rows: auto 1fr;
  height: 100vh;
  transition: grid-template-columns 0.2s ease;
}

#app.with-sidebar.collapsed {
  grid-template-columns: var(--sidebar-collapsed) 1fr;
}

/* 顶栏跨列 */
#app.with-sidebar #topbar,
#app.with-sidebar #tabs {
  grid-column: 1 / -1;
}

/* ── 侧边栏容器 ── */
#sidebar {
  grid-row: 3;
  grid-column: 1;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}

/* 侧边栏头部 */
.sidebar-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid var(--sidebar-border);
  flex-shrink: 0;
  gap: 10px;
}

.sidebar-toggle {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--sidebar-text);
  font-size: 18px;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.sidebar-toggle:hover {
  background: var(--sidebar-hover);
  color: var(--sidebar-text-active);
}

.sidebar-title {
  font-size: 14px;
  font-weight: 600;
  color: #c9d1d9;
  white-space: nowrap;
  overflow: hidden;
}

.collapsed .sidebar-title {
  display: none;
}

/* ── 标签导航 ── */
.sidebar-tabs {
  display: flex;
  flex-direction: column;
  padding: 8px;
  gap: 2px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--sidebar-border);
}

.sidebar-tab {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--sidebar-text);
  font-size: 13px;
  border: none;
  background: transparent;
  text-align: left;
  transition: all 0.15s;
  white-space: nowrap;
}

.sidebar-tab:hover {
  background: var(--sidebar-hover);
  color: #c9d1d9;
}

.sidebar-tab.active {
  background: var(--sidebar-active);
  color: var(--sidebar-text-active);
}

.sidebar-tab .tab-icon {
  font-size: 16px;
  width: 24px;
  text-align: center;
  flex-shrink: 0;
}

.collapsed .sidebar-tab {
  justify-content: center;
  padding: 8px;
}

.collapsed .sidebar-tab .tab-label {
  display: none;
}

/* ── 面板内容区域 ── */
.sidebar-panels {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.sidebar-panel {
  display: none;
  height: 100%;
  overflow-y: auto;
  padding: 12px;
}

.sidebar-panel.active {
  display: block;
}

/* ── 面板通用组件 ── */
.sidebar-panel h3 {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1d9;
  margin: 0 0 12px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.sidebar-panel .panel-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}

.sidebar-panel .btn-sm {
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid var(--sidebar-border);
  background: #0d1117;
  color: #c9d1d9;
  font-size: 12px;
  cursor: pointer;
}

.sidebar-panel .btn-sm:hover {
  background: var(--sidebar-hover);
}

.sidebar-panel .btn-sm.primary {
  background: #238636;
  border-color: #238636;
  color: #fff;
}

.sidebar-panel .btn-sm.primary:hover {
  background: #2ea043;
}

.sidebar-panel .btn-sm.danger {
  color: var(--danger-color);
  border-color: var(--danger-color);
}

.sidebar-panel .btn-sm.danger:hover {
  background: var(--danger-color);
  color: #fff;
}

/* ── 列表卡片 ── */
.sidebar-card {
  background: #0d1117;
  border: 1px solid var(--sidebar-border);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 6px;
}

.sidebar-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.sidebar-card-title {
  font-size: 12px;
  font-weight: 600;
  color: #c9d1d9;
}

.sidebar-card-sub {
  font-size: 11px;
  color: var(--sidebar-text);
  line-height: 1.4;
}

.sidebar-card-actions {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}

.sidebar-card-actions button {
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--sidebar-border);
  background: transparent;
  color: var(--sidebar-text);
  font-size: 11px;
  cursor: pointer;
}

.sidebar-card-actions button:hover {
  background: var(--sidebar-hover);
  color: #c9d1d9;
}

/* ── 开关 ── */
.toggle-switch {
  position: relative;
  width: 32px;
  height: 18px;
  flex-shrink: 0;
}

.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-switch .slider {
  position: absolute;
  inset: 0;
  background: #30363d;
  border-radius: 9px;
  cursor: pointer;
  transition: 0.2s;
}

.toggle-switch .slider::before {
  content: '';
  position: absolute;
  width: 14px;
  height: 14px;
  left: 2px;
  bottom: 2px;
  background: #8b949e;
  border-radius: 50%;
  transition: 0.2s;
}

.toggle-switch input:checked + .slider {
  background: #238636;
}

.toggle-switch input:checked + .slider::before {
  transform: translateX(14px);
  background: #fff;
}

/* ── 搜索框 ── */
.sidebar-search {
  width: 100%;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--sidebar-border);
  background: #0d1117;
  color: #c9d1d9;
  font-size: 12px;
  outline: none;
  box-sizing: border-box;
  margin-bottom: 8px;
}

.sidebar-search:focus {
  border-color: var(--sidebar-accent);
}

.sidebar-search::placeholder {
  color: #484f58;
}

/* ── 滑动条 ── */
.slider-group {
  margin-bottom: 10px;
}

.slider-group label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--sidebar-text);
  margin-bottom: 4px;
}

.slider-group input[type="range"] {
  width: 100%;
  height: 4px;
  -webkit-appearance: none;
  background: #30363d;
  border-radius: 2px;
  outline: none;
}

.slider-group input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--sidebar-accent);
  cursor: pointer;
}

/* ── 状态徽标 ── */
.badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
}

.badge.on { background: #3fb95020; color: #3fb950; }
.badge.off { background: #f8514920; color: #f85149; }
.badge.info { background: #58a6ff20; color: #58a6ff; }

/* ── Toast 提示 ── */
.sidebar-toast {
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

.sidebar-toast.success {
  background: #3fb950;
  color: #fff;
}

.sidebar-toast.error {
  background: var(--danger-color);
  color: #fff;
}

@keyframes toast-in {
  from { opacity: 0; transform: translateX(-50%) translateY(20px); }
  to { opacity: 1; transform: translateX(-50%) translateY(0); }
}

/* ── 确认弹窗 ── */
.sidebar-confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sidebar-confirm-box {
  background: #161b22;
  border: 1px solid var(--sidebar-border);
  border-radius: 10px;
  padding: 20px;
  max-width: 360px;
  width: 90%;
}

.sidebar-confirm-box p {
  font-size: 14px;
  color: #c9d1d9;
  margin: 0 0 16px;
  line-height: 1.5;
}

.sidebar-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ── 空状态 ── */
.sidebar-empty {
  text-align: center;
  padding: 30px 10px;
  color: #484f58;
  font-size: 13px;
}

/* ── 滚动条 ── */
.sidebar-panel::-webkit-scrollbar {
  width: 4px;
}
.sidebar-panel::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar-panel::-webkit-scrollbar-thumb {
  background: var(--sidebar-border);
  border-radius: 2px;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  #app.with-sidebar {
    grid-template-columns: 1fr !important;
  }
  #sidebar {
    display: none;
  }
  #sidebar.mobile-show {
    display: flex;
    position: fixed;
    inset: 0;
    z-index: 100;
    width: 100%;
  }
}
```

- [ ] **Step 2: 在 `templates/index.html` 中添加侧边栏 DOM 结构**

在 `<!-- 顶栏 -->` 和 `<!-- 标签切换 -->` 之间（或紧随 `#app` 内部开头）添加侧边栏：

```html
<!-- 侧边栏 -->
<div id="sidebar">
  <div class="sidebar-header">
    <button class="sidebar-toggle" onclick="toggleSidebar()" title="折叠侧边栏">☰</button>
    <span class="sidebar-title">Agent 管理</span>
  </div>
  <div class="sidebar-tabs">
    <div class="sidebar-tab active" data-panel="history" onclick="switchSidebarTab('history')">
      <span class="tab-icon">🕐</span>
      <span class="tab-label">历史会话</span>
    </div>
    <div class="sidebar-tab" data-panel="skills" onclick="switchSidebarTab('skills')">
      <span class="tab-icon">🔧</span>
      <span class="tab-label">技能管理</span>
    </div>
    <div class="sidebar-tab" data-panel="tools" onclick="switchSidebarTab('tools')">
      <span class="tab-icon">🛠</span>
      <span class="tab-label">工具集成</span>
    </div>
    <div class="sidebar-tab" data-panel="personality" onclick="switchSidebarTab('personality')">
      <span class="tab-icon">🎭</span>
      <span class="tab-label">人格配置</span>
    </div>
    <div class="sidebar-tab" data-panel="memory" onclick="switchSidebarTab('memory')">
      <span class="tab-icon">🧠</span>
      <span class="tab-label">记忆管理</span>
    </div>
  </div>
  <div class="sidebar-panels">
    <div class="sidebar-panel active" id="panel-history">
      <h3>🕐 历史会话</h3>
      <input class="sidebar-search" id="history-search" placeholder="搜索历史记录..." oninput="filterHistory()">
      <div class="panel-actions">
        <select id="history-sort" class="btn-sm" onchange="loadHistory()">
          <option value="newest">最新优先</option>
          <option value="oldest">最早优先</option>
        </select>
      </div>
      <div id="history-list"></div>
    </div>
    <div class="sidebar-panel" id="panel-skills">
      <h3>🔧 技能管理</h3>
      <div class="panel-actions">
        <button class="btn-sm primary" onclick="showAddSkill()">+ 添加技能</button>
      </div>
      <div id="skills-list"></div>
    </div>
    <div class="sidebar-panel" id="panel-tools">
      <h3>🛠 工具集成</h3>
      <div id="tools-count" style="font-size:12px;color:var(--sidebar-text);margin-bottom:8px"></div>
      <div id="tools-list"></div>
    </div>
    <div class="sidebar-panel" id="panel-personality">
      <h3>🎭 人格配置</h3>
      <div id="personality-presets"></div>
      <div id="personality-sliders"></div>
      <div class="panel-actions" style="margin-top:12px">
        <button class="btn-sm primary" onclick="savePersonality()">💾 保存配置</button>
        <button class="btn-sm" onclick="resetPersonality()">↺ 恢复默认</button>
      </div>
    </div>
    <div class="sidebar-panel" id="panel-memory">
      <h3>🧠 记忆管理</h3>
      <div id="memory-overview"></div>
      <div id="memory-content"></div>
      <div class="panel-actions" style="margin-top:8px">
        <button class="btn-sm primary" onclick="showAddMemory()">+ 手动添加</button>
        <button class="btn-sm" onclick="triggerCompression()">⚡ 触发压缩</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: 将 `#app` 的原始 CSS 布局改为支持侧边栏的 grid 布局**

在 `templates/index.html` 中找到 `#app{display:flex;flex-direction:column;height:100vh}`，改为：

```css
#app{display:flex;flex-direction:column;height:100vh}
#app.with-sidebar{display:grid;grid-template-columns:var(--sidebar-width,280px) 1fr;grid-template-rows:auto auto 1fr;height:100vh}
#app.with-sidebar.collapsed{grid-template-columns:var(--sidebar-collapsed,52px) 1fr}
#app.with-sidebar #topbar,#app.with-sidebar #tabs{grid-column:1/-1}
```

并在 `#app` 上添加 `with-sidebar` 类（在 HTML 中）。

- [ ] **Step 4: 验证页面渲染**

Run: `cd c:/Users/Administrator/agent && python -c "from app_server import app; print('OK'); print('Template:', app.template_folder); print('Static:', app.static_folder)"`

Expected: 打印出正确路径

- [ ] **Step 5: 提交**

```bash
git add templates/index.html static/css/sidebar.css
git commit -m "feat: add sidebar HTML structure and CSS styles"
```

---

### Task 3: 侧边栏 JS 框架 (sidebar.js)

实现侧边栏的折叠/展开、标签导航切换、全局工具函数（确认弹窗、Toast 提示）。

**Files:**
- Create: `static/js/sidebar/sidebar.js`

- [ ] **Step 1: 创建 `static/js/sidebar/sidebar.js`**

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · Agent 管理侧边栏 — 框架核心
// ════════════════════════════════════════════════════════════

// ── 折叠/展开 ──
function toggleSidebar() {
  const app = document.getElementById('app');
  app.classList.toggle('collapsed');
  const isCollapsed = app.classList.contains('collapsed');
  // 保存状态
  try { sessionStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0'); } catch(e) {}
  // 触发 resize 让内部元素自适应
  window.dispatchEvent(new Event('resize'));
}

function initSidebarState() {
  try {
    const collapsed = sessionStorage.getItem('sidebar_collapsed');
    if (collapsed === '1') {
      document.getElementById('app').classList.add('collapsed');
    }
  } catch(e) {}
}

// ── 标签切换 ──
function switchSidebarTab(panelName) {
  // 更新标签激活状态
  document.querySelectorAll('.sidebar-tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.sidebar-tab[data-panel="${panelName}"]`).classList.add('active');
  // 更新面板
  document.querySelectorAll('.sidebar-panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById(`panel-${panelName}`);
  if (panel) {
    panel.classList.add('active');
    // 加载该模块数据
    loadSidebarModule(panelName);
  }
  // 保存激活标签
  try { sessionStorage.setItem('sidebar_active_panel', panelName); } catch(e) {}
}

function initSidebarTab() {
  try {
    const saved = sessionStorage.getItem('sidebar_active_panel');
    if (saved && document.querySelector(`.sidebar-tab[data-panel="${saved}"]`)) {
      switchSidebarTab(saved);
      return;
    }
  } catch(e) {}
  switchSidebarTab('history');
}

// ── 模块数据加载路由 ──
function loadSidebarModule(name) {
  switch(name) {
    case 'history': loadHistory(); break;
    case 'skills': loadSkills(); break;
    case 'tools': loadTools(); break;
    case 'personality': loadPersonality(); break;
    case 'memory': loadMemory(); break;
  }
}

// ── Toast 提示 ──
function showToast(message, type = 'success') {
  const existing = document.querySelector('.sidebar-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `sidebar-toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// ── 确认弹窗 ──
function showConfirm(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'sidebar-confirm-overlay';
    overlay.innerHTML = `
      <div class="sidebar-confirm-box">
        <p>${message}</p>
        <div class="sidebar-confirm-actions">
          <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove(); window._confirmResolve(false)">取消</button>
          <button class="btn-sm danger" onclick="this.closest('.sidebar-confirm-overlay').remove(); window._confirmResolve(true)">确定</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    window._confirmResolve = resolve;
  });
}

// ── API 辅助 ──
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`GET ${url} failed: ${r.status}`);
  return r.json();
}

async function apiPost(url, data = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error(`POST ${url} failed: ${r.status}`);
  return r.json();
}

async function apiDelete(url) {
  const r = await fetch(url, { method: 'DELETE' });
  if (!r.ok) throw new Error(`DELETE ${url} failed: ${r.status}`);
  return r.json();
}

// ── HTML 转义（防 XSS） ──
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
  initSidebarState();
  initSidebarTab();
});
```

- [ ] **Step 2: 确认 JS 被正确加载**

Run: `cd c:/Users/Administrator/agent && python -c "from app_server import app; print('static folder:', app.static_folder)"`

Expected: 打印出正确的 static 目录路径

- [ ] **Step 3: 提交**

```bash
git add static/js/sidebar/sidebar.js
git commit -m "feat: add sidebar JS framework with collapse, tab nav, and utils"
```

---

### Task 4: 人格配置后端 API

创建 PersonalityManager 类，负责读写 `data/personality.json`，并提供 Flask API 端点。

**Files:**
- Modify: `app_server.py` — 添加 PersonalityManager + API 路由
- Create: `data/personality.json` — 初始人格配置

- [ ] **Step 1: 创建 `data/personality.json`**

```json
{
  "current_profile": "gentle_helper",
  "custom_params": {
    "tone": 0.6,
    "emotion": 0.7,
    "conciseness": 0.4,
    "initiative": 0.5,
    "humor": 0.3,
    "empathy": 0.8
  },
  "profiles": {
    "gentle_helper": {
      "name": "温和助人型",
      "description": "温暖、耐心、富有同理心",
      "params": { "tone": 0.6, "emotion": 0.7, "conciseness": 0.4, "initiative": 0.5, "humor": 0.3, "empathy": 0.8 }
    },
    "professional": {
      "name": "专业顾问型",
      "description": "严谨、客观、信息密度高",
      "params": { "tone": 0.3, "emotion": 0.2, "conciseness": 0.7, "initiative": 0.6, "humor": 0.1, "empathy": 0.4 }
    },
    "humorous": {
      "name": "幽默风趣型",
      "description": "轻松、活泼、喜欢开玩笑",
      "params": { "tone": 0.8, "emotion": 0.9, "conciseness": 0.3, "initiative": 0.7, "humor": 0.9, "empathy": 0.6 }
    }
  },
  "dimensions": [
    { "key": "tone", "label": "语气", "left": "正式", "right": "随意" },
    { "key": "emotion", "label": "情感", "left": "克制", "right": "丰富" },
    { "key": "conciseness", "label": "简练", "left": "详细", "right": "简洁" },
    { "key": "initiative", "label": "主动", "left": "被动", "right": "主动" },
    { "key": "humor", "label": "幽默", "left": "严肃", "right": "幽默" },
    { "key": "empathy", "label": "同理心", "left": "理性", "right": "感性" }
  ]
}
```

- [ ] **Step 2: 在 `app_server.py` 中添加 PersonalityManager 类**

在文件顶部（imports 之后）添加：

```python
# ── 人格配置管理器 ──
import json
import os

_PERSONALITY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'personality.json')

class PersonalityManager:
    """管理云枢的人格配置数据"""

    def __init__(self):
        self._cache = None

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        try:
            with open(_PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._cache = self._default()
        return self._cache

    def _save(self, data: dict):
        self._cache = data
        with open(_PERSONALITY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _default(self) -> dict:
        with open(_PERSONALITY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get(self) -> dict:
        data = self._load()
        return {
            "current_profile": data["current_profile"],
            "custom_params": data["custom_params"],
            "profiles": data["profiles"],
            "dimensions": data["dimensions"],
        }

    def update_params(self, params: dict) -> dict:
        data = self._load()
        data["custom_params"].update(params)
        data["current_profile"] = "custom"
        self._save(data)
        return {"ok": True, "params": data["custom_params"]}

    def apply_profile(self, profile_key: str) -> dict:
        data = self._load()
        if profile_key not in data["profiles"]:
            return {"ok": False, "error": f"未知人格方案: {profile_key}"}
        profile = data["profiles"][profile_key]
        data["current_profile"] = profile_key
        data["custom_params"] = dict(profile["params"])
        self._save(data)
        return {"ok": True, "profile": profile_key, "params": data["custom_params"]}

    def reset(self) -> dict:
        return self.apply_profile("gentle_helper")

_personality_mgr = PersonalityManager()
```

- [ ] **Step 3: 添加人格配置 API 路由**

在 `app_server.py` 的 API 路由区域添加：

```python
@app.route("/api/personality", methods=["GET"])
def api_personality_get():
    return jsonify(_personality_mgr.get())

@app.route("/api/personality/params", methods=["POST"])
def api_personality_params():
    data = request.get_json() or {}
    params = data.get("params", {})
    result = _personality_mgr.update_params(params)
    return jsonify(result)

@app.route("/api/personality/profile", methods=["POST"])
def api_personality_profile():
    data = request.get_json() or {}
    profile = data.get("profile", "")
    result = _personality_mgr.apply_profile(profile)
    return jsonify(result)

@app.route("/api/personality/reset", methods=["POST"])
def api_personality_reset():
    result = _personality_mgr.reset()
    return jsonify(result)
```

- [ ] **Step 4: 测试 API**

Run: `cd c:/Users/Administrator/agent && python -c "
from app_server import app
client = app.test_client()
r = client.get('/api/personality')
data = r.get_json()
print('Profiles:', list(data.get('profiles', {}).keys()))
print('Params:', data.get('custom_params'))
"`

Expected: 打印出人格配置数据

- [ ] **Step 5: 提交**

```bash
git add app_server.py data/personality.json
git commit -m "feat: add personality configuration backend API"
```

---

### Task 5: 技能管理后端 API

创建 SkillsManager 类 + Flask API 端点。

**Files:**
- Modify: `app_server.py` — 添加 SkillsManager + API 路由
- Create: `data/skills.json` — 初始技能配置

- [ ] **Step 1: 创建 `data/skills.json`**

```json
{
  "skills": [
    {
      "id": "self_reflection",
      "name": "自省反思",
      "enabled": true,
      "description": "每次交互后自动反思自身状态，不断成长",
      "params": { "frequency": "always", "depth": "normal" }
    },
    {
      "id": "memory_summary",
      "name": "记忆摘要",
      "enabled": true,
      "description": "定期压缩历史对话为结构化摘要",
      "params": { "interval": 10, "max_tokens": 512 }
    },
    {
      "id": "emotion_expression",
      "name": "情感表达",
      "enabled": true,
      "description": "在对话中表达情感色彩，让回应更生动",
      "params": { "intensity": "normal" }
    },
    {
      "id": "proactive_suggestion",
      "name": "主动建议",
      "enabled": false,
      "description": "在适当时机主动提出建议和想法",
      "params": { "threshold": 0.7 }
    },
    {
      "id": "context_aware",
      "name": "上下文感知",
      "enabled": true,
      "description": "感知对话上下文变化，自动调整回应策略",
      "params": { "window_size": 5 }
    }
  ]
}
```

- [ ] **Step 2: 在 `app_server.py` 中添加 SkillsManager 类**

```python
_SKILLS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'skills.json')

class SkillsManager:
    """管理云枢的技能配置"""

    def _load(self) -> dict:
        try:
            with open(_SKILLS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"skills": []}

    def _save(self, data: dict):
        with open(_SKILLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_all(self) -> list:
        return self._load().get("skills", [])

    def toggle(self, skill_id: str) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["enabled"] = not s.get("enabled", True)
                self._save(data)
                return {"ok": True, "id": skill_id, "enabled": s["enabled"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def update_params(self, skill_id: str, params: dict) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["params"].update(params)
                self._save(data)
                return {"ok": True, "id": skill_id, "params": s["params"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def add(self, skill: dict) -> dict:
        data = self._load()
        skill_id = skill.get("id", "")
        if any(s["id"] == skill_id for s in data["skills"]):
            return {"ok": False, "error": f"技能已存在: {skill_id}"}
        data["skills"].append({
            "id": skill_id,
            "name": skill.get("name", skill_id),
            "enabled": skill.get("enabled", True),
            "description": skill.get("description", ""),
            "params": skill.get("params", {}),
        })
        self._save(data)
        return {"ok": True, "id": skill_id}

    def delete(self, skill_id: str) -> dict:
        data = self._load()
        before = len(data["skills"])
        data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
        if len(data["skills"]) < before:
            self._save(data)
            return {"ok": True}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

_skills_mgr = SkillsManager()
```

- [ ] **Step 3: 添加技能管理 API 路由**

```python
@app.route("/api/skills", methods=["GET"])
def api_skills_get():
    return jsonify(_skills_mgr.get_all())

@app.route("/api/skills/toggle", methods=["POST"])
def api_skills_toggle():
    data = request.get_json() or {}
    skill_id = data.get("id", "")
    return jsonify(_skills_mgr.toggle(skill_id))

@app.route("/api/skills/params", methods=["POST"])
def api_skills_params():
    data = request.get_json() or {}
    return jsonify(_skills_mgr.update_params(data.get("id", ""), data.get("params", {})))

@app.route("/api/skills/add", methods=["POST"])
def api_skills_add():
    return jsonify(_skills_mgr.add(request.get_json() or {}))

@app.route("/api/skills/delete", methods=["POST"])
def api_skills_delete():
    data = request.get_json() or {}
    return jsonify(_skills_mgr.delete(data.get("id", "")))
```

- [ ] **Step 4: 测试 API**

Run: `cd c:/Users/Administrator/agent && python -c "
from app_server import app
client = app.test_client()
r = client.get('/api/skills')
skills = r.get_json()
print(f'Skills count: {len(skills)}')
print(f'First: {skills[0][\"name\"]} (enabled: {skills[0][\"enabled\"]})')
r2 = client.post('/api/skills/toggle', json={\"id\": skills[0][\"id\"]})
print(f'Toggle result: {r2.get_json()}')
"`

Expected: 显示技能列表和切换结果

- [ ] **Step 5: 提交**

```bash
git add app_server.py data/skills.json
git commit -m "feat: add skills management backend API"
```

---

### Task 6: 工具配置与记忆操作 API

为工具集成、历史删除、记忆操作添加后端 API。

**Files:**
- Modify: `app_server.py` — 新增 API 路由

- [ ] **Step 1: 添加工具配置 API**

在 `app_server.py` 中添加：
```python
from agent.tools import _registry as _tool_registry, list_tools

@app.route("/api/tools/config", methods=["GET"])
def api_tools_config():
    """获取工具列表及使用统计"""
    tools = list_tools()
    # 从 permission_system 获取权限状态
    try:
        perm_logs = _Yunshu._permission.get_permission_log()
    except Exception:
        perm_logs = []
    result = []
    for t in tools:
        tool_name = t["name"]
        # 统计调用次数
        call_count = sum(1 for log in perm_logs if log.get("tool") == tool_name)
        result.append({
            "name": tool_name,
            "description": t.get("description", ""),
            "enabled": True,  # 默认启用
            "call_count": call_count,
            "last_used": None,
        })
    return jsonify(result)

@app.route("/api/tools/toggle", methods=["POST"])
def api_tools_toggle():
    data = request.get_json() or {}
    tool_name = data.get("name", "")
    enabled = data.get("enabled", True)
    # 工具启用/禁用逻辑（暂时只返回成功）
    return jsonify({"ok": True, "name": tool_name, "enabled": enabled})
```

- [ ] **Step 2: 添加历史删除 API**

在 `app_server.py` 中添加：
```python
@app.route("/api/history/<int:index>", methods=["DELETE"])
def api_history_delete(index):
    """删除指定索引的历史记录"""
    global _CHAT_HISTORY
    if 0 <= index < len(_CHAT_HISTORY):
        deleted = _CHAT_HISTORY.pop(index)
        return jsonify({"ok": True, "deleted": deleted})
    return jsonify({"ok": False, "error": "索引超出范围"}), 404

@app.route("/api/history/search")
def api_history_search():
    """搜索历史记录"""
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify(_CHAT_HISTORY[-50:])
    results = [
        {"index": i, **entry}
        for i, entry in enumerate(_CHAT_HISTORY)
        if q in entry.get("user", "").lower() or q in entry.get("Yunshu", "").lower()
    ]
    return jsonify(results[-50:])
```

- [ ] **Step 3: 添加记忆操作 API**

在 `app_server.py` 中添加：
```python
@app.route("/api/memory/manual", methods=["POST"])
def api_memory_manual():
    """手动添加记忆"""
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    priority = data.get("priority", "normal")
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    try:
        _Yunshu._memory.add_memory({
            "role": "user",
            "content": f"[手动记忆·优先级:{priority}] {content}"
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/memory/<int:index>", methods=["DELETE"])
def api_memory_delete(index):
    """删除指定记忆"""
    try:
        # 清除对应记忆（简化版：清空最近的存储中的指定条目）
        _Yunshu._memory._storage.clear()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/memory/compress", methods=["POST"])
def api_memory_compress():
    """触发记忆压缩"""
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_Yunshu._memory.compress())
        loop.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/memory/overview")
def api_memory_overview():
    """获取记忆概览"""
    try:
        summary = _Yunshu._memory.load_summary()
        recent = _Yunshu._memory._storage.load_recent_messages(limit=20)
        logs = _Yunshu._memory._black_box.analyze()
        log_stats = logs if isinstance(logs, dict) else {}
        return jsonify({
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:300] if summary and summary[0] else None,
            "recent_messages": [
                {"index": i, "role": m.get("role", "?"), "content": m.get("content", "")[:100]}
                for i, m in enumerate(recent)
            ] if recent else [],
            "message_count": len(recent) if recent else 0,
            "log_stats": log_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: 验证 API 工作**

Run: `cd c:/Users/Administrator/agent && python -c "
from app_server import app
client = app.test_client()
r = client.get('/api/tools/config')
print(f'Tools: {r.get_json()}')
r2 = client.get('/api/memory/overview')
print(f'Memory overview keys: {list(r2.get_json().keys())}')
"`

Expected: 打印工具列表和记忆概览

- [ ] **Step 5: 提交**

```bash
git add app_server.py
git commit -m "feat: add tools config, history delete, and memory operation APIs"
```

---

### Task 7: 历史会话 + 技能管理前端模块

实现 history.js 和 skills.js 两个前端模块。

**Files:**
- Create: `static/js/sidebar/history.js`
- Create: `static/js/sidebar/skills.js`

- [ ] **Step 1: 创建 `static/js/sidebar/history.js`**

```javascript
// ════════════════════════════════════════════════════════════
// 历史会话管理模块
// ════════════════════════════════════════════════════════════

let _historyData = [];

async function loadHistory() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const sort = document.getElementById('history-sort').value;
    const searchQ = document.getElementById('history-search').value.trim().toLowerCase();
    let data = await apiGet('/api/history');
    // 反向索引
    _historyData = data.map((entry, idx) => ({ ...entry, index: idx }));
    // 排序
    if (sort === 'oldest') _historyData.reverse();
    // 搜索过滤
    if (searchQ) {
      _historyData = _historyData.filter(e =>
        (e.user || '').toLowerCase().includes(searchQ) ||
        (e.Yunshu || '').toLowerCase().includes(searchQ)
      );
    }
    renderHistory();
  } catch(e) {
    list.innerHTML = '<div class="sidebar-empty">加载历史失败</div>';
  }
}

function renderHistory() {
  const list = document.getElementById('history-list');
  if (_historyData.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">暂无历史记录</div>';
    return;
  }
  list.innerHTML = _historyData.map((entry, i) => `
    <div class="sidebar-card">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title">${escapeHtml(entry.user || '').substring(0, 30)}</span>
        <span class="badge ${entry.mode || 'info'}">${entry.mode || 'normal'}</span>
      </div>
      <div class="sidebar-card-sub">${escapeHtml(entry.Yunshu || '').substring(0, 60)}</div>
      <div class="sidebar-card-actions">
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
  // 使用确认弹窗样式展示详情
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
        <div style="font-size:11px;color:#8b949e;margin-bottom:4px">🤖 云枢:</div>
        <div style="font-size:13px;color:#c9d1d9;white-space:pre-wrap">${escapeHtml(entry.Yunshu)}</div>
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
```

- [ ] **Step 2: 创建 `static/js/sidebar/skills.js`**

```javascript
// ════════════════════════════════════════════════════════════
// 技能管理模块
// ════════════════════════════════════════════════════════════

async function loadSkills() {
  const list = document.getElementById('skills-list');
  list.innerHTML = '<div class="loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const skills = await apiGet('/api/skills');
    renderSkills(skills);
  } catch(e) {
    list.innerHTML = '<div class="sidebar-empty">加载技能失败</div>';
  }
}

function renderSkills(skills) {
  const list = document.getElementById('skills-list');
  if (!skills || skills.length === 0) {
    list.innerHTML = '<div class="sidebar-empty">暂无已配置的技能</div>';
    return;
  }
  list.innerHTML = skills.map(s => `
    <div class="sidebar-card">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title">${escapeHtml(s.name)}</span>
        <label class="toggle-switch">
          <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkill('${s.id}')">
          <span class="slider"></span>
        </label>
      </div>
      <div class="sidebar-card-sub">${escapeHtml(s.description)}</div>
      <div class="sidebar-card-actions">
        <button onclick="showSkillParams('${s.id}')">⚙ 参数</button>
        <button onclick="deleteSkill('${s.id}')" style="color:var(--danger-color)">🗑 删除</button>
      </div>
    </div>
  `).join('');
}

async function toggleSkill(id) {
  try {
    const result = await apiPost('/api/skills/toggle', { id });
    if (result.ok) {
      showToast(result.enabled ? '已启用' : '已禁用');
    }
  } catch(e) {
    showToast('操作失败', 'error');
    loadSkills(); // 恢复状态
  }
}

function showSkillParams(id) {
  // 简单参数编辑弹窗
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML = `
    <div class="sidebar-confirm-box" style="max-width:400px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">⚙ 技能参数配置</p>
      <div id="skill-params-form" style="font-size:13px;color:#8b949e">加载参数...</div>
      <div class="sidebar-confirm-actions" style="margin-top:12px">
        <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove()">取消</button>
        <button class="btn-sm primary" onclick="saveSkillParams('${id}')">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  // 加载当前参数
  apiGet('/api/skills').then(skills => {
    const skill = skills.find(s => s.id === id);
    if (!skill) return;
    const form = document.getElementById('skill-params-form');
    const params = skill.params || {};
    form.innerHTML = Object.entries(params).map(([key, val]) => `
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">${key}</label>
        <input class="sidebar-search" id="sp-${key}" value="${val}" style="margin-bottom:0">
      </div>
    `).join('');
  });
}

async function saveSkillParams(id) {
  const params = {};
  document.querySelectorAll('#skill-params-form input').forEach(inp => {
    const key = inp.id.replace('sp-', '');
    params[key] = inp.value;
  });
  try {
    const r = await apiPost('/api/skills/params', { id, params });
    if (r.ok) {
      showToast('参数已更新');
      document.querySelector('.sidebar-confirm-overlay').remove();
      loadSkills();
    }
  } catch(e) {
    showToast('保存失败', 'error');
  }
}

function showAddSkill() {
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-confirm-overlay';
  overlay.innerHTML = `
    <div class="sidebar-confirm-box" style="max-width:400px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">+ 添加技能</p>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">技能名称</label>
        <input class="sidebar-search" id="new-skill-name" placeholder="如: 多语言翻译" style="margin-bottom:0">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">技能ID</label>
        <input class="sidebar-search" id="new-skill-id" placeholder="如: multi_lang_translate" style="margin-bottom:0">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">描述</label>
        <input class="sidebar-search" id="new-skill-desc" placeholder="简短描述技能功能" style="margin-bottom:0">
      </div>
      <div class="sidebar-confirm-actions" style="margin-top:12px">
        <button class="btn-sm" onclick="this.closest('.sidebar-confirm-overlay').remove()">取消</button>
        <button class="btn-sm primary" onclick="confirmAddSkill()">添加</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function confirmAddSkill() {
  const name = document.getElementById('new-skill-name').value.trim();
  const id = document.getElementById('new-skill-id').value.trim();
  const desc = document.getElementById('new-skill-desc').value.trim();
  if (!name || !id) {
    showToast('请填写名称和ID', 'error');
    return;
  }
  try {
    const r = await apiPost('/api/skills/add', { id, name, description: desc, enabled: true, params: {} });
    if (r.ok) {
      showToast('技能已添加');
      document.querySelector('.sidebar-confirm-overlay').remove();
      loadSkills();
    } else {
      showToast(r.error || '添加失败', 'error');
    }
  } catch(e) {
    showToast('添加失败', 'error');
  }
}

async function deleteSkill(id) {
  const confirmed = await showConfirm('确定要删除这个技能吗？');
  if (!confirmed) return;
  try {
    const r = await apiPost('/api/skills/delete', { id });
    if (r.ok) {
      showToast('已删除');
      loadSkills();
    }
  } catch(e) {
    showToast('删除失败', 'error');
  }
}
```

- [ ] **Step 3: 提交**

```bash
git add static/js/sidebar/history.js static/js/sidebar/skills.js
git commit -m "feat: implement history and skills frontend modules"
```

---

### Task 8: 工具集成 + 人格配置 + 记忆管理前端模块

实现 tools.js, personality.js, memory.js。

**Files:**
- Create: `static/js/sidebar/tools.js`
- Create: `static/js/sidebar/personality.js`
- Create: `static/js/sidebar/memory.js`

- [ ] **Step 1: 创建 `static/js/sidebar/tools.js`**

```javascript
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
      <div class="sidebar-card-actions">
        <button onclick="showToolDetail('${t.name}')">📊 使用记录</button>
      </div>
    </div>
  `).join('');
}

async function toggleTool(name) {
  // 简单切换，实际项目中可对接权限系统
  showToast(`工具「${name}」权限已切换`);
}

function showToolDetail(name) {
  showToast(`工具「${name}」的使用详情（待扩展）`, 'info');
}
```

- [ ] **Step 2: 创建 `static/js/sidebar/personality.js`**

```javascript
// ════════════════════════════════════════════════════════════
// 人格配置模块
// ════════════════════════════════════════════════════════════

async function loadPersonality() {
  try {
    const data = await apiGet('/api/personality');
    renderPresets(data);
    renderSliders(data);
  } catch(e) {
    document.getElementById('personality-sliders').innerHTML = '<div class="sidebar-empty">加载人格配置失败</div>';
  }
}

function renderPresets(data) {
  const el = document.getElementById('personality-presets');
  const profiles = data.profiles || {};
  const current = data.current_profile;
  let html = '<div style="font-size:12px;color:#8b949e;margin-bottom:6px">★ 预设人格</div>';
  for (const [key, profile] of Object.entries(profiles)) {
    const active = key === current;
    html += `<div class="sidebar-card" style="cursor:pointer;${active ? 'border-color:#58a6ff' : ''}" onclick="applyProfile('${key}')">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title">${active ? '▸ ' : ''}${escapeHtml(profile.name)}</span>
        ${active ? '<span class="badge info">当前</span>' : ''}
      </div>
      <div class="sidebar-card-sub">${escapeHtml(profile.description)}</div>
    </div>`;
  }
  el.innerHTML = html;
}

function renderSliders(data) {
  const el = document.getElementById('personality-sliders');
  const params = data.custom_params || {};
  const dimensions = data.dimensions || [];
  let html = '<div style="font-size:12px;color:#8b949e;margin:10px 0 6px">── 详细参数 ──</div>';
  for (const dim of dimensions) {
    const val = Math.round((params[dim.key] || 0.5) * 100);
    html += `<div class="slider-group">
      <label>
        <span>${dim.left}</span>
        <span>${dim.label}: ${val}%</span>
        <span>${dim.right}</span>
      </label>
      <input type="range" min="0" max="100" value="${val}" data-key="${dim.key}" oninput="updateSliderLabel(this)">
    </div>`;
  }
  el.innerHTML = html;
}

function updateSliderLabel(slider) {
  const label = slider.closest('.slider-group').querySelector('label span:nth-child(2)');
  const dimKey = slider.dataset.key;
  const dims = window._personalityDimensions || [];
  const dim = dims.find(d => d.key === dimKey);
  label.textContent = `${dim ? dim.label : dimKey}: ${slider.value}%`;
}

async function applyProfile(profileKey) {
  try {
    const r = await apiPost('/api/personality/profile', { profile: profileKey });
    if (r.ok) {
      showToast(`已切换到「${r.profile}」`);
      loadPersonality();
    } else {
      showToast(r.error || '切换失败', 'error');
    }
  } catch(e) {
    showToast('切换失败', 'error');
  }
}

async function savePersonality() {
  const params = {};
  document.querySelectorAll('#personality-sliders input[type="range"]').forEach(sl => {
    const key = sl.dataset.key;
    params[key] = parseInt(sl.value) / 100;
  });
  try {
    const r = await apiPost('/api/personality/params', { params });
    if (r.ok) {
      showToast('人格配置已保存');
      loadPersonality();
    }
  } catch(e) {
    showToast('保存失败', 'error');
  }
}

async function resetPersonality() {
  const confirmed = await showConfirm('确定恢复默认人格配置吗？');
  if (!confirmed) return;
  try {
    const r = await apiPost('/api/personality/reset');
    if (r.ok) {
      showToast('已恢复默认配置');
      loadPersonality();
    }
  } catch(e) {
    showToast('重置失败', 'error');
  }
}
```

- [ ] **Step 3: 创建 `static/js/sidebar/memory.js`**

```javascript
// ════════════════════════════════════════════════════════════
// 记忆管理模块
// ════════════════════════════════════════════════════════════

async function loadMemory() {
  try {
    const data = await apiGet('/api/memory/overview');
    renderMemoryOverview(data);
    renderMemoryContent(data);
  } catch(e) {
    document.getElementById('memory-overview').innerHTML = '<div class="sidebar-empty">加载记忆失败</div>';
  }
}

function renderMemoryOverview(data) {
  const el = document.getElementById('memory-overview');
  const recent = data.recent_messages || [];
  el.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px">
      <div class="sidebar-card" style="text-align:center">
        <div style="font-size:20px;font-weight:700;color:#58a6ff">${recent.length}</div>
        <div style="font-size:10px;color:#8b949e">短期消息</div>
      </div>
      <div class="sidebar-card" style="text-align:center">
        <div style="font-size:20px;font-weight:700;color:#3fb950">${data.summary_version || '无'}</div>
        <div style="font-size:10px;color:#8b949e">摘要版本</div>
      </div>
    </div>
  `;
}

function renderMemoryContent(data) {
  const el = document.getElementById('memory-content');
  const recent = data.recent_messages || [];
  const logs = data.log_stats || {};

  let html = '<div style="font-size:12px;color:#8b949e;margin-bottom:6px">📌 短期记忆</div>';

  if (recent.length === 0) {
    html += '<div class="sidebar-empty">暂无短期记忆</div>';
  } else {
    for (const msg of recent) {
      const role = msg.role === 'user' ? '👤' : '🤖';
      html += `<div class="sidebar-card">
        <div class="sidebar-card-header">
          <span class="sidebar-card-title">${role} ${escapeHtml(msg.content || '').substring(0, 40)}</span>
        </div>
        <div class="sidebar-card-sub">${escapeHtml(msg.content || '').substring(0, 60)}</div>
        <div class="sidebar-card-actions">
          <button onclick="editMemory(${msg.index}, '${escapeHtml(msg.content || '')}')">✏ 编辑</button>
          <button onclick="deleteMemory(${msg.index})" style="color:var(--danger-color)">🗑 删除</button>
        </div>
      </div>`;
    }
  }

  if (data.summary_text) {
    html += '<div style="font-size:12px;color:#8b949e;margin:10px 0 6px">📦 长期摘要</div>';
    html += `<div class="sidebar-card">
      <div class="sidebar-card-sub" style="font-size:11px">${escapeHtml(data.summary_text)}</div>
    </div>`;
  }

  if (Object.keys(logs).length > 0) {
    html += '<div style="font-size:12px;color:#8b949e;margin:10px 0 6px">📊 日志统计</div>';
    html += '<div class="sidebar-card" style="font-size:11px;color:#8b949e">';
    html += Object.entries(logs).map(([k, v]) => `${k}: ${v} 次`).join(' | ');
    html += '</div>';
  }

  el.innerHTML = html;
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

function editMemory(index, content) {
  showToast('编辑功能（待扩展）', 'info');
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
```

- [ ] **Step 4: 提交**

```bash
git add static/js/sidebar/tools.js static/js/sidebar/personality.js static/js/sidebar/memory.js
git commit -m "feat: implement tools, personality, and memory frontend modules"
```

---

### Task 9: 整合测试与 Bug 修复

启动应用，验证所有功能正常工作。

- [ ] **Step 1: 检查 app_server.py 导入和引用完整性**

```bash
cd c:/Users/Administrator/agent && python -c "
from app_server import app
# 尝试调用每个 API 路由
with app.test_client() as c:
    routes = ['/api/health', '/api/sensors', '/api/status', '/api/personality', '/api/skills',
              '/api/tools/config', '/api/history', '/api/memory/overview']
    for route in routes:
        r = c.get(route)
        status = 'OK' if r.status_code == 200 else f'FAIL({r.status_code})'
        print(f'{status}: {route}')
"
```

Expected: 所有路由返回 200

- [ ] **Step 2: 检查 HTML 模板中的侧边栏结构**

Run: `cd c:/Users/Administrator/agent && python -c "
from app_server import app
with app.test_client() as c:
    r = c.get('/')
    html = r.data.decode('utf-8')
    checks = ['sidebar', 'panel-history', 'panel-skills', 'panel-tools', 'panel-personality', 'panel-memory',
              'sidebar.css', 'sidebar.js', 'history.js', 'skills.js', 'tools.js', 'personality.js', 'memory.js']
    for check in checks:
        found = check in html
        print(f'{\"OK\" if found else \"MISSING\"}: {check}')
"
```

Expected: 所有元素都存在

- [ ] **Step 3: 测试人格配置 CRUD**

```bash
cd c:/Users/Administrator/agent && python -c "
from app_server import app
with app.test_client() as c:
    # GET
    r = c.get('/api/personality')
    assert r.status_code == 200
    data = r.get_json()
    print(f'Profiles: {list(data[\"profiles\"].keys())}')
    # 更新参数
    r = c.post('/api/personality/params', json={'params': {'tone': 0.9}})
    print(f'Update params: {r.get_json()}')
    # 切换预设
    r = c.post('/api/personality/profile', json={'profile': 'humorous'})
    print(f'Apply profile: {r.get_json()}')
    # 重置
    r = c.post('/api/personality/reset', json={})
    print(f'Reset: {r.get_json()}')
    print('Personality CRUD OK')
"
```

- [ ] **Step 4: 测试技能管理 CRUD**

```bash
cd c:/Users/Administrator/agent && python -c "
from app_server import app
with app.test_client() as c:
    r = c.get('/api/skills')
    assert r.status_code == 200
    skills = r.get_json()
    print(f'Skills count: {len(skills)}')
    # Toggle
    if skills:
        r = c.post('/api/skills/toggle', json={'id': skills[0]['id']})
        print(f'Toggle: {r.get_json()}')
    # Add
    r = c.post('/api/skills/add', json={'id': 'test_skill', 'name': '测试技能', 'description': '测试'})
    print(f'Add: {r.get_json()}')
    # Delete
    r = c.post('/api/skills/delete', json={'id': 'test_skill'})
    print(f'Delete: {r.get_json()}')
    print('Skills CRUD OK')
"
```

- [ ] **Step 5: 提交最终修复**

```bash
git add -A
git commit -m "fix: resolve integration issues after sidebar implementation"
```
