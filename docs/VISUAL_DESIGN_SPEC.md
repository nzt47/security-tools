# 云枢数字生命系统 - 视觉设计规范

## 文档信息
- **版本**: v1.0
- **创建日期**: 2026-05-30
- **设计风格**: AIRI + 东方禅意融合
- **适用平台**: 桌面端（主）、移动端（次）

---

## 1. 设计理念与愿景

### 1.1 核心理念
云枢的视觉设计融合了 **AIRI的桌面伴侣概念** 与 **东方禅意美学**，打造一个"有呼吸感的数字生命"。

### 1.2 设计关键词
- **有灵性**: 界面元素仿佛有生命，会呼吸、会眨眼、会回应
- **禅意空间**: 留白、流动、自然过渡
- **科技温度**: 冷峻的技术内核 + 温暖的人文关怀
- **轻盈透明**: 始终在线但不干扰，像空气一样自然

### 1.3 视觉隐喻
- **云枢** = 灵性 + 呼吸感 + 自然流动
- **数字生命** = 始终在线 + 自我感知 + 成长演化

---

## 2. 色彩系统

### 2.1 主色调

#### 暗色主题（默认，推荐）
```css
:root {
    /* 主背景层次 */
    --bg-deep: #0a0a0f;           /* 最深层背景 - 虚空 */
    --bg-primary: #12121a;        /* 主背景 - 夜空 */
    --bg-secondary: #1a1a24;      /* 次级背景 - 渐变层 */
    --bg-elevated: #242430;       /* 悬浮层 - 浮岛 */
    --bg-glass: rgba(26, 26, 36, 0.85);  /* 毛玻璃 */

    /* 强调色 - 云枢青 */
    --accent-primary: #4fd1c5;    /* 主强调 - 生命之光 */
    --accent-secondary: #81e6d9;   /* 次强调 - 清新 */
    --accent-glow: rgba(79, 209, 197, 0.3); /* 发光效果 */

    /* 功能色 */
    --text-primary: #e8e8ec;      /* 主文字 - 柔和白 */
    --text-secondary: #9090a0;    /* 次文字 - 烟灰 */
    --text-muted: #606070;        /* 弱化文字 */
    --border-subtle: rgba(255, 255, 255, 0.06);
    --border-accent: rgba(79, 209, 197, 0.2);
}
```

#### 亮色主题
```css
:root[data-theme="light"] {
    --bg-deep: #f5f5f7;
    --bg-primary: #ffffff;
    --bg-secondary: #fafafa;
    --bg-elevated: #ffffff;
    --bg-glass: rgba(255, 255, 255, 0.9);

    --accent-primary: #319795;    /* 更沉稳的青色 */
    --accent-secondary: #4fd1c5;

    --text-primary: #1a1a24;
    --text-secondary: #606070;
    --text-muted: #9090a0;
}
```

### 2.2 语义色彩

| 用途 | 颜色名称 | 色值 | 使用场景 |
|------|----------|------|----------|
| 成功 | 春芽绿 | `#48bb78` | 任务完成、正向反馈 |
| 警告 | 琥珀橙 | `#ed8936` | 低电量、内存警告 |
| 错误 | 珊瑚红 | `#fc8181` | 错误状态、过热警告 |
| 信息 | 天空蓝 | `#63b3ed` | 通知、信息提示 |
| 活力 | 樱粉 | `#f687b3` | 活跃状态、情感表达 |

### 2.3 渐变系统

```css
/* 云枢呼吸渐变 - 用于Mascot背景 */
--gradient-breathe: linear-gradient(
    135deg,
    rgba(79, 209, 197, 0.1) 0%,
    rgba(129, 230, 217, 0.05) 50%,
    rgba(79, 209, 197, 0.1) 100%
);

/* 能量流动渐变 - 用于状态指示 */
--gradient-energy: linear-gradient(
    90deg,
    transparent 0%,
    rgba(79, 209, 197, 0.3) 50%,
    transparent 100%
);

/* 情绪渐变 - 表达情感状态 */
--gradient-happy: linear-gradient(135deg, #4fd1c5, #81e6d9);
--gradient-excited: linear-gradient(135deg, #f687b3, #ed8936);
--gradient-tired: linear-gradient(135deg, #a0aec0, #718096);
--gradient-thinking: linear-gradient(135deg, #63b3ed, #4299e1);
```

---

## 3. 字体系统

### 3.1 字体家族

```css
/* 中文优先 */
--font-primary: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;

/* 英文/数字 */
--font-secondary: "Inter", "SF Pro Display", system-ui, sans-serif;

/* 代码/技术内容 */
--font-mono: "JetBrains Mono", "Fira Code", "Consolas", monospace;

/* 装饰/特殊 */
--font-decorative: "Ma Shan Zheng", "ZCOOL XiaoWei", cursive;
```

### 3.2 字体层级

| 层级 | 字体大小 | 字重 | 行高 | 使用场景 |
|------|----------|------|------|----------|
| H1 | 28px | 600 | 1.3 | 页面标题 |
| H2 | 22px | 600 | 1.35 | 模块标题 |
| H3 | 18px | 500 | 1.4 | 卡片标题 |
| Body | 15px | 400 | 1.6 | 正文内容 |
| Caption | 13px | 400 | 1.5 | 说明文字 |
| Micro | 11px | 400 | 1.4 | 标签、时间戳 |
| Code | 14px | 400 | 1.5 | 代码内容 |

---

## 4. 空间系统

### 4.1 基础间距

```css
/* 8px 基准系统 */
--space-xs: 4px;   /* 微间距 */
--space-sm: 8px;   /* 小间距 */
--space-md: 16px;  /* 标准间距 */
--space-lg: 24px;  /* 大间距 */
--space-xl: 32px;  /* 区块间距 */
--space-2xl: 48px; /* 区域间距 */
--space-3xl: 64px; /* 大区域 */
```

### 4.2 圆角系统

```css
/* 圆角层次 */
--radius-sm: 4px;     /* 微圆角 - 小按钮、标签 */
--radius-md: 8px;     /* 中圆角 - 卡片、输入框 */
--radius-lg: 16px;    /* 大圆角 - 模态框、面板 */
--radius-xl: 24px;    /* 超大圆角 - Mascot容器 */
--radius-full: 9999px; /* 完全圆角 - 头像、徽章 */
```

### 4.3 阴影系统

```css
/* 阴影层次 */
--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.4);
--shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.5);
--shadow-glow: 0 0 20px rgba(79, 209, 197, 0.3);  /* 发光效果 */
--shadow-inner: inset 0 2px 4px rgba(0, 0, 0, 0.3);
```

---

## 5. 动效系统

### 5.1 核心理念
动效是数字生命"呼吸感"的来源。所有动画都应该是**有机的**、**流畅的**、**有意义的**。

### 5.2 基础动效曲线

```css
/* 自然呼吸曲线 */
--ease-breathe: cubic-bezier(0.4, 0, 0.2, 1);
--ease-bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55);
--ease-spring: cubic-bezier(0.175, 0.885, 0.32, 1.275);

/* 标准过渡 */
--duration-instant: 100ms;
--duration-fast: 200ms;
--duration-normal: 300ms;
--duration-slow: 500ms;
--duration-breathe: 2000ms;  /* 呼吸周期 */
```

### 5.3 Mascot 专属动效

#### 呼吸动画
```css
@keyframes breathe {
    0%, 100% {
        transform: scale(1);
        opacity: 0.9;
    }
    50% {
        transform: scale(1.02);
        opacity: 1;
    }
}

/* 应用到Mascot容器 */
.mascot-container {
    animation: breathe var(--duration-breathe) var(--ease-breathe) infinite;
}
```

#### 眨眼动画
```css
@keyframes blink {
    0%, 45%, 55%, 100% {
        transform: scaleY(1);
    }
    50% {
        transform: scaleY(0.1);
    }
}

/* 随机眨眼频率：每3-7秒一次 */
.eye {
    animation: blink 0.15s ease-in-out;
    animation-delay: var(--blink-delay, 0s);
}
```

#### 视线追踪
```css
/* 眼球跟随鼠标 - 使用CSS变量控制 */
.eye-ball {
    transform: translate(
        calc(var(--gaze-x, 0) * 5px),
        calc(var(--gaze-y, 0) * 3px)
    );
    transition: transform 0.1s var(--ease-breathe);
}
```

#### 情绪表达
```css
/* 开心 - 眼睛弯成月牙 */
.eye.happy {
    border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
}

/* 惊讶 - 眼睛变大 */
.eye.surprised {
    transform: scale(1.3);
}

/* 思考 - 眼睛略微向下看 */
.eye.thinking {
    transform: translateY(2px);
}
```

### 5.4 过渡动画

| 元素 | 动画类型 | 时长 | 缓动 |
|------|----------|------|------|
| 按钮悬停 | scale(1.02) + 发光增强 | 200ms | ease-breathe |
| 卡片出现 | opacity + translateY(10px) | 300ms | ease-out |
| 面板滑入 | translateX + opacity | 300ms | ease-spring |
| 状态变化 | 颜色渐变 + 发光 | 500ms | ease-breathe |
| 页面转场 | 淡入淡出 + 轻微缩放 | 400ms | ease-in-out |

### 5.5 微交互

```css
/* 按钮点击反馈 */
.button:active {
    transform: scale(0.96);
    transition: transform 0.1s;
}

/* 输入框聚焦 */
.input:focus {
    border-color: var(--accent-primary);
    box-shadow: 0 0 0 3px var(--accent-glow);
}

/* 滑块拖动 */
.slider:active {
    cursor: grabbing;
}
```

### 5.6 骨架屏加载动画

```css
/* 骨架屏 shimmer 效果 */
@keyframes shimmer {
    0% {
        background-position: -200% 0;
    }
    100% {
        background-position: 200% 0;
    }
}

.skeleton {
    background: linear-gradient(
        90deg,
        var(--bg-secondary) 25%,
        var(--bg-elevated) 50%,
        var(--bg-secondary) 75%
    );
    background-size: 200% 100%;
    animation: shimmer 1.5s infinite;
}

.skeleton-text {
    height: 14px;
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-sm);
}

.skeleton-text:last-child {
    width: 60%;
}
```

### 5.7 Toast 通知动画

```css
/* Toast 容器 */
.toast-container {
    position: fixed;
    top: var(--space-lg);
    right: var(--space-lg);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
}

/* Toast 进入动画 */
@keyframes toast-enter {
    from {
        opacity: 0;
        transform: translateX(100%);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

/* Toast 离开动画 */
@keyframes toast-exit {
    from {
        opacity: 1;
        transform: translateX(0);
    }
    to {
        opacity: 0;
        transform: translateX(100%);
    }
}

.toast {
    animation: toast-enter var(--duration-normal) var(--ease-spring);
}

.toast.exiting {
    animation: toast-exit var(--duration-fast) var(--ease-breathe) forwards;
}

/* Toast 进度条 */
@keyframes toast-progress {
    from { width: 100%; }
    to { width: 0%; }
}

.toast-progress {
    height: 3px;
    background: var(--accent-primary);
    border-radius: 0 0 var(--radius-sm) var(--radius-sm);
    animation: toast-progress 5s linear forwards;
}
```

### 5.8 Modal 弹框动画

```css
/* Modal 遮罩层 */
@keyframes modal-fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
}

/* Modal 内容 */
@keyframes modal-scale-in {
    from {
        opacity: 0;
        transform: scale(0.95) translateY(10px);
    }
    to {
        opacity: 1;
        transform: scale(1) translateY(0);
    }
}

.modal-overlay {
    animation: modal-fade-in var(--duration-normal) var(--ease-breathe);
}

.modal-content {
    animation: modal-scale-in var(--duration-normal) var(--ease-spring);
}

/* Modal 关闭动画 */
@keyframes modal-scale-out {
    from {
        opacity: 1;
        transform: scale(1);
    }
    to {
        opacity: 0;
        transform: scale(0.95);
    }
}

.modal-closing .modal-content {
    animation: modal-scale-out var(--duration-fast) var(--ease-breathe) forwards;
}
```

### 5.9 打字机效果

```css
/* 打字机效果 - 用于对话气泡 */
@keyframes typing {
    from { width: 0; }
    to { width: 100%; }
}

.typing-text {
    overflow: hidden;
    white-space: nowrap;
    animation: typing 2s steps(40) forwards;
}

/* 逐字显示 */
.typing-char {
    display: inline-block;
    opacity: 0;
    animation: char-reveal 0.05s forwards;
}

@keyframes char-reveal {
    to { opacity: 1; }
}

/* 打字光标 */
.typing-cursor::after {
    content: '|';
    animation: cursor-blink 0.8s infinite;
}

@keyframes cursor-blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
}
```

### 5.10 脉冲动画

```css
/* 状态指示脉冲 */
@keyframes pulse {
    0%, 100% {
        opacity: 1;
        transform: scale(1);
    }
    50% {
        opacity: 0.6;
        transform: scale(1.1);
    }
}

.status-pulse {
    animation: pulse 2s ease-in-out infinite;
}

/* 环形脉冲 */
@keyframes ring-pulse {
    0% {
        transform: scale(1);
        opacity: 1;
    }
    100% {
        transform: scale(1.5);
        opacity: 0;
    }
}

.ring::before {
    content: '';
    position: absolute;
    inset: 0;
    border: 2px solid var(--accent-primary);
    border-radius: 50%;
    animation: ring-pulse 1.5s ease-out infinite;
}
```

### 5.11 列表交错动画

```css
/* 列表项依次进入 */
@keyframes list-enter {
    from {
        opacity: 0;
        transform: translateY(20px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.list-item {
    opacity: 0;
    animation: list-enter var(--duration-normal) var(--ease-out) forwards;
}

/* 交错延迟 - 使用 CSS 变量 */
.list-item:nth-child(1) { animation-delay: 0ms; }
.list-item:nth-child(2) { animation-delay: 50ms; }
.list-item:nth-child(3) { animation-delay: 100ms; }
.list-item:nth-child(4) { animation-delay: 150ms; }
.list-item:nth-child(5) { animation-delay: 200ms; }

/* 列表拖拽排序 */
.list-item.dragging {
    opacity: 0.8;
    transform: scale(1.02);
    box-shadow: var(--shadow-lg);
    z-index: 100;
}

.list-item.drop-target {
    border-top: 2px solid var(--accent-primary);
}
```

### 5.12 GPU 加速与性能优化

```css
/* 启用GPU加速 */
.gpu-accelerated {
    transform: translateZ(0);
    will-change: transform, opacity;
    backface-visibility: hidden;
}

/* 动画性能最佳实践 */
.perfect-animation {
    /* 只动画 transform 和 opacity */
    transform: translateX(0);
    opacity: 1;
    transition: transform var(--duration-fast), opacity var(--duration-fast);
}

/* 避免动画的属性（性能差） */
/* width, height, margin, padding, top, left, right, bottom */

/* 低性能模式 - 检测与适配 */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        animation-delay: 0ms !important;
    }
    
    /* 但保留必要的状态指示 */
    .status-indicator {
        animation: none !important;
        opacity: 1 !important;
    }
}
```

### 5.13 JavaScript 动画控制接口

```javascript
/**
 * 动画控制器 - 提供完整的动画生命周期管理
 */
class AnimationController {
    constructor(element) {
        this.element = element;
        this.animations = new Map();
        this.eventListeners = new Map();
    }
    
    /**
     * 播放指定动画
     * @param {string} animationName - 动画名称
     * @param {Array} keyframes - 动画关键帧
     * @param {Object} options - 动画选项
     * @returns {Animation} Web Animation API 对象
     */
    play(animationName, keyframes, options = {}) {
        // 清理已存在的同名动画
        if (this.animations.has(animationName)) {
            this.animations.get(animationName).cancel();
        }
        
        const animation = this.element.animate(keyframes, {
            duration: options.duration || 300,
            easing: options.easing || 'ease',
            iterations: options.iterations || 1,
            fill: options.fill || 'forwards',
            delay: options.delay || 0,
            ...options
        });
        
        // 绑定回调
        if (options.onfinish) {
            animation.onfinish = options.onfinish;
        }
        if (options.oncancel) {
            animation.oncancel = options.oncancel;
        }
        if (options.onerror) {
            animation.onerror = options.onerror;
        }
        
        this.animations.set(animationName, animation);
        
        // 自动清理完成的动画
        if (options.autoCleanup !== false) {
            animation.onfinish = () => {
                this.remove(animationName);
                this.emit('finished', animationName);
            };
        }
        
        return animation;
    }
    
    /**
     * 暂停动画
     */
    pause(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) animation.pause();
        return this;
    }
    
    /**
     * 继续播放
     */
    resume(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) animation.play();
        return this;
    }
    
    /**
     * 重置动画
     */
    reset(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) animation.cancel();
        return this;
    }
    
    /**
     * 完成动画
     */
    finish(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) animation.finish();
        return this;
    }
    
    /**
     * 获取动画状态
     */
    getState(animationName) {
        const animation = this.animations.get(animationName);
        if (!animation) return null;
        return {
            playState: animation.playState,
            currentTime: animation.currentTime,
            startTime: animation.startTime,
            playbackRate: animation.playbackRate
        };
    }
    
    /**
     * 反向播放
     */
    reverse(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) animation.reverse();
        return this;
    }
    
    /**
     * 设置播放速率
     * @param {string} animationName - 动画名称
     * @param {number} rate - 播放速率 (0.5 = 慢放, 2 = 快进)
     */
    setPlaybackRate(animationName, rate) {
        const animation = this.animations.get(animationName);
        if (animation) animation.playbackRate = rate;
        return this;
    }
    
    /**
     * 获取播放速率
     */
    getPlaybackRate(animationName) {
        const animation = this.animations.get(animationName);
        return animation ? animation.playbackRate : null;
    }
    
    /**
     * 暂停所有动画
     */
    pauseAll() {
        this.animations.forEach(anim => anim.pause());
        return this;
    }
    
    /**
     * 恢复所有动画
     */
    resumeAll() {
        this.animations.forEach(anim => anim.play());
        return this;
    }
    
    /**
     * 重置所有动画
     */
    resetAll() {
        this.animations.forEach(anim => anim.cancel());
        this.animations.clear();
        return this;
    }
    
    /**
     * 完成所有动画
     */
    finishAll() {
        this.animations.forEach(anim => anim.finish());
        return this;
    }
    
    /**
     * 检查动画是否存在
     */
    exists(animationName) {
        return this.animations.has(animationName);
    }
    
    /**
     * 移除指定动画
     */
    remove(animationName) {
        const animation = this.animations.get(animationName);
        if (animation) {
            animation.cancel();
            this.animations.delete(animationName);
        }
        return this;
    }
    
    /**
     * 事件发射器
     */
    emit(event, ...args) {
        const listeners = this.eventListeners.get(event);
        if (listeners) {
            listeners.forEach(callback => callback(...args));
        }
        return this;
    }
    
    /**
     * 绑定事件监听
     * @param {string} event - 事件名 (finished, cancelled, paused, resumed)
     * @param {Function} callback - 回调函数
     */
    on(event, callback) {
        if (!this.eventListeners.has(event)) {
            this.eventListeners.set(event, new Set());
        }
        this.eventListeners.get(event).add(callback);
        return this;
    }
    
    /**
     * 移除事件监听
     */
    off(event, callback) {
        const listeners = this.eventListeners.get(event);
        if (listeners) {
            listeners.delete(callback);
        }
        return this;
    }
    
    /**
     * 获取所有动画名称
     */
    getAnimationNames() {
        return Array.from(this.animations.keys());
    }
    
    /**
     * 销毁控制器，清理所有动画
     */
    destroy() {
        this.resetAll();
        this.eventListeners.clear();
    }
}

// 使用示例
const controller = new AnimationController(document.querySelector('.mascot'));

// 基础用法 - 呼吸动画
controller.play('breathe', [
    { transform: 'scale(1)', opacity: 0.9 },
    { transform: 'scale(1.03)', opacity: 1 }
], {
    duration: 2000,
    iterations: Infinity
});

// 带回调的动画
controller.play('fade-out', [
    { opacity: 1 },
    { opacity: 0 }
], {
    duration: 500,
    autoCleanup: true,
    onfinish: () => console.log('淡出完成')
});

// 批量控制
controller.pauseAll();  // 暂停所有
controller.resumeAll(); // 恢复所有

// 事件监听
controller.on('finished', (name) => {
    console.log(`${name} 动画已完成`);
});

// 速度控制
controller.setPlaybackRate('breathe', 0.5); // 慢放
controller.setPlaybackRate('breathe', 2.0);  // 快进

// 条件执行
if (controller.exists('breathe')) {
    controller.reverse('breathe');  // 反向播放
}

// 获取状态
const state = controller.getState('breathe');
console.log(`状态: ${state.playState}, 时间: ${state.currentTime}`);

// 销毁
controller.destroy();
```

### 5.14 移动端手势动画

```css
/* 长按效果 */
.touch-press {
    transform: scale(0.95);
    transition: transform 0.1s;
}

.touch-press:active {
    transform: scale(0.92);
}

/* 滑动删除 */
.swipe-item {
    transition: transform 0.3s var(--ease-breathe);
}

.swipe-item.swiping {
    transition: none;
}

.swipe-item.deleting {
    transform: translateX(-100%);
    opacity: 0;
}

/* 触摸反馈涟漪 */
.ripple-container {
    position: relative;
    overflow: hidden;
}

.ripple {
    position: absolute;
    border-radius: 50%;
    background: rgba(79, 209, 197, 0.3);
    transform: scale(0);
    animation: ripple-effect 0.6s linear;
    pointer-events: none;
}

@keyframes ripple-effect {
    to {
        transform: scale(4);
        opacity: 0;
    }
}

/* 下拉刷新 */
.pull-refresh {
    transition: transform 0.3s var(--ease-bounce);
}

.pull-refresh.refreshing {
    animation: refresh-spin 1s linear infinite;
}

@keyframes refresh-spin {
    to { transform: rotate(360deg); }
}
```

---

## 6. 组件规范

### 6.1 Mascot（云枢本体）

#### 尺寸规范
```css
/* 桌面模式 - 侧边栏常驻 */
.mascot-sidebar {
    width: 80px;           /* 最小尺寸 */
    height: 120px;         /* 最小高度 */
    max-width: 150px;      /* 最大宽度 */
    max-height: 200px;     /* 最大高度 */
}

/* 桌面模式 - 独立窗口 */
.mascot-floating {
    width: 120px;          /* 默认宽度 */
    height: 180px;         /* 默认高度 */
}

/* 移动模式 */
.mascot-mobile {
    width: 60px;
    height: 90px;
}
```

#### 状态视觉
| 状态 | 视觉表现 | 动画 |
|------|----------|------|
| 空闲 | 轻微呼吸动画，眼睛缓慢眨眼 | 3-7秒眨眼 |
| 倾听 | 呼吸加快，眼睛注视用户方向 | 视线追踪 |
| 思考 | 眼睛向上看，身体轻微倾斜 | 眨眼变慢 |
| 说话 | 嘴部动画（如果有），眼睛正常 | 视线稳定 |
| 疲惫 | 眼睛半闭，身体略微下沉 | 眨眼变长 |
| 兴奋 | 眼睛变大发光，身体微微弹跳 | 呼吸加速 |
| 异常 | 眼睛变红，身体轻微颤抖 | 不规则动画 |

### 6.2 对话气泡

```css
/* 用户消息 - 右对齐 */
.message-user {
    background: var(--accent-primary);
    color: var(--bg-deep);
    border-radius: var(--radius-lg) var(--radius-lg) var(--radius-sm) var(--radius-lg);
    padding: var(--space-sm) var(--space-md);
    max-width: 70%;
}

/* 云枢消息 - 左对齐 */
.message-Yunshu {
    background: var(--bg-elevated);
    color: var(--text-primary);
    border-radius: var(--radius-lg) var(--radius-lg) var(--radius-lg) var(--radius-sm);
    border: 1px solid var(--border-subtle);
    padding: var(--space-sm) var(--space-md);
    max-width: 70%;
}

/* 时间戳 */
.message-time {
    font-size: var(--font-micro);
    color: var(--text-muted);
    margin-top: var(--space-xs);
}
```

### 6.3 状态指示器

```css
/* 身体状态指示 - 圆环进度条 */
.status-ring {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: conic-gradient(
        var(--status-color) var(--status-percent),
        var(--bg-secondary) var(--status-percent)
    );
    position: relative;
}

.status-ring::before {
    content: '';
    position: absolute;
    inset: 4px;
    background: var(--bg-primary);
    border-radius: 50%;
}

.status-ring-value {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 600;
}

/* 状态颜色 */
.status-cpu { --status-color: #63b3ed; }
.status-memory { --status-color: #f687b3; }
.status-battery { --status-color: #48bb78; }
.status-temp { --status-color: #ed8936; }
```

### 6.4 工具按钮

```css
.button-tool {
    width: 36px;
    height: 36px;
    border-radius: var(--radius-md);
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all var(--duration-fast) var(--ease-breathe);
}

.button-tool:hover {
    background: var(--bg-elevated);
    border-color: var(--accent-primary);
    color: var(--accent-primary);
    transform: translateY(-2px);
    box-shadow: var(--shadow-glow);
}

.button-tool:active {
    transform: translateY(0) scale(0.95);
}
```

### 6.5 面板和卡片

```css
/* 毛玻璃面板 */
.panel-glass {
    background: var(--bg-glass);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    padding: var(--space-lg);
}

/* 设置卡片 */
.card-settings {
    background: var(--bg-secondary);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: var(--space-md);
    transition: all var(--duration-fast) var(--ease-breathe);
}

.card-settings:hover {
    border-color: var(--accent-primary);
    box-shadow: var(--shadow-glow);
}

/* 分割线 */
.divider {
    height: 1px;
    background: linear-gradient(
        90deg,
        transparent,
        var(--border-subtle) 20%,
        var(--border-subtle) 80%,
        transparent
    );
    margin: var(--space-lg) 0;
}
```

### 6.6 浮动提示

```css
.tooltip {
    background: var(--bg-elevated);
    border: 1px solid var(--border-accent);
    border-radius: var(--radius-sm);
    padding: var(--space-xs) var(--space-sm);
    font-size: var(--font-caption);
    color: var(--text-primary);
    box-shadow: var(--shadow-md);
    opacity: 0;
    transform: translateY(4px);
    transition: all var(--duration-fast) var(--ease-breathe);
    pointer-events: none;
}

.tooltip.visible {
    opacity: 1;
    transform: translateY(0);
}
```

---

## 7. 布局规范

### 7.1 桌面端布局

```
┌──────────────────────────────────────────────────────────────┐
│  ┌─────────┐                              ┌──────────────────┐ │
│  │         │                              │                  │ │
│  │ Mascot  │      Main Content Area      │   Side Panel    │ │
│  │ (固定)  │                              │   (可折叠)      │ │
│  │         │                              │                  │ │
│  └─────────┘                              └──────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │                    Chat Input Area                      │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

#### 布局比例
- **Mascot区域**: 固定宽度 80-150px，高度自适应
- **主内容区**: flex: 1，最小宽度 400px
- **侧边栏**: 固定宽度 300px，可折叠
- **底部输入区**: 固定高度 80px

### 7.2 Mascot 位置选项

```css
/* 左侧固定 */
.mascot-left {
    position: fixed;
    left: var(--space-lg);
    top: 50%;
    transform: translateY(-50%);
}

/* 右下角悬浮 */
.mascot-bottom-right {
    position: fixed;
    right: var(--space-lg);
    bottom: var(--space-lg);
}

/* 顶部中央 */
.mascot-top-center {
    position: fixed;
    top: var(--space-lg);
    left: 50%;
    transform: translateX(-50%);
}
```

### 7.3 响应式断点

```css
/* 断点定义 */
--breakpoint-sm: 640px;   /* 手机横屏 */
--breakpoint-md: 768px;   /* 平板 */
--breakpoint-lg: 1024px;  /* 小屏电脑 */
--breakpoint-xl: 1280px;  /* 标准电脑 */
--breakpoint-2xl: 1536px; /* 大屏 */

@media (max-width: 768px) {
    /* 移动端布局调整 */
    .mascot-sidebar {
        width: 50px;
    }
    
    .side-panel {
        position: fixed;
        right: -300px;
        width: 280px;
    }
}
```

---

## 8. 图标系统

### 8.1 图标风格
采用 **线性 + 渐变** 风格，线条粗细 1.5px，与云枢的"轻盈"气质一致。

### 8.2 核心图标

| 图标名称 | 用途 | 建议 |
|----------|------|------|
| 💬 | 对话/聊天 | 主要交互入口 |
| ⚙️ | 设置 | 系统设置 |
| 📊 | 状态 | 身体状态查看 |
| 🎭 | 人格 | Persona设置 |
| 🧠 | 记忆 | LifeTrace记忆 |
| 🔍 | 搜索 | 记忆搜索 |
| 📝 | 工具 | 工具列表 |
| ❓ | 帮助 | 使用帮助 |

### 8.3 图标动画

```css
.icon-interactive {
    transition: all var(--duration-fast) var(--ease-breathe);
}

.icon-interactive:hover {
    transform: scale(1.1);
    color: var(--accent-primary);
}

.icon-interactive:active {
    transform: scale(0.95);
}
```

---

## 9. 声音与反馈

### 9.1 UI 音效（可选）

```css
/* 轻柔的UI反馈音 - 建议使用 */
.ui-sound-send: url('/assets/sounds/send.mp3');     /* 发送消息 */
.ui-sound-receive: url('/assets/sounds/receive.mp3'); /* 收到消息 */
.ui-sound-hover: url('/assets/sounds/hover.mp3');   /* 悬停反馈 */
.ui-sound-click: url('/assets/sounds/click.mp3');   /* 点击确认 */

/* 音量建议: 10-20% */
.ui-sound-volume: 0.15;
```

### 9.2 云枢专属音效

```css
/* 情绪音效 */
.sound-happy: url('/assets/sounds/Yunshu-happy.mp3');
.sound-thinking: url('/assets/sounds/Yunshu-thinking.mp3');
.sound-tired: url('/assets/sounds/Yunshu-tired.mp3');
.sound-excited: url('/assets/sounds/Yunshu-excited.mp3');
```

---

## 10. 无障碍设计

### 10.1 颜色对比度

```css
/* WCAG AA 标准 */
--contrast-normal: 4.5;  /* 正常文本 */
--contrast-large: 3.0;   /* 大文本/图标 */

/* 确保所有文本符合对比度要求 */
.text-primary {
    color: var(--text-primary);  /* 对比度 > 4.5:1 */
}
```

### 10.2 减少动画

```css
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
}
```

### 10.3 焦点管理

```css
/* 高对比度焦点指示 */
:focus-visible {
    outline: 2px solid var(--accent-primary);
    outline-offset: 2px;
}
```

---

## 11. 技术实现建议

### 11.1 推荐技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 渲染 | Vue 3 + TypeScript | 响应式UI框架 |
| 状态 | Pinia | 全局状态管理 |
| 样式 | UnoCSS + CSS Variables | 原子化 + 主题变量 |
| 动画 | GSAP / CSS Animation | 高性能动画 |
| 3D/2D | Three.js / Live2D | Mascot渲染 |
| 桌面 | Tauri / Electron | 跨平台桌面 |

### 11.2 性能目标

| 指标 | 目标值 |
|------|--------|
| 首屏加载 | < 1.5s |
| 交互响应 | < 100ms |
| 动画帧率 | 60fps |
| 内存占用 | < 200MB |
| CPU占用（空闲） | < 3% |

---

## 12. 设计资源清单

### 12.1 需要的资源

- [ ] 云枢 Mascot 设计稿（正面/侧面/各表情）
- [ ] Mascot 动画文件（呼吸/眨眼/情绪）
- [ ] 图标库（线性风格，SVG格式）
- [ ] 音效文件（UI反馈 + 情绪表达）
- [ ] 字体文件（Noto Sans SC / Inter）

### 12.2 设计工具建议

- **Figma**: 主设计工具，组件库管理
- **After Effects**: 复杂动画设计
- **Lottie**: 动画导出
- **Figma Variables**: 主题切换实现

---

## 附录 A: 快速参考

### 色彩速查
```
主色调: #4fd1c5 (云枢青)
背景深: #0a0a0f
背景主: #12121a
文字主: #e8e8ec
成功色: #48bb78
警告色: #ed8936
错误色: #fc8181
```

### 间距速查
```
xs: 4px | sm: 8px | md: 16px | lg: 24px | xl: 32px
```

### 圆角速查
```
sm: 4px | md: 8px | lg: 16px | xl: 24px | full: 9999px
```

---

**文档版本**: v1.0
**下次更新**: 根据UI开发实际情况调整
**维护者**: 设计团队
