# 云枢控制台三栏布局重构设计

## 概述

基于前几轮布局迭代（280px 侧栏 → 48px 图标栏+浮层 → 200px 导航栏）的经验教训，对云枢数字生命体 Web 主界面进行解耦式重构，用 CSS Grid 三栏结构替换当前耦合的内联布局。

## 前几轮经验教训

1. **布局反复变化** — 三周内三次大改布局结构，需要稳定为长期可用的框架
2. **布局耦合太紧** — CSS/JS/HTML 之间无清晰边界，改一处波及全局
3. **状态面板信息密度不够** — 纯文本指标不够直观，事件流缺少自动滚动
4. **管理模块体验割裂** — 浮层面板操作受限、视觉突兀，与主视图体验不一致

## 核心设计决策

- 所有管理模块（历史/技能/工具/人格/记忆）嵌入主内容区，与对话/全景同级切换
- CSS Grid 三栏骨架，全部尺寸由 CSS 变量控制
- 组件间通过事件总线通信，不直接操作对方 DOM
- 状态面板可折叠，折叠时主内容区自动扩展

## 整体布局

```
┌──────────────────────────────────────────────────────────────┐
│  Topbar · 44px  [🤖 云枢 · 数字生命体]    [⚙ 设置] [⟳ 刷新] │
├────────┬─────────────────────────────────┬───────────────────┤
│        │                                 │                    │
│ 导航栏  │  主内容区                       │  状态面板 220px     │
│ 200px  │  ┌──────────────────────────┐   │  可折叠 ◀         │
│ 可折叠 │  │  当前活动视图               │   │  ┌─────────────┐  │
│ 至48px │  │  (chat/panorama/history/  │   │  │ CPU   45% ██ │  │
│        │  │   skills/tools/          │   │  │ 内存  62% ████│  │
│ 💬 对话 │  │   personality/memory)    │   │  │ 磁盘  71% ████│  │
│ 🗺 全景 │  │                          │   │  │ 电池  85% ████│  │
│ ────── │  │  所有视图平级              │   │  │ 传感器 5/8    │  │
│ 🕐 历史 │  │  切换时 display 切换       │   │  │ 模式: 正常 ●  │  │
│ 🔧 技能 │  │  + 淡入动画               │   │  ├─────────────┤  │
│ 🛠 工具 │  │                          │   │  │ 实时事件流    │  │
│ 🎭 人格 │  │                          │   │  │ 10:23 ✓      │  │
│ 🧠 记忆 │  │                          │   │  │ 10:22 ⚠ CPU  │  │
│        │  │                          │   │  │ 10:20 ✓      │  │
│ ────── │  │                          │   │  │            │  │
│ ⚙ 设置 │  │                          │   │  │            │  │
│ ⟳ 刷新 │  │                          │   │  │            │  │
└────────┴─────────────────────────────────┴───────────────────┘
```

### Grid 声明

```css
:root {
  --nav-w: 200px;           --nav-collapsed-w: 48px;
  --panel-w: 220px;         --topbar-h: 44px;
  --nav-bg: #161b22;        --content-bg: #0d1117;
  --panel-bg: #161b22;      --border-color: #30363d;
  --accent: #58a6ff;        --success: #3fb950;
  --warning: #d29922;       --danger: #f85149;
}

#app {
  display: grid;
  grid-template-columns: var(--nav-w) 1fr var(--panel-w);
  grid-template-rows: var(--topbar-h) 1fr;
  height: 100vh;
}

#app.panel-collapsed {
  grid-template-columns: var(--nav-w) 1fr 0px;
}
```

## 组件详细设计

### 1. 顶栏 (Topbar)

保持不变。44px 高度，左侧品牌标题，右侧操作按钮（设置/刷新/清空对话）。

### 2. 左侧导航栏

**宽度变化：**
- 默认：200px，显示图标 + 文字
- 折叠后：48px，仅显示图标
- 折叠后 hover：整栏展开为 200px（不只是 tooltip）

**交互：**
- 折叠按钮：导航栏右下角 `◀` / `▶` 按钮
- 折叠状态存入 `sessionStorage`
- < 768px 自动折叠
- 激活项：左侧 3px `#58a6ff` 竖条 + 深色背景

**导航项列表：**
| 组 | 项 |
|----|----|
| 核心 | 对话、全景 |
| 管理 | 历史会话、技能管理、工具集成、人格配置、记忆管理 |
| 操作 | 设置（弹窗）、刷新 |

### 3. 右侧状态面板 (220px)

**三区块：**
1. **系统状态** — CPU、内存、磁盘（进度条 + 数值 + 三级色码）、电池、网络、传感器
2. **运行信息** — 会话ID、交互计数、运行时长、模式徽标
3. **实时事件流** — 自动滚动，保留最近 20 条，警告/危险高亮

**隐藏机制：**
- 面板左侧边缘 `◀` / `▶` 折叠按钮
- `#app.panel-collapsed` 类控制 Grid 第三列变为 `0px`
- 折叠状态存入 `sessionStorage`
- < 1000px 自动隐藏

**刷新：** 10 秒定时通过事件总线推送更新

### 4. 主内容区 & 视图切换

**视图清单：**
| 视图 | 类型 | 加载策略 |
|------|------|---------|
| chat | 核心 | DOM 常驻 |
| panorama | 核心 | DOM 常驻 |
| history | 管理 | 切换时懒加载，之后 DOM 保留 |
| skills | 管理 | 切换时懒加载，之后 DOM 保留 |
| tools | 管理 | 切换时懒加载，之后 DOM 保留 |
| personality | 管理 | 切换时懒加载，之后 DOM 保留 |
| memory | 管理 | 切换时懒加载，之后 DOM 保留 |

**所有视图通过统一的 app.switchView() 切换：**
- 先隐藏所有 `.view`（`display:none`）
- 显示目标视图（`display:flex` + `opacity` 过渡）
- 如视图尚未加载，触发对应的 load 函数
- 管理视图渲染为全尺寸页面，不再是浮层

**管理视图通用布局：**
```
┌────────────────────────────────────┐
│  视图标题  [搜索框]  [操作按钮]      │
├────────────────────────────────────┤
│  ┌──────────────────────────────┐  │
│  │  内容列表 / 卡片网格           │  │
│  │  (各视图自定)                  │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

### 5. 全景视图

保持现有全景功能，复用现有的 `panorama.css` 样式。导航切换为在主内容区全宽显示。

### 6. 设置弹窗

保持不变。

## 数据流

```
定时器(10s)
  → fetch /api/health
  → app.emit('health:updated', data)
    → status-panel 更新指标 + 进度条
    → panorama 更新仪表盘（如果显示）

视图切换
  → app.switchView('history')
    → 判定是否需要懒加载
    → fetch('/api/history')
    → 渲染到 #view-history

用户操作
  → 导航栏点击
  → app.switchView() / showSettings()
```

## CSS 文件架构

```
static/css/
├── base.css           — CSS 变量、reset、通用颜色/字体/滚动条
├── layout.css         — Grid 三栏骨架
├── nav.css            — 左侧导航栏（折叠/展开/激活）
├── status-panel.css   — 右侧状态面板（指标/进度条/事件流）
├── chat.css           — 对话视图（气泡/输入区）
├── panorama.css       — 全景视图（精简）
├── views.css          — 管理视图通用样式
├── modals.css         — 设置弹窗/确认弹窗/Toast
└── responsive.css     — 所有 media queries
```

## JavaScript 文件架构

```
static/js/
├── app.js           — 全局 app 对象、事件总线、状态管理
├── nav.js           — 导航栏交互
├── status-panel.js  — 状态面板渲染 + 定时刷新
├── chat.js          — 对话逻辑
├── panorama.js      — 全景视图逻辑
└── views/
    ├── history.js
    ├── skills.js
    ├── tools.js
    ├── personality.js
    └── memory.js
```

### 事件总线 API

```javascript
app.on('health:updated', handler)  // 订阅
app.off('health:updated', handler) // 取消订阅
app.emit('health:updated', data)   // 发布

app.state.panelCollapsed  // 全局状态
app.setState('panelCollapsed', true) // 触发更新
```

### 视图注册 API

```javascript
app.registerView('history', {
  load: () => fetch('/api/history').then(render),
  template: '<div class="view" id="view-history">...</div>',
  keepAlive: true,  // 切换后保留 DOM
})
```

## 响应式策略

| 断点 | 导航栏 | 状态面板 |
|------|--------|---------|
| > 1000px | 200px 展开 | 220px 显示 |
| 768–1000px | 200px 展开 | 自动隐藏，可点击浮动按钮临时显示 |
| < 768px | 自动折叠为 48px | 自动隐藏 |
| < 600px | 48px 折叠 | 隐藏 |

## API 变更

无。所有现有 API 端点保持不变。

## 保留清单

- 顶栏标题 + 操作按钮
- 对话视图（消息气泡 + 输入区）
- 全景视图（流水线、4 阶段卡片、传感器清单、标签体系、翻译规则、提示词模板、摘要/日志/压缩信息、行为模式/工具/权限、系统总览、交互追踪）
- 5 个管理模块的数据和功能（历史/技能/工具/人格/记忆）
- 设置弹窗
- 所有 API 端点
- 10 秒定时刷新

## 删除清单

- 浮层面板机制（`toggleFloatingPanel`、`closeFloatingPanel`）
- `sidebar-resize-handle`（拖拽调整宽度）
- `#sidebar` 的 `collapsed` / `hidden` 类切换
- `#sidebar-show-btn` 显示按钮
- 管理模块的浮层模式

## 实现要点

1. **HTML 重写**：从 grid 布局开始，所有视图平级放置在 `<main>` 内
2. **CSS 变量驱动**：所有尺寸、颜色、间距由 `:root` 变量控制
3. **事件总线**：`app.on/emit` 替代直接函数调用
4. **保留全景 JS**：`panorama.js` 微调即可适配新 DOM 结构
5. **管理视图复用**：现有 `loadHistory()`、`loadSkills()` 等函数只需改变渲染目标（从浮层容器改为主内容区）
6. **状态面板折叠**：纯 CSS 实现（class 切换），JS 只负责存储状态
