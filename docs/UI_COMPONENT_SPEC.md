# 云枢 UI 组件库规范

## 基于 AIRI Stage-UI 的组件设计

---

## 1. 组件设计原则

### 1.1 核心理念
参考 AIRI 的 `@proj-airi/stage-ui` 设计理念，云枢的组件库遵循：

- **统一性**: 所有组件遵循同一视觉语言
- **可组合性**: 组件可自由组合，满足各种场景
- **可定制性**: 通过 CSS Variables 实现主题适配
- **无障碍性**: 符合 WCAG 标准

### 1.2 组件分类

```
组件库
├── 基础组件 (Base)
│   ├── Button
│   ├── Input
│   ├── Select
│   ├── Switch
│   ├── Slider
│   └── Checkbox
├── 数据展示 (Data)
│   ├── Badge
│   ├── Progress
│   ├── Tag
│   └── StatusIndicator
├── 布局组件 (Layout)
│   ├── Container
│   ├── Card
│   ├── Panel
│   └── Divider
├── 反馈组件 (Feedback)
│   ├── Tooltip
│   ├── Toast
│   ├── Modal
│   └── Loading
├── 导航组件 (Navigation)
│   ├── Tab
│   ├── Menu
│   ├── Breadcrumb
│   └── Pagination
└── 特殊组件 (Special)
    ├── Mascot
    ├── ChatBubble
    ├── StatusRing
    └── EmotionIndicator
```

---

## 2. 基础组件规范

### 2.1 Button 按钮

#### 变体 (Variants)

```css
/* 主要按钮 - 云枢青 */
.btn-primary {
    background: var(--accent-primary);
    color: var(--bg-deep);
    border: none;
    border-radius: var(--radius-md);
    padding: var(--space-sm) var(--space-lg);
    font-weight: 500;
    transition: all var(--duration-fast) var(--ease-breathe);
}

.btn-primary:hover {
    background: var(--accent-secondary);
    transform: translateY(-1px);
    box-shadow: var(--shadow-glow);
}

.btn-primary:active {
    transform: translateY(0) scale(0.98);
}

/* 次要按钮 - 描边 */
.btn-secondary {
    background: transparent;
    color: var(--accent-primary);
    border: 1px solid var(--accent-primary);
}

.btn-secondary:hover {
    background: rgba(79, 209, 197, 0.1);
}

/* 幽灵按钮 */
.btn-ghost {
    background: transparent;
    color: var(--text-secondary);
    border: 1px solid var(--border-subtle);
}

.btn-ghost:hover {
    background: var(--bg-secondary);
    border-color: var(--accent-primary);
    color: var(--accent-primary);
}

/* 危险按钮 */
.btn-danger {
    background: var(--color-error);
    color: white;
}

/* 禁用状态 */
.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
}
```

#### 尺寸 (Sizes)

```css
.btn-sm { padding: var(--space-xs) var(--space-md); font-size: 13px; }
.btn-md { padding: var(--space-sm) var(--space-lg); font-size: 15px; }
.btn-lg { padding: var(--space-md) var(--space-xl); font-size: 17px; }
```

#### 图标按钮

```css
.btn-icon {
    width: 36px;
    height: 36px;
    padding: 0;
    border-radius: var(--radius-md);
    display: flex;
    align-items: center;
    justify-content: center;
}

.btn-icon.btn-sm { width: 28px; height: 28px; }
.btn-icon.btn-lg { width: 44px; height: 44px; }
```

### 2.2 Input 输入框

```css
.input {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-sm) var(--space-md);
    color: var(--text-primary);
    font-size: var(--font-body);
    transition: all var(--duration-fast) var(--ease-breathe);
    width: 100%;
}

.input:hover {
    border-color: var(--text-muted);
}

.input:focus {
    outline: none;
    border-color: var(--accent-primary);
    box-shadow: 0 0 0 3px var(--accent-glow);
}

.input::placeholder {
    color: var(--text-muted);
}

/* 错误状态 */
.input-error {
    border-color: var(--color-error);
}

.input-error:focus {
    box-shadow: 0 0 0 3px rgba(252, 129, 129, 0.3);
}

/* 禁用状态 */
.input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

/* 带前缀/后缀 */
.input-wrapper {
    position: relative;
    display: flex;
}

.input-prefix,
.input-suffix {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    color: var(--text-muted);
    pointer-events: none;
}

.input-prefix { left: var(--space-md); }
.input-suffix { right: var(--space-md); }

.input-with-prefix { padding-left: 40px; }
.input-with-suffix { padding-right: 40px; }
```

### 2.3 Select 下拉框

```css
.select {
    appearance: none;
    background: var(--bg-secondary) url("data:image/svg+xml,...") no-repeat right 12px center;
    background-size: 16px;
    padding-right: 36px;
    cursor: pointer;
}

/* 选项面板 */
.select-dropdown {
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-lg);
    max-height: 300px;
    overflow-y: auto;
    z-index: 100;
}

.select-option {
    padding: var(--space-sm) var(--space-md);
    cursor: pointer;
    transition: background var(--duration-fast);
}

.select-option:hover {
    background: var(--bg-secondary);
}

.select-option.selected {
    background: rgba(79, 209, 197, 0.1);
    color: var(--accent-primary);
}
```

### 2.4 Switch 开关

```css
.switch {
    width: 44px;
    height: 24px;
    background: var(--bg-secondary);
    border-radius: 12px;
    position: relative;
    cursor: pointer;
    transition: background var(--duration-fast) var(--ease-breathe);
}

.switch::after {
    content: '';
    position: absolute;
    width: 18px;
    height: 18px;
    background: white;
    border-radius: 50%;
    top: 3px;
    left: 3px;
    transition: transform var(--duration-fast) var(--ease-spring);
}

.switch.active {
    background: var(--accent-primary);
}

.switch.active::after {
    transform: translateX(20px);
}

/* 禁用状态 */
.switch:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

### 2.5 Slider 滑块

```css
.slider {
    -webkit-appearance: none;
    width: 100%;
    height: 6px;
    background: var(--bg-secondary);
    border-radius: 3px;
    outline: none;
}

.slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 18px;
    height: 18px;
    background: var(--accent-primary);
    border-radius: 50%;
    cursor: pointer;
    transition: transform var(--duration-fast);
}

.slider::-webkit-slider-thumb:hover {
    transform: scale(1.2);
}

.slider::-webkit-slider-thumb:active {
    transform: scale(1.1);
}

/* 禁用状态 */
.slider:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

---

## 3. 数据展示组件

### 3.1 Badge 徽章

```css
.badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
    border-radius: var(--radius-full);
    line-height: 1.4;
}

/* 变体 */
.badge-default {
    background: var(--bg-secondary);
    color: var(--text-secondary);
}

.badge-primary {
    background: rgba(79, 209, 197, 0.2);
    color: var(--accent-primary);
}

.badge-success {
    background: rgba(72, 187, 120, 0.2);
    color: var(--color-success);
}

.badge-warning {
    background: rgba(237, 137, 54, 0.2);
    color: var(--color-warning);
}

.badge-error {
    background: rgba(252, 129, 129, 0.2);
    color: var(--color-error);
}
```

### 3.2 Progress 进度条

```css
.progress {
    width: 100%;
    height: 8px;
    background: var(--bg-secondary);
    border-radius: var(--radius-full);
    overflow: hidden;
}

.progress-bar {
    height: 100%;
    background: var(--accent-primary);
    border-radius: var(--radius-full);
    transition: width var(--duration-slow) var(--ease-breathe);
}

/* 变体 */
.progress-success .progress-bar { background: var(--color-success); }
.progress-warning .progress-bar { background: var(--color-warning); }
.progress-error .progress-bar { background: var(--color-error); }

/* 带标签 */
.progress-label {
    display: flex;
    justify-content: space-between;
    margin-bottom: var(--space-xs);
    font-size: var(--font-caption);
    color: var(--text-secondary);
}
```

### 3.3 Tag 标签

```css
.tag {
    display: inline-flex;
    align-items: center;
    gap: var(--space-xs);
    padding: var(--space-xs) var(--space-sm);
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    font-size: var(--font-caption);
    color: var(--text-secondary);
}

.tag-close {
    width: 14px;
    height: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    border-radius: 50%;
    transition: background var(--duration-fast);
}

.tag-close:hover {
    background: var(--bg-elevated);
}
```

### 3.4 StatusIndicator 状态指示器

```css
.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
}

.status-dot.online { background: var(--color-success); }
.status-dot.busy { background: var(--color-warning); }
.status-dot.offline { background: var(--text-muted); }
.status-dot.error { background: var(--color-error); }

/* 带动画的在线状态 */
.status-dot.online {
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}
```

---

## 4. 布局组件

### 4.1 Card 卡片

```css
.card {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    padding: var(--space-lg);
    transition: all var(--duration-fast) var(--ease-breathe);
}

.card:hover {
    border-color: var(--accent-primary);
    box-shadow: var(--shadow-glow);
}

/* 卡片变体 */
.card-glass {
    background: var(--bg-glass);
    backdrop-filter: blur(20px);
}

.card-elevated {
    background: var(--bg-elevated);
    box-shadow: var(--shadow-md);
}

/* 卡片内部结构 */
.card-header {
    margin-bottom: var(--space-md);
    padding-bottom: var(--space-md);
    border-bottom: 1px solid var(--border-subtle);
}

.card-title {
    font-size: var(--font-h3);
    font-weight: 600;
    color: var(--text-primary);
}

.card-body {
    margin-bottom: var(--space-md);
}

.card-footer {
    padding-top: var(--space-md);
    border-top: 1px solid var(--border-subtle);
}
```

### 4.2 Panel 面板

```css
.panel {
    background: var(--bg-primary);
    border-right: 1px solid var(--border-subtle);
    height: 100%;
    overflow-y: auto;
}

.panel-header {
    position: sticky;
    top: 0;
    background: var(--bg-primary);
    padding: var(--space-md) var(--space-lg);
    border-bottom: 1px solid var(--border-subtle);
    z-index: 10;
}

.panel-content {
    padding: var(--space-lg);
}

.panel-footer {
    position: sticky;
    bottom: 0;
    background: var(--bg-primary);
    padding: var(--space-md) var(--space-lg);
    border-top: 1px solid var(--border-subtle);
}

/* 可折叠面板 */
.panel-collapsible .panel-header {
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.panel-collapsible .panel-header::after {
    content: '▼';
    font-size: 10px;
    transition: transform var(--duration-fast);
}

.panel-collapsed .panel-header::after {
    transform: rotate(-90deg);
}

.panel-collapsed .panel-content {
    display: none;
}
```

### 4.3 Divider 分割线

```css
.divider {
    height: 1px;
    background: var(--border-subtle);
    margin: var(--space-lg) 0;
}

.divider-vertical {
    width: 1px;
    height: auto;
    align-self: stretch;
    margin: 0 var(--space-lg);
}

/* 带文字的分割线 */
.divider-text {
    display: flex;
    align-items: center;
    text-align: center;
    color: var(--text-muted);
    font-size: var(--font-caption);
}

.divider-text::before,
.divider-text::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border-subtle);
}

.divider-text span {
    padding: 0 var(--space-md);
}
```

---

## 5. 反馈组件

### 5.1 Tooltip 提示

```css
.tooltip {
    position: absolute;
    background: var(--bg-elevated);
    border: 1px solid var(--border-accent);
    border-radius: var(--radius-sm);
    padding: var(--space-xs) var(--space-sm);
    font-size: var(--font-caption);
    color: var(--text-primary);
    box-shadow: var(--shadow-md);
    z-index: 1000;
    opacity: 0;
    transform: translateY(4px);
    transition: all var(--duration-fast) var(--ease-breathe);
    pointer-events: none;
    white-space: nowrap;
}

.tooltip.visible {
    opacity: 1;
    transform: translateY(0);
}

/* 位置变体 */
.tooltip-top { bottom: 100%; left: 50%; transform: translateX(-50%) translateY(-4px); }
.tooltip-bottom { top: 100%; left: 50%; transform: translateX(-50%) translateY(4px); }
.tooltip-left { right: 100%; top: 50%; transform: translateY(-50%) translateX(-4px); }
.tooltip-right { left: 100%; top: 50%; transform: translateY(-50%) translateX(4px); }
```

### 5.2 Toast 通知

```css
.toast-container {
    position: fixed;
    top: var(--space-lg);
    right: var(--space-lg);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
}

.toast {
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-md);
    min-width: 280px;
    max-width: 400px;
    box-shadow: var(--shadow-lg);
    animation: slideIn var(--duration-normal) var(--ease-spring);
}

@keyframes slideIn {
    from {
        opacity: 0;
        transform: translateX(100%);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

.toast-success { border-left: 3px solid var(--color-success); }
.toast-warning { border-left: 3px solid var(--color-warning); }
.toast-error { border-left: 3px solid var(--color-error); }
.toast-info { border-left: 3px solid var(--color-info); }

.toast-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-xs);
}

.toast-title {
    font-weight: 500;
    color: var(--text-primary);
}

.toast-close {
    cursor: pointer;
    color: var(--text-muted);
    transition: color var(--duration-fast);
}

.toast-close:hover {
    color: var(--text-primary);
}

.toast-content {
    font-size: var(--font-caption);
    color: var(--text-secondary);
}

.toast-progress {
    height: 3px;
    background: var(--bg-secondary);
    margin-top: var(--space-sm);
    border-radius: var(--radius-full);
    overflow: hidden;
}

.toast-progress-bar {
    height: 100%;
    background: var(--accent-primary);
    transition: width 1s linear;
}
```

### 5.3 Modal 模态框

```css
.modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    visibility: hidden;
    transition: all var(--duration-normal);
}

.modal-overlay.visible {
    opacity: 1;
    visibility: visible;
}

.modal {
    background: var(--bg-primary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-xl);
    width: 90%;
    max-width: 500px;
    max-height: 85vh;
    overflow: hidden;
    transform: scale(0.95);
    transition: transform var(--duration-normal) var(--ease-spring);
}

.modal-overlay.visible .modal {
    transform: scale(1);
}

.modal-header {
    padding: var(--space-lg);
    border-bottom: 1px solid var(--border-subtle);
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.modal-title {
    font-size: var(--font-h3);
    font-weight: 600;
}

.modal-close {
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    cursor: pointer;
    color: var(--text-muted);
    transition: all var(--duration-fast);
}

.modal-close:hover {
    background: var(--bg-secondary);
    color: var(--text-primary);
}

.modal-body {
    padding: var(--space-lg);
    overflow-y: auto;
    max-height: calc(85vh - 140px);
}

.modal-footer {
    padding: var(--space-md) var(--space-lg);
    border-top: 1px solid var(--border-subtle);
    display: flex;
    justify-content: flex-end;
    gap: var(--space-sm);
}

/* 尺寸变体 */
.modal-sm { max-width: 380px; }
.modal-lg { max-width: 680px; }
.modal-xl { max-width: 900px; }
.modal-full { max-width: 95vw; }
```

### 5.4 Loading 加载

```css
/* 旋转加载 */
.spinner {
    width: 24px;
    height: 24px;
    border: 2px solid var(--bg-secondary);
    border-top-color: var(--accent-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

/* 脉动加载 */
.pulse {
    width: 24px;
    height: 24px;
    background: var(--accent-primary);
    border-radius: 50%;
    animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { transform: scale(0.8); opacity: 0.5; }
    50% { transform: scale(1); opacity: 1; }
}

/* 骨架屏 */
.skeleton {
    background: linear-gradient(
        90deg,
        var(--bg-secondary) 25%,
        var(--bg-elevated) 50%,
        var(--bg-secondary) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.5s infinite;
    border-radius: var(--radius-sm);
}

@keyframes shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}

/* 加载文本 */
.loading-text {
    display: inline-flex;
    align-items: center;
    gap: var(--space-sm);
    color: var(--text-secondary);
    font-size: var(--font-caption);
}
```

---

## 6. 导航组件

### 6.1 Tab 标签页

```css
.tabs {
    display: flex;
    gap: var(--space-xs);
    border-bottom: 1px solid var(--border-subtle);
}

.tab {
    padding: var(--space-sm) var(--space-md);
    color: var(--text-secondary);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: all var(--duration-fast);
}

.tab:hover {
    color: var(--text-primary);
}

.tab.active {
    color: var(--accent-primary);
    border-bottom-color: var(--accent-primary);
}

/* 胶囊样式 */
.tabs-pill {
    border-bottom: none;
    background: var(--bg-secondary);
    padding: var(--space-xs);
    border-radius: var(--radius-md);
}

.tabs-pill .tab {
    border-bottom: none;
    border-radius: var(--radius-sm);
    margin-bottom: 0;
}

.tabs-pill .tab.active {
    background: var(--accent-primary);
    color: var(--bg-deep);
}
```

### 6.2 Menu 菜单

```css
.menu {
    min-width: 180px;
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-xs);
    box-shadow: var(--shadow-lg);
}

.menu-item {
    padding: var(--space-sm) var(--space-md);
    color: var(--text-secondary);
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: all var(--duration-fast);
    display: flex;
    align-items: center;
    gap: var(--space-sm);
}

.menu-item:hover {
    background: var(--bg-secondary);
    color: var(--text-primary);
}

.menu-item.active {
    background: rgba(79, 209, 197, 0.1);
    color: var(--accent-primary);
}

.menu-item.disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.menu-divider {
    height: 1px;
    background: var(--border-subtle);
    margin: var(--space-xs) 0;
}

.menu-submenu {
    position: relative;
}
```

### 6.3 Breadcrumb 面包屑

```css
.breadcrumb {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
    font-size: var(--font-caption);
    color: var(--text-muted);
}

.breadcrumb-item {
    color: var(--text-secondary);
    cursor: pointer;
    transition: color var(--duration-fast);
}

.breadcrumb-item:hover {
    color: var(--accent-primary);
}

.breadcrumb-item.current {
    color: var(--text-primary);
    cursor: default;
}

.breadcrumb-separator {
    color: var(--text-muted);
}
```

---

## 7. 特殊组件

### 7.1 ChatBubble 对话气泡

```css
.chat-container {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
    padding: var(--space-md);
    overflow-y: auto;
}

.chat-message {
    display: flex;
    gap: var(--space-sm);
    max-width: 80%;
}

.chat-message.user {
    flex-direction: row-reverse;
    align-self: flex-end;
}

.chat-message.assistant {
    flex-direction: row;
    align-self: flex-start;
}

/* 头像 */
.chat-avatar {
    width: 36px;
    height: 36px;
    border-radius: var(--radius-full);
    background: var(--bg-secondary);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    flex-shrink: 0;
}

.chat-message.user .chat-avatar {
    background: var(--accent-primary);
    color: var(--bg-deep);
}

/* 气泡 */
.chat-bubble {
    padding: var(--space-sm) var(--space-md);
    border-radius: var(--radius-lg);
    line-height: 1.5;
    position: relative;
}

.chat-message.user .chat-bubble {
    background: var(--accent-primary);
    color: var(--bg-deep);
    border-bottom-right-radius: var(--radius-sm);
}

.chat-message.assistant .chat-bubble {
    background: var(--bg-elevated);
    color: var(--text-primary);
    border: 1px solid var(--border-subtle);
    border-bottom-left-radius: var(--radius-sm);
}

/* 时间戳 */
.chat-time {
    font-size: var(--font-micro);
    color: var(--text-muted);
    margin-top: var(--space-xs);
}

/* 状态指示 */
.chat-status {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
    font-size: var(--font-micro);
    color: var(--text-muted);
}

.chat-status.sending { color: var(--color-info); }
.chat-status.sent { color: var(--color-success); }
.chat-status.error { color: var(--color-error); }
```

### 7.2 StatusRing 状态环

```css
.status-ring {
    width: 48px;
    height: 48px;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
}

.status-ring-track {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: conic-gradient(
        var(--ring-color) var(--ring-percent),
        var(--bg-secondary) var(--ring-percent)
    );
}

.status-ring-inner {
    position: absolute;
    inset: 4px;
    background: var(--bg-primary);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
}

.status-ring-value {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-primary);
}

.status-ring-label {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 2px;
}

/* 尺寸变体 */
.status-ring-sm { width: 32px; height: 32px; }
.status-ring-md { width: 48px; height: 48px; }
.status-ring-lg { width: 64px; height: 64px; }
```

### 7.3 EmotionIndicator 情绪指示器

```css
.emotion-indicator {
    display: flex;
    gap: var(--space-xs);
    padding: var(--space-xs);
    background: var(--bg-secondary);
    border-radius: var(--radius-full);
}

.emotion-badge {
    padding: 2px 8px;
    border-radius: var(--radius-full);
    font-size: 11px;
    font-weight: 500;
    transition: all var(--duration-fast);
    cursor: pointer;
}

.emotion-badge:hover {
    transform: scale(1.1);
}

/* 情绪色彩 */
.emotion-happy {
    background: linear-gradient(135deg, #4fd1c5, #81e6d9);
    color: var(--bg-deep);
}

.emotion-excited {
    background: linear-gradient(135deg, #f687b3, #ed8936);
    color: white;
}

.emotion-calm {
    background: linear-gradient(135deg, #63b3ed, #4299e1);
    color: white;
}

.emotion-tired {
    background: linear-gradient(135deg, #a0aec0, #718096);
    color: white;
}

.emotion-thinking {
    background: linear-gradient(135deg, #9f7aea, #805ad5);
    color: white;
}
```

---

## 8. 组件使用示例

### 8.1 基础表单

```vue
<template>
  <div class="form">
    <div class="form-group">
      <label class="label">用户名</label>
      <input type="text" class="input" placeholder="请输入用户名" />
    </div>
    
    <div class="form-group">
      <label class="label">状态</label>
      <select class="input select">
        <option>工作中</option>
        <option>空闲</option>
      </select>
    </div>
    
    <div class="form-group">
      <label class="label">音量</label>
      <input type="range" class="slider" min="0" max="100" />
    </div>
    
    <div class="form-actions">
      <button class="btn btn-secondary">取消</button>
      <button class="btn btn-primary">确认</button>
    </div>
  </div>
</template>

<style scoped>
.form { max-width: 400px; }
.form-group { margin-bottom: var(--space-md); }
.form-actions { display: flex; gap: var(--space-sm); justify-content: flex-end; }
</style>
```

### 8.2 卡片列表

```vue
<template>
  <div class="card-list">
    <div v-for="item in items" :key="item.id" class="card">
      <div class="card-header">
        <h3 class="card-title">{{ item.title }}</h3>
        <span class="badge badge-primary">{{ item.status }}</span>
      </div>
      <div class="card-body">
        <p>{{ item.description }}</p>
      </div>
      <div class="card-footer">
        <span class="text-muted">{{ item.date }}</span>
      </div>
    </div>
  </div>
</template>
```

---

**文档版本**: v1.0
**基于**: AIRI Stage-UI 设计理念
**最后更新**: 2026-05-30
