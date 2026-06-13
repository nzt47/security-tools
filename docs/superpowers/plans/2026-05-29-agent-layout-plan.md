# 云枢界面布局重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将云枢 Web 界面从"侧边栏+堆叠内容"重构为"图标栏+主内容+状态面板"的三列仪表盘布局，保留所有现有功能。

**Architecture:** CSS Grid 三列布局（48px 图标栏 + 1fr 主内容 + 170px 状态面板），全景视图时状态面板隐藏。管理模块从固定侧边栏改为图标栏浮层交互。新增 layout.css 和 responsive.css，修改 sidebar.css、panorama.css、index.html、sidebar.js。

**Tech Stack:** Vanilla HTML/CSS/JS, Flask 模板 (Jinja2), CSS Grid, 无额外依赖

---

## 文件结构

```
New files:
  static/css/layout.css        — 全局 Grid 布局 + 图标栏 + 状态面板基础样式
  static/css/responsive.css    — 响应式 media queries

Modified files:
  static/css/sidebar.css       — 图标栏交互样式 + 浮层面板样式
  static/css/panorama.css      — 移除未使用的重复样式，微调
  templates/index.html         — DOM 结构重构 + 内联样式精简
  static/js/sidebar/sidebar.js — 浮层开关 + 状态面板刷新逻辑
  static/js/panorama.js        — 适配新 DOM 结构

Untouched:
  static/js/sidebar/history.js, skills.js, tools.js, personality.js, memory.js
  app_server.py, config.py, main.py, all data files
```

---

### Task 1: 创建 layout.css — 全局 Grid 布局

**Files:**
- Create: `static/css/layout.css`

- [ ] **Step 1: 编写 layout.css**

```css
/* ════════════════════════════════════════
   云枢 · 全局布局 — CSS Grid 三列结构
   ════════════════════════════════════════ */

/* 根变量 */
:root {
  --icon-bar-width: 48px;
  --status-panel-width: 170px;
  --topbar-height: 42px;
  --tabbar-height: 34px;
}

/* 全局 Grid 容器 */
#app {
  display: grid;
  grid-template-columns: var(--icon-bar-width) 1fr var(--status-panel-width);
  grid-template-rows: auto auto 1fr;
  height: 100vh;
  overflow: hidden;
}

/* 无状态面板时（全景视图） */
#app.no-right-panel {
  grid-template-columns: var(--icon-bar-width) 1fr;
}

/* 顶栏横跨全部列 */
#topbar {
  grid-column: 1 / -1;
  grid-row: 1;
}

/* 标签栏横跨全部列 */
#tabs {
  grid-column: 1 / -1;
  grid-row: 2;
}

/* 图标栏 */
#icon-bar {
  grid-column: 1;
  grid-row: 3;
  background: #161b22;
  border-right: 1px solid #30363d;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 10px 0;
  gap: 12px;
  overflow: hidden;
  z-index: 20;
}

#icon-bar .icon-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  color: #8b949e;
  font-size: 18px;
  cursor: pointer;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  position: relative;
}

#icon-bar .icon-btn:hover,
#icon-bar .icon-btn.active {
  background: #21262d;
  color: #58a6ff;
}

/* tooltip */
#icon-bar .icon-btn::after {
  content: attr(data-tooltip);
  position: absolute;
  left: 44px;
  top: 50%;
  transform: translateY(-50%);
  background: #21262d;
  color: #c9d1d9;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 11px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 30;
  border: 1px solid #30363d;
}

#icon-bar .icon-btn:hover::after {
  opacity: 1;
}

#icon-bar .icon-spacer {
  flex: 1;
}

/* 主内容区 */
#main {
  grid-column: 2;
  grid-row: 3;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

/* 右侧状态面板 */
#status-panel {
  grid-column: 3;
  grid-row: 3;
  background: #161b22;
  border-left: 1px solid #30363d;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  font-size: 11px;
}

#status-panel.hidden {
  display: none;
}

#status-panel .sp-section-title {
  font-size: 10px;
  font-weight: 600;
  color: #8b949e;
  margin-bottom: 2px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

#status-panel .sp-metric {
  background: #0d1117;
  border-radius: 4px;
  padding: 5px 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

#status-panel .sp-metric .sp-label {
  color: #8b949e;
  font-size: 10px;
}

#status-panel .sp-metric .sp-value {
  font-weight: 700;
  font-size: 11px;
}

#status-panel .sp-metric .sp-value.normal { color: #3fb950; }
#status-panel .sp-metric .sp-value.warning { color: #d29922; }
#status-panel .sp-metric .sp-value.critical { color: #f85149; }

#status-panel .sp-events {
  flex: 1;
  overflow-y: auto;
  font-size: 9px;
  color: #8b949e;
  line-height: 1.6;
  min-height: 0;
}

#status-panel .sp-events .sp-event {
  padding: 2px 0;
  border-bottom: 1px solid #21262d40;
}

/* 标签栏内指标 */
#tab-metrics {
  margin-left: auto;
  display: flex;
  gap: 10px;
  font-size: 11px;
  color: #8b949e;
  align-items: center;
}

#tab-metrics .tm-sep {
  color: #30363d;
}

#tab-metrics .tm-value {
  font-weight: 600;
}

#tab-metrics .tm-value.normal { color: #3fb950; }
#tab-metrics .tm-value.warning { color: #d29922; }
#tab-metrics .tm-value.critical { color: #f85149; }
```

- [ ] **Step 2: 在 index.html 中引入 layout.css**

在 `<link rel="stylesheet" href="{{ url_for('static', filename='css/sidebar.css') }}">` 之前添加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
```

- [ ] **Step 3: 提交**

```bash
git add static/css/layout.css templates/index.html
git commit -m "feat: add layout.css with CSS Grid three-column structure"
```

---

### Task 2: 修改 sidebar.css — 图标栏 + 浮层面板 + 扩展侧边栏

**Files:**
- Modify: `static/css/sidebar.css`

- [ ] **Step 1: 重构 sidebar.css**

当前 sidebar.css 定义了 `#app.with-sidebar` 的 grid 布局，现在 layout.css 已经接管全局布局。sidebar.css 需要改为：
- 完整侧边栏（展开态）的样式
- 浮层面板的样式
- 保留面板内容区样式（`.sidebar-panel`、卡片、开关、搜索框等）不变

用以下内容**完全替换** sidebar.css：

```css
/* ════════════════════════════════════════
   云枢 · Agent 管理 — 图标栏 + 浮层面板 + 扩展侧边栏
   ════════════════════════════════════════ */

:root {
  --sidebar-width: 280px;
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

/* ── 完整侧边栏（展开态，替代图标栏） ── */
#sidebar {
  grid-column: 1;
  grid-row: 3;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: none;          /* 默认隐藏 */
  flex-direction: column;
  overflow: hidden;
  z-index: 25;
  width: var(--sidebar-width);
}

#sidebar.expanded {
  display: flex;
}

/* 展开时隐藏图标栏 */
#app.sidebar-expanded #icon-bar {
  display: none;
}

#app.sidebar-expanded {
  grid-template-columns: var(--sidebar-width) 1fr var(--status-panel-width);
}

#app.sidebar-expanded.no-right-panel {
  grid-template-columns: var(--sidebar-width) 1fr;
}

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

.sidebar-tab .tab-icon { font-size: 16px; width: 24px; text-align: center; flex-shrink: 0; }

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

/* ── 浮层面板 ── */
.floating-panel-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: none;
}

.floating-panel-overlay.show {
  display: block;
}

.floating-panel {
  position: fixed;
  left: 56px;                  /* 图标栏宽度 + 8px 间隙 */
  top: 80px;                   /* 顶栏 + 标签栏下方 */
  width: 280px;
  max-height: calc(100vh - 100px);
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  z-index: 51;
  display: none;
  flex-direction: column;
  overflow: hidden;
}

.floating-panel.show {
  display: flex;
}

.floating-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid #21262d;
  flex-shrink: 0;
}

.floating-panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #c9d1d9;
}

.floating-panel-close {
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: #8b949e;
  font-size: 18px;
  cursor: pointer;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.floating-panel-close:hover {
  background: #21262d;
  color: #c9d1d9;
}

.floating-panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

/* ── 面板通用组件（与原来相同） ── */
.sidebar-panel h3, .floating-panel-body h3 {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1d9;
  margin: 0 0 12px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.sidebar-panel .panel-actions, .floating-panel-body .panel-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}

.btn-sm {
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid var(--sidebar-border);
  background: #0d1117;
  color: #c9d1d9;
  font-size: 12px;
  cursor: pointer;
}

.btn-sm:hover { background: var(--sidebar-hover); }

.btn-sm.primary {
  background: #238636;
  border-color: #238636;
  color: #fff;
}

.btn-sm.primary:hover { background: #2ea043; }

.btn-sm.danger {
  color: var(--danger-color);
  border-color: var(--danger-color);
}

.btn-sm.danger:hover {
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

.sidebar-card-title { font-size: 12px; font-weight: 600; color: #c9d1d9; }
.sidebar-card-sub { font-size: 11px; color: var(--sidebar-text); line-height: 1.4; }

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

.sidebar-card-actions button:hover { background: var(--sidebar-hover); color: #c9d1d9; }

/* ── 开关 ── */
.toggle-switch {
  position: relative;
  width: 32px;
  height: 18px;
  flex-shrink: 0;
}

.toggle-switch input { opacity: 0; width: 0; height: 0; }

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

.toggle-switch input:checked + .slider { background: #238636; }
.toggle-switch input:checked + .slider::before { transform: translateX(14px); background: #fff; }

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

.sidebar-search:focus { border-color: var(--sidebar-accent); }
.sidebar-search::placeholder { color: #484f58; }

/* ── 滑动条 ── */
.slider-group { margin-bottom: 10px; }

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

.sidebar-toast.success { background: #3fb950; color: #fff; }
.sidebar-toast.error { background: var(--danger-color); color: #fff; }

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

.sidebar-confirm-box p { font-size: 14px; color: #c9d1d9; margin: 0 0 16px; line-height: 1.5; }

.sidebar-confirm-actions { display: flex; justify-content: flex-end; gap: 8px; }

/* ── 空状态 ── */
.sidebar-empty { text-align: center; padding: 30px 10px; color: #484f58; font-size: 13px; }

/* ── 滚动条 ── */
.sidebar-panel::-webkit-scrollbar, .floating-panel-body::-webkit-scrollbar { width: 4px; }
.sidebar-panel::-webkit-scrollbar-track, .floating-panel-body::-webkit-scrollbar-track { background: transparent; }
.sidebar-panel::-webkit-scrollbar-thumb, .floating-panel-body::-webkit-scrollbar-thumb { background: var(--sidebar-border); border-radius: 2px; }

/* ── 拖拽调整宽度 ── */
.sidebar-resizer {
  display: none;  /* 不再需要拖拽，由图标栏+浮层替代 */
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  #sidebar.expanded {
    position: fixed;
    inset: 0;
    z-index: 100;
    width: 100%;
  }
}
```

- [ ] **Step 2: 提交**

```bash
git add static/css/sidebar.css
git commit -m "refactor: rewrite sidebar.css for icon bar + floating panel + expanded sidebar"
```

---

### Task 3: 微调 panorama.css

**Files:**
- Modify: `static/css/panorama.css`

当前 panorama.css 已包含 `.pano-layout`、`.pano-nav`、`.pano-content` 等样式，且与新布局兼容。只需要：

- [ ] **Step 1: 删除 panorama.css 中与 layout.css 重复/冲突的样式**

需要删除 panorama.css 中关于 `#app.with-sidebar` 的 grid 覆盖样式（如果有），以及 `.pano-healthbar` 的全宽引用。

检查确认 panorama.css 中没有与 `#app` grid 布局冲突的选择器。当前 panorama.css 只定义了 `.pano-*` 开头的类，不冲突。

- [ ] **Step 2: 添加全景视图专属的导航激活样式补充**

在 panorama.css 末尾追加：

```css
/* ── 仪表盘总览网格 ── */
.pano-dashboard-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 10px;
}

.pano-dashboard-bottom {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

/* ── 单层详情视图 ── */
.pano-detail-view {
  display: none;
}

.pano-detail-view.active {
  display: block;
}

/* ── 全景内容区滚动 ── */
.pano-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  min-height: 0;
}
```

- [ ] **Step 3: 提交**

```bash
git add static/css/panorama.css
git commit -m "feat: add dashboard grid and detail view styles to panorama.css"
```

---

### Task 4: 创建 responsive.css

**Files:**
- Create: `static/css/responsive.css`

- [ ] **Step 1: 编写响应式断点样式**

```css
/* ════════════════════════════════════════
   云枢 · 响应式布局
   ════════════════════════════════════════ */

/* 768-900px: 隐藏右侧状态面板 */
@media (max-width: 900px) {
  #app {
    grid-template-columns: var(--icon-bar-width) 1fr;
  }
  #status-panel {
    display: none;
  }
  #status-panel.show-mobile {
    display: flex;
    position: fixed;
    right: 0;
    top: 0;
    bottom: 0;
    width: 200px;
    z-index: 60;
    box-shadow: -4px 0 16px rgba(0,0,0,0.3);
  }
}

/* 600-768px: 图标栏隐藏，汉堡菜单移到顶栏 */
@media (max-width: 768px) {
  #app {
    grid-template-columns: 1fr;
  }
  #icon-bar {
    display: none;
  }
  #topbar .mobile-menu-btn {
    display: inline-flex;
  }
  .floating-panel {
    left: 8px;
    right: 8px;
    width: auto;
  }
}

/* < 600px: 全景导航折叠为顶部横排 */
@media (max-width: 600px) {
  .pano-layout {
    flex-direction: column;
  }
  .pano-nav {
    flex-direction: row;
    width: 100%;
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
  .pano-content {
    padding: 8px;
  }
  .pano-dashboard-grid,
  .pano-dashboard-bottom {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: 在 index.html 中引入 responsive.css**

在 layout.css 引入之后添加：

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/responsive.css') }}">
```

- [ ] **Step 3: 提交**

```bash
git add static/css/responsive.css templates/index.html
git commit -m "feat: add responsive.css with breakpoints at 900/768/600px"
```

---

### Task 5: 重构 index.html — DOM 结构

**Files:**
- Modify: `templates/index.html`

这是最大的一步。需要重构 DOM 结构：删除旧侧边栏和指标栏，添加图标栏、浮层面板、状态面板，重构全景视图。

- [ ] **Step 1: 重写 `<body>` 内的 HTML 结构**

用以下内容替换 index.html 中 `<body>` 开始到 `</body>` 结束之间的所有内容（保留内联 `<style>` 不变，JS 脚本标签在 Task 6/7 中修改）：

```html
<body>
<div id="app" class="no-right-panel">
  <!-- 顶栏 -->
  <div id="topbar">
    <div style="display:flex;align-items:center;gap:10px">
      <button class="mobile-menu-btn" onclick="toggleSidebar()" title="菜单"
              style="display:none;width:28px;height:28px;border:none;background:transparent;color:#8b949e;font-size:18px;cursor:pointer;border-radius:4px">☰</button>
      <h1 style="font-size:18px;color:#58a6ff;margin:0">云枢 · 数字生命体</h1>
      <span class="sub" style="font-size:12px;color:#8b949e" id="status-sub">感知-认知-行动闭环</span>
    </div>
    <div class="actions" style="display:flex;gap:8px">
      <button onclick="showSettings()" style="padding:5px 14px;border-radius:12px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px;cursor:pointer">⚙ 设置</button>
      <button onclick="refreshLeft()" style="padding:5px 14px;border-radius:12px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px;cursor:pointer">⟳ 刷新</button>
      <button onclick="clearChat()" style="padding:5px 14px;border-radius:12px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:12px;cursor:pointer">清空对话</button>
    </div>
  </div>

  <!-- 标签栏（含健康指标） -->
  <div id="tabs" style="display:flex;align-items:center;background:#0d1117;border-bottom:1px solid #30363d;padding:0 12px">
    <div style="display:flex">
      <span class="tab active" data-tab="chat" onclick="switchTab('chat')" style="padding:8px 18px;font-size:13px;color:#58a6ff;cursor:pointer;border-bottom:2px solid #58a6ff;user-select:none">💬 对话</span>
      <span class="tab" data-tab="panorama" onclick="switchTab('panorama')" style="padding:8px 18px;font-size:13px;color:#8b949e;cursor:pointer;border-bottom:2px solid transparent;user-select:none">🗺 全景</span>
    </div>
    <div id="tab-metrics">
      <!-- JS 动态填充 -->
    </div>
  </div>

  <!-- 图标栏 -->
  <div id="icon-bar">
    <button class="icon-btn" data-panel="history" data-tooltip="历史会话" onclick="toggleFloatingPanel('history')">🕐</button>
    <button class="icon-btn" data-panel="skills" data-tooltip="技能管理" onclick="toggleFloatingPanel('skills')">🔧</button>
    <button class="icon-btn" data-panel="tools" data-tooltip="工具集成" onclick="toggleFloatingPanel('tools')">🛠</button>
    <button class="icon-btn" data-panel="personality" data-tooltip="人格配置" onclick="toggleFloatingPanel('personality')">🎭</button>
    <button class="icon-btn" data-panel="memory" data-tooltip="记忆管理" onclick="toggleFloatingPanel('memory')">🧠</button>
    <div class="icon-spacer"></div>
    <button class="icon-btn" data-tooltip="展开侧边栏" onclick="toggleSidebar()" style="font-size:16px">☰</button>
  </div>

  <!-- 完整侧边栏（展开态，默认隐藏） -->
  <div id="sidebar">
    <div class="sidebar-header">
      <button class="sidebar-toggle" onclick="toggleSidebar()" title="折叠侧边栏">☰</button>
      <span class="sidebar-title">Agent 管理</span>
    </div>
    <div class="sidebar-tabs">
      <div class="sidebar-tab active" data-panel="history" onclick="switchSidebarTab('history')">
        <span class="tab-icon">🕐</span><span class="tab-label">历史会话</span>
      </div>
      <div class="sidebar-tab" data-panel="skills" onclick="switchSidebarTab('skills')">
        <span class="tab-icon">🔧</span><span class="tab-label">技能管理</span>
      </div>
      <div class="sidebar-tab" data-panel="tools" onclick="switchSidebarTab('tools')">
        <span class="tab-icon">🛠</span><span class="tab-label">工具集成</span>
      </div>
      <div class="sidebar-tab" data-panel="personality" onclick="switchSidebarTab('personality')">
        <span class="tab-icon">🎭</span><span class="tab-label">人格配置</span>
      </div>
      <div class="sidebar-tab" data-panel="memory" onclick="switchSidebarTab('memory')">
        <span class="tab-icon">🧠</span><span class="tab-label">记忆管理</span>
      </div>
    </div>
    <div class="sidebar-panels" id="sidebar-panels-container">
      <!-- 面板内容由 JS 动态生成，与浮层共享同一套创建逻辑 -->
    </div>
  </div>

  <!-- 浮层面板容器 -->
  <div class="floating-panel-overlay" id="floating-overlay" onclick="closeFloatingPanel()"></div>
  <div class="floating-panel" id="floating-panel">
    <div class="floating-panel-header">
      <span class="floating-panel-title" id="floating-panel-title">面板</span>
      <button class="floating-panel-close" onclick="closeFloatingPanel()">&times;</button>
    </div>
    <div class="floating-panel-body" id="floating-panel-body">
      <!-- 面板内容由 JS 动态填充 -->
    </div>
  </div>

  <!-- 主内容区 -->
  <div id="main">
    <!-- 对话视图 -->
    <div id="chat-view" class="tab-view active" style="flex-direction:column;flex:1;display:flex;min-height:0">
      <div id="chat-panel" style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div id="chat-messages" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px"></div>
        <div id="chat-input-area" style="display:flex;gap:8px;padding:12px 16px;background:#161b22;border-top:1px solid #30363d;flex-shrink:0">
          <textarea id="chat-input" rows="1" placeholder="和云枢说话..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"
                    style="flex:1;padding:10px 14px;border-radius:20px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:14px;outline:none;resize:none;font-family:inherit;max-height:80px"></textarea>
          <button id="send-btn" onclick="sendMessage()" style="padding:8px 20px;border-radius:20px;border:none;background:#238636;color:#fff;font-size:14px;cursor:pointer;white-space:nowrap">发送</button>
        </div>
      </div>
    </div>

    <!-- 全景视图 -->
    <div id="panorama-view" class="tab-view" style="flex:1;min-height:0">
      <div class="pano-layout" style="display:flex;height:100%">
        <div class="pano-nav">
          <div class="pano-nav-item active" data-pano-section="dashboard" onclick="switchPanoSection('dashboard')">
            <span class="nav-dot" style="background:#58a6ff"></span> 仪表盘总览
          </div>
          <div class="pano-nav-item" data-pano-section="phase1" onclick="switchPanoSection('phase1')">
            <span class="nav-dot" style="background:#58a6ff"></span> 感知层
          </div>
          <div class="pano-nav-item" data-pano-section="phase2" onclick="switchPanoSection('phase2')">
            <span class="nav-dot" style="background:#bc8cff"></span> 认知层
          </div>
          <div class="pano-nav-item" data-pano-section="phase3" onclick="switchPanoSection('phase3')">
            <span class="nav-dot" style="background:#3fb950"></span> 记忆层
          </div>
          <div class="pano-nav-item" data-pano-section="phase4" onclick="switchPanoSection('phase4')">
            <span class="nav-dot" style="background:#d29922"></span> 行动层
          </div>
          <div class="pano-nav-indicator"></div>
          <div class="pano-nav-item" data-pano-section="trace" onclick="switchPanoSection('trace')">
            <span class="nav-dot" style="background:#8b949e"></span> 交互追踪
          </div>
          <div class="pano-nav-item" data-pano-section="system" onclick="switchPanoSection('system')">
            <span class="nav-dot" style="background:#8b949e"></span> 系统总览
          </div>
        </div>
        <div class="pano-content" id="pano-content">
          <!-- 仪表盘总览（默认） -->
          <div id="pano-section-dashboard">
            <!-- 流水线 -->
            <div class="pano-pipeline">
              <div class="pipe-node phase-1" onclick="switchPanoSection('phase1')">
                <div class="pn-icon">👁</div><div class="pn-label">感知层</div><div class="pn-sub">BodySensor</div><div class="pn-status">●</div>
              </div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node phase-2" onclick="switchPanoSection('phase2')">
                <div class="pn-icon">🧠</div><div class="pn-label">认知层</div><div class="pn-sub">PromptInjector</div><div class="pn-status">●</div>
              </div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node phase-3" onclick="switchPanoSection('phase3')">
                <div class="pn-icon">💾</div><div class="pn-label">记忆层</div><div class="pn-sub">MemoryManager</div><div class="pn-status">●</div>
              </div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node phase-4" onclick="switchPanoSection('phase4')">
                <div class="pn-icon">🤖</div><div class="pn-label">行动层</div><div class="pn-sub">DigitalLife</div><div class="pn-status">●</div>
              </div>
              <div class="pipe-arrow">→</div>
              <div class="pipe-node pipe-output">
                <div class="pn-icon">💬</div><div class="pn-label">响应</div><div class="pn-sub">输出</div>
              </div>
            </div>

            <!-- 4 阶段指标卡片 2x2 -->
            <div class="pano-dashboard-grid">
              <div class="pano-card phase-1">
                <div class="pano-card-header" onclick="switchPanoSection('phase1')">
                  <span class="phase-badge">阶段一</span>
                  <span class="pano-card-title">感知底座</span>
                  <span class="expand-btn">详情 ▸</span>
                </div>
                <div class="pano-card-body">
                  <div class="card-metrics">
                    <div class="card-metric"><div class="cm-val" id="pano-cpu-val">-</div><div class="cm-label">CPU</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-mem-val">-</div><div class="cm-label">内存</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-sensor-val">-</div><div class="cm-label">传感器</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-battery-val">-</div><div class="cm-label">电池</div></div>
                  </div>
                </div>
              </div>
              <div class="pano-card phase-2">
                <div class="pano-card-header" onclick="switchPanoSection('phase2')">
                  <span class="phase-badge">阶段二</span>
                  <span class="pano-card-title">元认知引擎</span>
                  <span class="expand-btn">详情 ▸</span>
                </div>
                <div class="pano-card-body">
                  <div class="card-metrics">
                    <div class="card-metric"><div class="cm-val" id="pano-cog-status">-</div><div class="cm-label">引擎状态</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-reject-status">-</div><div class="cm-label">可执行</div></div>
                  </div>
                </div>
              </div>
              <div class="pano-card phase-3">
                <div class="pano-card-header" onclick="switchPanoSection('phase3')">
                  <span class="phase-badge">阶段三</span>
                  <span class="pano-card-title">记忆管理</span>
                  <span class="expand-btn">详情 ▸</span>
                </div>
                <div class="pano-card-body">
                  <div class="card-metrics">
                    <div class="card-metric"><div class="cm-val" id="pano-msg-count">-</div><div class="cm-label">消息数</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-log-count">-</div><div class="cm-label">日志数</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-summary-ver">-</div><div class="cm-label">摘要版本</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-compress-info">-</div><div class="cm-label">压缩阈值</div></div>
                  </div>
                </div>
              </div>
              <div class="pano-card phase-4">
                <div class="pano-card-header" onclick="switchPanoSection('phase4')">
                  <span class="phase-badge">阶段四</span>
                  <span class="pano-card-title">反身智能</span>
                  <span class="expand-btn">详情 ▸</span>
                </div>
                <div class="pano-card-body">
                  <div class="card-metrics">
                    <div class="card-metric"><div class="cm-val" id="pano-mode">-</div><div class="cm-label">行为模式</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-tools">-</div><div class="cm-label">注册工具</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-reflections">-</div><div class="cm-label">反思记录</div></div>
                    <div class="card-metric"><div class="cm-val" id="pano-llm">-</div><div class="cm-label">LLM状态</div></div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 事件流 + 系统总览 -->
            <div class="pano-dashboard-bottom">
              <div class="pano-events">
                <div class="pano-events-header">📋 实时事件流</div>
                <div class="pano-events-body" id="pano-events-body">
                  <div class="pano-empty">暂无事件</div>
                </div>
              </div>
              <div class="pano-sysbar" id="pano-sysbar">
                <div class="sys-item">会话: <span id="pano-session">-</span></div>
                <div class="sys-item">交互: <span id="pano-interactions">-</span></div>
                <div class="sys-item">运行: <span id="pano-uptime">-</span></div>
                <div class="sys-item">传感器: <span id="pano-sensor-total">-</span></div>
                <div class="sys-item">模式: <span id="pano-mode-badge-2" class="status-badge normal">正常</span></div>
              </div>
            </div>
          </div>

          <!-- 各层详情视图（默认隐藏，点击导航切换） -->
          <div id="pano-section-phase1" class="pano-detail-view">
            <!-- 感知层完整详情：健康指标 + 传感器分类 + 标签 + 清单 -->
            <div class="pano-card phase-1">
              <div class="pano-card-header"><span class="phase-badge">阶段一</span><span class="pano-card-title">感知底座 · BodySensor</span></div>
              <div class="pano-card-body">
                <div class="pano-metrics" id="pano-health"></div>
                <div class="pano-stat">传感器: <span id="pano-sensor-count">-</span></div>
                <div class="pano-divider"></div>
                <div class="detail-section-title">📂 传感器四大分类</div>
                <div class="pano-cat-grid" id="pano-sensor-categories"></div>
                <div class="detail-section-title">🏷 八维标签体系</div>
                <div class="pano-tag-grid" id="pano-tag-grid"></div>
                <div class="detail-section-title">📡 全部传感器清单</div>
                <div class="pano-sensor-list" id="pano-sensor-list"></div>
              </div>
            </div>
          </div>

          <div id="pano-section-phase2" class="pano-detail-view">
            <div class="pano-card phase-2">
              <div class="pano-card-header"><span class="phase-badge">阶段二</span><span class="pano-card-title">元认知引擎 · PromptInjector</span></div>
              <div class="pano-card-body">
                <div id="pano-cognitive" class="pano-text">加载中...</div>
                <div id="pano-reject-status" style="margin-top:6px"></div>
                <div class="pano-divider"></div>
                <div class="detail-section-title">📜 翻译规则配置</div>
                <div id="pano-translate-rules" class="pano-text"></div>
                <div class="detail-section-title">📝 提示词模板</div>
                <pre id="pano-prompt-preview" class="pano-pre"></pre>
              </div>
            </div>
          </div>

          <div id="pano-section-phase3" class="pano-detail-view">
            <div class="pano-card phase-3">
              <div class="pano-card-header"><span class="phase-badge">阶段三</span><span class="pano-card-title">记忆管理 · MemoryManager</span></div>
              <div class="pano-card-body">
                <div class="pano-stat">摘要版本: <span id="pano-summary-ver-detail">-</span></div>
                <div class="pano-stat">消息记录: <span id="pano-msg-count-detail">-</span></div>
                <div class="pano-stat">日志条目: <span id="pano-log-count-detail">-</span></div>
                <div class="pano-divider"></div>
                <div class="detail-section-title">📄 当前摘要</div>
                <pre id="pano-summary-text" class="pano-pre"></pre>
                <div class="detail-section-title">📊 日志统计</div>
                <div id="pano-log-stats" class="pano-text"></div>
                <div class="detail-section-title">⚙ 压缩策略</div>
                <div id="pano-compress-info-detail" class="pano-text"></div>
              </div>
            </div>
          </div>

          <div id="pano-section-phase4" class="pano-detail-view">
            <div class="pano-card phase-4">
              <div class="pano-card-header"><span class="phase-badge">阶段四</span><span class="pano-card-title">整合与反身智能 · DigitalLife</span></div>
              <div class="pano-card-body">
                <div class="pano-stat">行为模式: <span id="pano-mode-detail">-</span></div>
                <div class="pano-stat">注册工具: <span id="pano-tools-detail">-</span></div>
                <div class="pano-stat">反思记录: <span id="pano-reflections-detail">-</span></div>
                <div class="pano-stat">LLM 状态: <span id="pano-llm-detail">-</span></div>
                <div class="pano-divider"></div>
                <div class="detail-section-title">🔄 六种行为模式</div>
                <div id="pano-mode-list" class="pano-mode-grid"></div>
                <div class="detail-section-title">🔧 注册工具</div>
                <div id="pano-tool-list" class="pano-text"></div>
                <div class="detail-section-title">🛡 权限检查记录</div>
                <div id="pano-perm-info" class="pano-text"></div>
              </div>
            </div>
          </div>

          <div id="pano-section-trace" class="pano-detail-view">
            <div class="pano-card pano-full-width">
              <div class="pano-card-header"><span class="phase-badge">🔍</span><span class="pano-card-title">上次交互 · 数据流追踪</span></div>
              <div class="pano-card-body" id="pano-trace-body">
                <div class="pano-text sub">暂无交互记录</div>
              </div>
            </div>
          </div>

          <div id="pano-section-system" class="pano-detail-view">
            <div class="pano-sysbar" id="pano-sysbar-detail">
              <div class="sys-item">会话: <span id="pano-session-detail">-</span></div>
              <div class="sys-item">交互: <span id="pano-interactions-detail">-</span></div>
              <div class="sys-item">运行: <span id="pano-uptime-detail">-</span></div>
              <div class="sys-item">传感器: <span id="pano-sensor-total-detail">-</span></div>
              <div class="sys-item">模式: <span id="pano-mode-badge-detail" class="status-badge normal">正常</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 右侧状态面板 -->
  <div id="status-panel">
    <div class="sp-section-title">📊 系统状态</div>
    <div class="sp-metric"><span class="sp-label">CPU</span><span class="sp-value" id="sp-cpu">-</span></div>
    <div class="sp-metric"><span class="sp-label">内存</span><span class="sp-value" id="sp-memory">-</span></div>
    <div class="sp-metric"><span class="sp-label">磁盘</span><span class="sp-value" id="sp-disk">-</span></div>
    <div class="sp-metric"><span class="sp-label">电池</span><span class="sp-value" id="sp-battery">-</span></div>
    <div class="sp-metric"><span class="sp-label">网络</span><span class="sp-value" id="sp-network">-</span></div>
    <div class="sp-metric"><span class="sp-label">传感器</span><span class="sp-value" id="sp-sensors">-</span></div>
    <div class="sp-metric"><span class="sp-label">模式</span><span class="sp-value" id="sp-mode">-</span></div>
    <div class="sp-section-title" style="margin-top:8px">📋 事件流</div>
    <div class="sp-events" id="sp-events">
      <div class="sidebar-empty">暂无事件</div>
    </div>
  </div>

</div>

<!-- 设置弹窗（不变） -->
<div id="settings-modal" class="modal-overlay" style="display:none" onclick="if(event.target===this)hideSettings()">
  <div class="modal" style="background:#161b22;border:1px solid #30363d;border-radius:12px;width:420px;max-width:90vw;box-shadow:0 8px 32px #00000080">
    <div class="modal-header" style="display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid #30363d">
      <h3 style="margin:0;font-size:16px;color:#c9d1d9">⚙ 云枢设置</h3>
      <span class="modal-close" onclick="hideSettings()" style="font-size:24px;cursor:pointer;color:#8b949e;line-height:1">&times;</span>
    </div>
    <div class="modal-body" style="padding:20px">
      <div class="form-group" style="margin-bottom:14px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:4px">LLM 提供商</label>
        <select id="cfg-provider" onchange="updateModelHint()" style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:14px;outline:none;box-sizing:border-box">
          <option value="openai">OpenAI</option>
          <option value="deepseek">DeepSeek</option>
          <option value="anthropic">Anthropic</option>
        </select>
      </div>
      <div class="form-group" style="margin-bottom:14px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:4px">API Key</label>
        <input type="password" id="cfg-apikey" placeholder="sk-..." autocomplete="off" style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:14px;outline:none;box-sizing:border-box">
      </div>
      <div class="form-group" style="margin-bottom:14px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:4px">模型名称</label>
        <div class="model-hint" id="cfg-model-hint" style="font-size:11px;color:#8b949e;margin-bottom:4px">提示: gpt-4 / deepseek-chat / claude-sonnet-4-20250514</div>
        <input type="text" id="cfg-model" placeholder="deepseek-chat" style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:14px;outline:none;box-sizing:border-box">
      </div>
      <div id="cfg-status" class="cfg-status" style="padding:8px 12px;border-radius:6px;font-size:13px;display:none"></div>
    </div>
    <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;padding:12px 20px;border-top:1px solid #30363d">
      <button class="btn-secondary" onclick="hideSettings()" style="padding:7px 18px;border-radius:6px;border:1px solid #30363d;background:#0d1117;color:#c9d1d9;font-size:13px;cursor:pointer">取消</button>
      <button class="btn-primary" onclick="saveConfig()" style="padding:7px 18px;border-radius:6px;border:none;background:#238636;color:#fff;font-size:13px;cursor:pointer">保存并连接</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 清理内联 `<style>` 标签**

删除 index.html `<style>` 中以下与旧布局相关的样式段：
- `#app{display:flex;flex-direction:column;height:100vh}` → 由 layout.css 接管
- `#topbar{...}` 样式保留（内联已移到元素 style 属性）
- `#tabs{...}` 和 `.tab{...}` 样式保留（内联已移到元素 style 属性）
- `.tab-view{...}` 样式保留
- `#main{display:flex;flex:1;min-height:0}` → 由 layout.css 接管
- `#chat-metrics-bar{...}` 和 `.quick-grid{...}` 和 `.metric{...}` → 删除
- `#chat-status-bar{...}` 和 `.status-badge{...}` → 样式保留但移到元素上
- `#chat-panel{...}` 样式保留

具体删除以下样式块：
```css
/* 删除这些 */
#app{display:flex;flex-direction:column;height:100vh}
#topbar{...}         /* 改为内联 */
#tabs{...}           /* 改为内联 */
.tab{...}            /* 保留在 style 中 */
.tab:hover{...}
.tab.active{...}
.tab-view{...}       /* 保留 */
.tab-view.active{...}
#main{display:flex;flex:1;min-height:0}
#chat-view{flex-direction:column}
#chat-metrics-bar{...}  /* 删除整个块 */
.quick-grid{...}        /* 删除整个块 */
.metric{...}            /* 删除整个块 */
.metric .value{...}
.metric .label{...}
.metric.normal .value{...}
.metric.warning .value{...}
.metric.critical .value{...}
#chat-status-bar{...}   /* 删除整个块 */
.status-badge{...}      /* 保留在 style 中 */
.status-badge.normal{...}
/* ... 其他 status-badge 变体保留 */
#chat-panel{...}        /* 保留 */
/* panorama 样式全部保留（仍有用） */
```

- [ ] **Step 3: 提交**

```bash
git add templates/index.html
git commit -m "refactor: restructure HTML DOM for three-column layout with icon bar and status panel"
```

---

### Task 6: 修改 sidebar.js — 浮层逻辑 + 面板生成 + 状态面板刷新

**Files:**
- Modify: `static/js/sidebar/sidebar.js`

- [ ] **Step 1: 重写 sidebar.js**

用以下内容完全替换：

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · Agent 管理 — 图标栏 + 浮层面板 + 状态面板
// ════════════════════════════════════════════════════════════

let _activeFloatingPanel = null;
let _sidebarExpanded = false;

// ── 浮层面板开关 ──
function toggleFloatingPanel(panelName) {
  if (_activeFloatingPanel === panelName) {
    closeFloatingPanel();
    return;
  }
  openFloatingPanel(panelName);
}

function openFloatingPanel(panelName) {
  // 关闭之前的
  closeFloatingPanel();

  // 更新图标栏活跃状态
  document.querySelectorAll('#icon-bar .icon-btn').forEach(b => b.classList.remove('active'));
  const iconBtn = document.querySelector(`#icon-bar .icon-btn[data-panel="${panelName}"]`);
  if (iconBtn) iconBtn.classList.add('active');

  // 设置标题
  const titles = {
    history: '🕐 历史会话',
    skills: '🔧 技能管理',
    tools: '🛠 工具集成',
    personality: '🎭 人格配置',
    memory: '🧠 记忆管理'
  };
  document.getElementById('floating-panel-title').textContent = titles[panelName] || panelName;

  // 填充面板内容
  renderPanelContent(panelName, 'floating-panel-body');

  // 显示浮层
  document.getElementById('floating-overlay').classList.add('show');
  document.getElementById('floating-panel').classList.add('show');
  _activeFloatingPanel = panelName;

  // 加载数据
  loadSidebarModule(panelName);
}

function closeFloatingPanel() {
  document.querySelectorAll('#icon-bar .icon-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('floating-overlay').classList.remove('show');
  document.getElementById('floating-panel').classList.remove('show');
  _activeFloatingPanel = null;
}

// ── 面板内容渲染 ──
function renderPanelContent(panelName, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  let html = '';
  switch (panelName) {
    case 'history':
      html = `
        <h3>🕐 历史会话</h3>
        <input class="sidebar-search" id="history-search" placeholder="搜索历史记录..." oninput="filterHistory()">
        <div class="panel-actions">
          <select id="history-sort" class="btn-sm" onchange="loadHistory()">
            <option value="newest">最新优先</option>
            <option value="oldest">最早优先</option>
          </select>
        </div>
        <div id="history-list"></div>`;
      break;
    case 'skills':
      html = `
        <h3>🔧 技能管理</h3>
        <div class="panel-actions">
          <button class="btn-sm primary" onclick="showAddSkill()">+ 添加技能</button>
        </div>
        <div id="skills-list"></div>`;
      break;
    case 'tools':
      html = `
        <h3>🛠 工具集成</h3>
        <div id="tools-count" style="font-size:12px;color:#8b949e;margin-bottom:8px"></div>
        <div id="tools-list"></div>`;
      break;
    case 'personality':
      html = `
        <h3>🎭 人格配置</h3>
        <div id="personality-presets"></div>
        <div id="personality-sliders"></div>
        <div class="panel-actions" style="margin-top:12px">
          <button class="btn-sm primary" onclick="savePersonality()">💾 保存配置</button>
          <button class="btn-sm" onclick="resetPersonality()">↺ 恢复默认</button>
        </div>`;
      break;
    case 'memory':
      html = `
        <h3>🧠 记忆管理</h3>
        <div id="memory-overview"></div>
        <div id="memory-content"></div>
        <div class="panel-actions" style="margin-top:8px">
          <button class="btn-sm primary" onclick="showAddMemory()">+ 手动添加</button>
          <button class="btn-sm" onclick="triggerCompression()">⚡ 触发压缩</button>
        </div>`;
      break;
  }
  container.innerHTML = html;
}

// ── 完整侧边栏展开/折叠 ──
function toggleSidebar() {
  _sidebarExpanded = !_sidebarExpanded;
  const app = document.getElementById('app');
  const sidebar = document.getElementById('sidebar');

  if (_sidebarExpanded) {
    app.classList.add('sidebar-expanded');
    sidebar.classList.add('expanded');
    // 渲染侧边栏内容
    if (!document.getElementById('sidebar-panels-container').hasChildNodes()) {
      // 首次展开，初始化侧边栏面板
      initSidebarPanels();
    }
    switchSidebarTab('history');
  } else {
    app.classList.remove('sidebar-expanded');
    sidebar.classList.remove('expanded');
  }

  try { sessionStorage.setItem('sidebar_expanded', _sidebarExpanded ? '1' : '0'); } catch(e) {}
}

function initSidebarPanels() {
  const container = document.getElementById('sidebar-panels-container');
  ['history', 'skills', 'tools', 'personality', 'memory'].forEach(name => {
    const panel = document.createElement('div');
    panel.className = 'sidebar-panel';
    panel.id = 'panel-' + name;
    container.appendChild(panel);
    renderPanelContent(name, 'panel-' + name);
  });
}

// ── 侧边栏标签切换 ──
function switchSidebarTab(panelName) {
  document.querySelectorAll('#sidebar .sidebar-tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector(`#sidebar .sidebar-tab[data-panel="${panelName}"]`);
  if (tab) tab.classList.add('active');

  document.querySelectorAll('#sidebar .sidebar-panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('panel-' + panelName);
  if (panel) {
    panel.classList.add('active');
    loadSidebarModule(panelName);
  }
  try { sessionStorage.setItem('sidebar_active_panel', panelName); } catch(e) {}
}

// ── 模块数据加载路由 ──
function loadSidebarModule(name) {
  switch(name) {
    case 'history': typeof loadHistory === 'function' && loadHistory(); break;
    case 'skills': typeof loadSkills === 'function' && loadSkills(); break;
    case 'tools': typeof loadTools === 'function' && loadTools(); break;
    case 'personality': typeof loadPersonality === 'function' && loadPersonality(); break;
    case 'memory': typeof loadMemory === 'function' && loadMemory(); break;
  }
}

// ── 右侧状态面板刷新 ──
async function updateStatusPanel() {
  try {
    const r = await fetch('/api/health');
    const data = await r.json();
    const healthMap = {};
    (data || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function setSP(elId, sensorKey, unit) {
      const el = document.getElementById(elId);
      if (!el) return;
      const d = healthMap[sensorKey];
      if (d) {
        el.textContent = d.value + (unit || '');
        el.className = 'sp-value ' + (d.severity || 'normal');
      } else {
        el.textContent = '-';
        el.className = 'sp-value';
      }
    }

    setSP('sp-cpu', 'cpu_usage', '%');
    setSP('sp-memory', 'memory_usage', '%');
    setSP('sp-disk', 'disk_usage', '%');
    setSP('sp-battery', 'battery', '%');
    setSP('sp-network', 'network', '');

    // 传感器计数
    try {
      const sr = await fetch('/api/sensors');
      const sensors = await sr.json();
      const on = sensors.filter(s => s.enabled).length;
      const spSensors = document.getElementById('sp-sensors');
      if (spSensors) {
        spSensors.textContent = on + '/' + sensors.length;
        spSensors.className = 'sp-value normal';
      }
    } catch(e) {}

    // 模式
    try {
      const mr = await fetch('/api/mode');
      const mode = await mr.json();
      const spMode = document.getElementById('sp-mode');
      if (spMode) {
        spMode.textContent = mode.label || mode.mode || '-';
        spMode.className = 'sp-value normal';
      }
    } catch(e) {}

    // 事件流
    updateStatusEvents(healthMap);

  } catch(e) { console.error('Status panel update error:', e); }
}

function updateStatusEvents(healthMap) {
  const el = document.getElementById('sp-events');
  if (!el) return;
  const events = [];
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  Object.entries(healthMap).forEach(([key, m]) => {
    if (m.severity === 'warning' || m.severity === 'critical') {
      events.push({ time: timeStr, text: (m.description || key) + ': ' + m.value + (m.unit||''), cls: m.severity });
    }
  });

  if (events.length === 0) {
    el.innerHTML = '<div class="sidebar-empty">系统正常</div>';
    return;
  }
  el.innerHTML = events.map(e =>
    `<div class="sp-event"><span style="color:${e.cls==='critical'?'#f85149':'#d29922'}">⚠</span> ${e.text}</div>`
  ).join('');
}

// ── 标签栏指标刷新 ──
async function updateTabMetrics() {
  try {
    const r = await fetch('/api/health');
    const data = await r.json();
    const healthMap = {};
    (data || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    function metricHTML(key, label, unit) {
      const d = healthMap[key];
      if (!d) return '';
      return `<span>${label} <b class="tm-value ${d.severity || 'normal'}">${d.value}${unit||''}</b></span>`;
    }

    const el = document.getElementById('tab-metrics');
    if (!el) return;
    const parts = [
      metricHTML('cpu_usage', 'CPU', '%'),
      metricHTML('memory_usage', '内存', '%'),
      metricHTML('disk_usage', '磁盘', '%'),
      metricHTML('battery', '电池', '%'),
    ].filter(Boolean);

    // 传感器
    try {
      const sr = await fetch('/api/sensors');
      const sensors = await sr.json();
      const on = sensors.filter(s => s.enabled).length;
      parts.push(`<span>📡 <b class="tm-value normal">${on}/${sensors.length}</b></span>`);
    } catch(e) {}

    el.innerHTML = parts.join('<span class="tm-sep">|</span>');
  } catch(e) {}
}

// ── Toast ──
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
      </div>`;
    document.body.appendChild(overlay);
    window._confirmResolve = resolve;
  });
}

// ── HTML 转义 ──
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
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

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', () => {
  // 恢复侧边栏展开状态
  try {
    const expanded = sessionStorage.getItem('sidebar_expanded');
    if (expanded === '1') {
      _sidebarExpanded = true;
      document.getElementById('app').classList.add('sidebar-expanded');
      document.getElementById('sidebar').classList.add('expanded');
      initSidebarPanels();
      const savedPanel = sessionStorage.getItem('sidebar_active_panel') || 'history';
      switchSidebarTab(savedPanel);
    }
  } catch(e) {}

  // 初始加载
  updateTabMetrics();
  updateStatusPanel();
});

// 定时刷新（10秒）
setInterval(() => {
  updateTabMetrics();
  updateStatusPanel();
}, 10000);
```

- [ ] **Step 2: 提交**

```bash
git add static/js/sidebar/sidebar.js
git commit -m "refactor: rewrite sidebar.js for floating panels + status panel + tab metrics"
```

---

### Task 7: 修改 panorama.js — 适配新 DOM

**Files:**
- Modify: `static/js/panorama.js`

- [ ] **Step 1: 重写 panorama.js**

主要改动：`switchPanoSection()` 控制新 DOM 结构，`loadPanorama()` 适配新的元素 ID。用以下内容完全替换：

```javascript
// ════════════════════════════════════════════════════════════
// 云枢 · 系统全景仪表盘
// ════════════════════════════════════════════════════════════

// ── 左侧导航切换 ──
function switchPanoSection(section) {
  document.querySelectorAll('.pano-nav-item').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`.pano-nav-item[data-pano-section="${section}"]`);
  if (navItem) navItem.classList.add('active');

  // 隐藏所有面板
  document.getElementById('pano-section-dashboard').style.display = 'none';
  document.querySelectorAll('.pano-detail-view').forEach(el => el.style.display = 'none');

  // 显示目标面板
  if (section === 'dashboard') {
    document.getElementById('pano-section-dashboard').style.display = 'block';
  } else {
    const target = document.getElementById('pano-section-' + section);
    if (target) {
      target.style.display = 'block';
      target.classList.add('active');
    }
  }

  // 加载数据
  loadPanorama();
}

// ── 加载全景数据 ──
async function loadPanorama() {
  try {
    const r = await fetch('/api/panorama');
    const d = await r.json();

    // 构建健康数据映射
    const healthMap = {};
    (d.health || []).forEach(m => { healthMap[m.sensor_name || ''] = m; });

    // ── 仪表盘总览：4 阶段卡片指标 ──
    const cpuEl = document.getElementById('pano-cpu-val');
    if (cpuEl) cpuEl.textContent = healthMap['cpu_usage'] ? healthMap['cpu_usage'].value + '%' : '-';
    const memEl = document.getElementById('pano-mem-val');
    if (memEl) memEl.textContent = healthMap['memory_usage'] ? healthMap['memory_usage'].value + '%' : '-';
    const sensorValEl = document.getElementById('pano-sensor-val');
    if (sensorValEl) sensorValEl.textContent = '📡 ' + (d.sensor_on||0) + '/' + (d.sensor_total||0);
    const batteryEl = document.getElementById('pano-battery-val');
    if (batteryEl) batteryEl.textContent = healthMap['battery'] ? healthMap['battery'].value + '%' : '-';

    // 认知
    const cogEl = document.getElementById('pano-cog-status');
    if (cogEl) {
      cogEl.textContent = d.cognitive_summary ? '活跃' : '待机';
      cogEl.style.color = d.cognitive_summary ? '#3fb950' : '#8b949e';
    }
    const rejectEl = document.getElementById('pano-reject-status');
    if (rejectEl) {
      rejectEl.textContent = d.can_accept ? '✓ 可执行' : '✗ 拒绝中';
      rejectEl.style.color = d.can_accept ? '#3fb950' : '#f85149';
    }

    // 记忆
    const msgEl = document.getElementById('pano-msg-count');
    if (msgEl) msgEl.textContent = d.message_count != null ? d.message_count : '-';
    const logEl = document.getElementById('pano-log-count');
    if (logEl) logEl.textContent = d.log_count != null ? d.log_count : '-';
    const svEl = document.getElementById('pano-summary-ver');
    if (svEl) svEl.textContent = d.summary_version || '无';
    const ciEl = document.getElementById('pano-compress-info');
    if (ciEl) ciEl.textContent = Math.round((d.compress_threshold||0.8)*100) + '% / ' + (d.token_limit||4096);

    // 行动
    const modeEl = document.getElementById('pano-mode');
    if (modeEl) modeEl.textContent = d.mode_label || '-';
    const toolsEl = document.getElementById('pano-tools');
    if (toolsEl) toolsEl.textContent = (d.tool_count || 0) + ' 个';
    const refEl = document.getElementById('pano-reflections');
    if (refEl) refEl.textContent = (d.reflection_count || 0) + ' 条';
    const llmEl = document.getElementById('pano-llm');
    if (llmEl) {
      llmEl.textContent = d.llm_configured ? '已连接' : '未配置';
      llmEl.style.color = d.llm_configured ? '#3fb950' : '#8b949e';
    }

    // ── 系统总览条 ──
    function setSysText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
    setSysText('pano-session', d.session_id || '-');
    setSysText('pano-interactions', (d.interaction_count || 0) + ' 次');
    setSysText('pano-sensor-total', (d.sensor_on||0) + '/' + (d.sensor_total||0));
    if (d.started_at) {
      const secs = Math.floor((Date.now() - new Date(d.started_at)) / 1000);
      setSysText('pano-uptime', Math.floor(secs/60) + ' 分 ' + (secs%60) + ' 秒');
    }
    const badge2 = document.getElementById('pano-mode-badge-2');
    if (badge2) {
      badge2.textContent = d.mode_label || '正常';
      badge2.className = 'status-badge ' + (d.mode || 'normal');
    }

    // ── 事件流 ──
    loadEvents(d);

    // ── 加载单层详情（如果当前在某详情视图） ──
    const activeDetail = document.querySelector('.pano-detail-view.active');
    if (activeDetail) {
      const sectionId = activeDetail.id; // e.g. "pano-section-phase1"
      const phase = sectionId.replace('pano-section-phase', '');
      if (phase && phase >= '1' && phase <= '4') {
        loadPhaseDetail(parseInt(phase), d);
      } else if (sectionId === 'pano-section-trace') {
        loadTraceDetail(d);
      } else if (sectionId === 'pano-section-system') {
        loadSystemDetail(d);
      }
    }

  } catch(e) { console.error('Panorama load error:', e); }
}

// ── 加载阶段详情 ──
function loadPhaseDetail(phase, d) {
  if (!d) return;

  if (phase === 1) {
    // 健康指标
    const healthEl = document.getElementById('pano-health');
    if (healthEl) {
      healthEl.innerHTML = (d.health || []).map(m => {
        let cls = 'pm-norm';
        if (m.severity === 'warning') cls = 'pm-warn';
        else if (m.severity === 'critical') cls = 'pm-crit';
        return `<div class="pano-metric ${cls}"><div class="pm-val">${m.value}${m.unit||''}</div><div class="pm-label">${m.description||m.sensor_name}</div></div>`;
      }).join('');
    }
    const scEl = document.getElementById('pano-sensor-count');
    if (scEl) scEl.textContent = (d.sensor_on||0) + '/' + (d.sensor_total||0);

    // 传感器分类
    const catsEl = document.getElementById('pano-sensor-categories');
    if (catsEl) {
      catsEl.innerHTML = (d.sensor_categories || []).map(c => {
        const sensorsHtml = (c.sensors || []).map(s =>
          `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;background:#0d1117;border-radius:3px;font-size:10px;margin:2px"><span style="width:5px;height:5px;border-radius:50%;background:${s.enabled?'#3fb950':'#30363d'}"></span>${s.name}</span>`
        ).join('');
        return `<div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:10px;margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><strong style="font-size:13px;color:#c9d1d9">${c.name}</strong><span style="font-size:11px;color:#8b949e">${c.count} 个</span></div><div style="font-size:10px;color:#8b949e;margin-bottom:4px">📌 ${c.source}</div><div style="display:flex;flex-wrap:wrap;gap:2px">${sensorsHtml}</div></div>`;
      }).join('') || '<div class="pano-text">暂无数据</div>';
    }

    // 标签
    const tagsEl = document.getElementById('pano-tag-grid');
    if (tagsEl) {
      tagsEl.innerHTML = (d.tag_dimensions || []).map(t => `<div class="pano-tag-item"><span class="tag-dim">${t.label}</span><span class="tag-vals">${t.values.join('、')}</span></div>`).join('');
    }
  }
  else if (phase === 2) {
    const trEl = document.getElementById('pano-translate-rules');
    if (trEl) trEl.innerHTML = (d.translate_rules || []).map(r => `<div style="margin-bottom:4px"><strong>${r.name}</strong>: ${r.message}</div>`).join('') || '暂无翻译规则';
    const ppEl = document.getElementById('pano-prompt-preview');
    if (ppEl) ppEl.textContent = d.prompt_template || '暂无模板';
  }
  else if (phase === 3) {
    const stEl = document.getElementById('pano-summary-text');
    if (stEl) stEl.textContent = d.summary_text || '（无摘要）';
    const lsEl = document.getElementById('pano-log-stats');
    if (lsEl) {
      const logs = d.log_stats || {};
      lsEl.innerHTML = Object.entries(logs).map(([k,v]) => `${k}: ${v} 次`).join(' | ') || '暂无日志';
    }
    const cidEl = document.getElementById('pano-compress-info-detail');
    if (cidEl) cidEl.innerHTML = `压缩触发阈值: ${d.compress_threshold || '-'} | Token 限制: ${d.token_limit || '-'}`;
  }
  else if (phase === 4) {
    const mlEl = document.getElementById('pano-mode-list');
    if (mlEl) {
      mlEl.innerHTML = (d.behavior_modes || []).map(m => `<div class="pano-mode-item"><span class="mm-name" style="color:${m.color}">${m.label}</span><span class="mm-desc">${m.desc}</span></div>`).join('');
    }
    const tlEl = document.getElementById('pano-tool-list');
    if (tlEl) tlEl.innerHTML = (d.tool_list || []).map(t => `<div style="font-size:11px;color:#8b949e">🔧 ${t.name}: ${t.desc}</div>`).join('') || '无工具';
    const permEl = document.getElementById('pano-perm-info');
    if (permEl) {
      const perm = d.permission_info || {};
      permEl.innerHTML = `检查次数: ${perm.check_count||0} | 已备份: ${perm.backup_count||0} 个文件 | 备份目录: ${perm.backup_dir||'-'}`;
    }
  }
}

function loadTraceDetail(d) {
  const body = document.getElementById('pano-trace-body');
  if (!body) return;
  if (d.last_trace && d.last_trace.length) {
    body.innerHTML = d.last_trace.map(t =>
      `<div class="trace-step"><span class="ts-phase ts-p${t.phase}">${t.phase_label}</span><span class="ts-icon">${t.icon}</span><span class="ts-text">${t.text}</span></div>`
    ).join('');
  } else {
    body.innerHTML = '<div class="pano-text sub">暂无交互记录</div>';
  }
}

function loadSystemDetail(d) {
  if (!d) return;
  function setT(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
  setT('pano-session-detail', d.session_id || '-');
  setT('pano-interactions-detail', (d.interaction_count || 0) + ' 次');
  setT('pano-sensor-total-detail', (d.sensor_on||0) + '/' + (d.sensor_total||0));
  if (d.started_at) {
    const secs = Math.floor((Date.now() - new Date(d.started_at)) / 1000);
    setT('pano-uptime-detail', Math.floor(secs/60) + ' 分 ' + (secs%60) + ' 秒');
  }
  const badge = document.getElementById('pano-mode-badge-detail');
  if (badge) {
    badge.textContent = d.mode_label || '正常';
    badge.className = 'status-badge ' + (d.mode || 'normal');
  }
}

// ── 事件流 ──
function loadEvents(d) {
  const body = document.getElementById('pano-events-body');
  if (!body) return;
  const events = [];
  const now = new Date();
  const timeStr = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0');

  if (d.mode) {
    events.push({ time: timeStr, icon: '🎯', text: '行为模式: ' + (d.mode_label || d.mode), tag: 'info', tagClass: 'ok' });
  }
  if (d.llm_configured) {
    events.push({ time: timeStr, icon: '🧠', text: 'LLM 已连接', tag: 'ok', tagClass: 'ok' });
  } else {
    events.push({ time: timeStr, icon: '⚠️', text: 'LLM 未配置', tag: 'warn', tagClass: 'warn' });
  }
  if (d.health) {
    d.health.filter(h => h.severity === 'warning' || h.severity === 'critical').forEach(w => {
      events.push({ time: timeStr, icon: '🔴', text: (w.description || w.sensor_name) + ': ' + w.value + (w.unit||''), tag: w.severity === 'critical' ? '异常' : '告警', tagClass: w.severity === 'critical' ? 'err' : 'warn' });
    });
  }

  if (events.length === 0) {
    body.innerHTML = '<div class="pano-empty">暂无事件</div>';
    return;
  }
  body.innerHTML = events.map(e =>
    '<div class="event-item"><span class="ei-time">' + e.time + '</span><span class="ei-icon">' + e.icon + '</span><span class="ei-text">' + e.text + '</span><span class="ei-tag ' + e.tagClass + '">' + e.tag + '</span></div>'
  ).join('');
}
```

- [ ] **Step 2: 提交**

```bash
git add static/js/panorama.js
git commit -m "refactor: update panorama.js for new master-detail DOM structure"
```

---

### Task 8: 修改 index.html 内联 JS — 标签切换逻辑

**Files:**
- Modify: `templates/index.html` (内联 `<script>` 部分)

- [ ] **Step 1: 更新 `switchTab()` 函数**

在 `switchTab()` 函数中，切换标签时同步控制右侧状态面板和 `#app` 的 class：

```javascript
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${name}"]`).classList.add('active');
  document.getElementById(name + '-view').classList.add('active');

  const app = document.getElementById('app');
  if (name === 'panorama') {
    // 全景视图：隐藏右侧状态面板
    app.classList.add('no-right-panel');
    document.getElementById('status-panel').classList.add('hidden');
    loadPanorama();
  } else {
    // 对话视图：显示右侧状态面板
    app.classList.remove('no-right-panel');
    document.getElementById('status-panel').classList.remove('hidden');
  }
}
```

- [ ] **Step 2: 更新初始化代码**

将现有的初始化调用：

```javascript
refreshLeft();
loadChatStatus();
document.getElementById('chat-input').focus();
```

替换为：

```javascript
updateTabMetrics();
updateStatusPanel();
document.getElementById('chat-input').focus();
```

- [ ] **Step 3: 更新定时刷新代码**

```javascript
setInterval(function() {
  updateTabMetrics();
  updateStatusPanel();
  if (document.getElementById('panorama-view').classList.contains('active')) {
    loadPanorama();
  }
}, 10000);
```

- [ ] **Step 4: 删除已废弃的函数**

删除 `refreshLeft()`、`loadChatHealth()`、`loadChatStatus()` 三个函数，它们的功能已由 `updateTabMetrics()` 和 `updateStatusPanel()` 替代。

- [ ] **Step 5: 提交**

```bash
git add templates/index.html
git commit -m "refactor: update inline JS - tab switching controls status panel visibility"
```

---

### Task 9: 验证 — 启动服务器并检查布局

**Files:**
- 无新建或修改（仅验证）

- [ ] **Step 1: 启动 Flask 开发服务器**

```bash
cd /c/Users/Administrator/agent && python app_server.py &
```

等待服务器启动后，访问 http://localhost:5000

- [ ] **Step 2: 手动验证检查清单**

在浏览器中验证以下功能：

**布局检查：**
- [ ] 顶栏完整显示（标题 + 设置/刷新/清空按钮）
- [ ] 标签栏显示对话/全景切换 + 右侧健康指标
- [ ] 左侧 48px 图标栏显示 5 个图标 + ☰
- [ ] 对话视图右侧 170px 状态面板显示指标 + 事件流
- [ ] 聊天消息区和输入框正常工作

**图标栏交互：**
- [ ] 悬停图标显示 tooltip 标签
- [ ] 点击图标弹出浮层面板（240px，含完整内容）
- [ ] 点击浮层外部或 × 关闭浮层
- [ ] 再次点击同一图标关闭浮层
- [ ] 切换不同图标，前一个浮层先关闭
- [ ] 点击 ☰ 展开完整 280px 侧边栏
- [ ] 再次点击 ☰ 收缩侧边栏

**全景视图：**
- [ ] 切换到全景标签，状态面板隐藏
- [ ] 左侧 130px 导航栏 + 右侧仪表盘内容
- [ ] 默认显示仪表盘总览（流水线 + 2x2 卡片 + 事件流 + 系统总览）
- [ ] 点击导航项切换到对应详情视图
- [ ] 详情视图的内容正确加载

**对话功能：**
- [ ] 发送消息正常
- [ ] 接收回复正常
- [ ] 清空对话正常
- [ ] 设置弹窗正常

**数据刷新：**
- [ ] 标签栏指标 10 秒自动刷新
- [ ] 右侧状态面板 10 秒自动刷新
- [ ] 全景视图（如可见）10 秒自动刷新

- [ ] **Step 3: 检查浏览器控制台**

打开 DevTools Console，确认无 JS 报错。

- [ ] **Step 4: 如有问题，修复后重新验证**

---

### Task 10: 最终提交

- [ ] **Step 1: 确认所有改动已提交**

```bash
git status
git log --oneline -10
```

- [ ] **Step 2: 如有遗漏，补交**

```bash
git add -A
git commit -m "chore: final cleanup for layout redesign"
```
