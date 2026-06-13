# Sidebar 导航系统重新设计

## 目标
将当前分散的导航入口（顶部标签栏 + 左侧图标栏 + 可展开侧边栏）统一为单列左侧导航栏，提升操作效率和视觉一致性。

## 设计

### 布局变化
```
┌───────┬─────────────────────────────────┬──────────┐
│ 导航栏  │  主内容区                      │ 状态面板  │
│ 200px  │  (flex 1)                      │ 170px    │
├───────┼─────────────────────────────────┤          │
│ 🤖 云枢 │  💬 对话  或  🗺 全景           │          │
│───────│  (平滑切换动画)                  │          │
│ 💬 对话│                                 │          │
│ 🗺 全景│                                 │          │
│───────│                                 │          │
│ 🕐 历史│                                 │          │
│ 🔧 技能│                                 │          │
│ 🛠 工具│                                 │          │
│ 🎭 人格│                                 │          │
│ 🧠 记忆│                                 │          │
│       │                                 │          │
│ ⚙ 设置│                                 │          │
└───────┴─────────────────────────────────┴──────────┘
```

### 文件改动

| 文件 | 改动 |
|------|------|
| `templates/index.html` | 移除 `#tabs`、`#icon-bar`；重写 `#sidebar` 为固定导航 |
| `static/css/layout.css` | Grid 布局改为 200px 导航栏 |
| `static/css/sidebar.css` | 新增导航按钮样式，删除展开/折叠相关样式 |
| `static/css/responsive.css` | 小屏幕导航栏折叠为图标 |
| `static/js/sidebar/sidebar.js` | 简化逻辑：移除 sidebar-expanded，新增 nav 切换 |

### 导航按钮交互
- **对话/全景**: 切换 `#chat-view` / `#panorama-view`（`display:flex` 切换 + CSS 过渡动画）
- **历史/技能/工具/人格/记忆**: 调用 `toggleFloatingPanel()` 打开浮层面板（同现有逻辑）
- **设置**: 调用 `showSettings()` 打开设置弹窗

### 激活状态
- 左侧 3px 蓝色边框 `border-left: 3px solid #58a6ff`
- 背景色 `#1c2333`
- 文字色 `#58a6ff`

### 切换动画
```css
.tab-view {
    opacity: 0;
    transform: translateY(8px);
    transition: opacity 0.25s ease, transform 0.25s ease;
    pointer-events: none;
}
.tab-view.active {
    opacity: 1;
    transform: translateY(0);
    pointer-events: auto;
}
```

### 响应式
- `>=768px`: 完整导航栏，显示图标 + 文字
- `<768px`: 导航栏宽度缩为 48px，仅显示图标；hover/active 时展开显示标签
- `<600px`: 导航栏折叠为底部横排（可选，超出范围）
