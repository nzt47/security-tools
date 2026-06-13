# 云枢 Mascot 设计规范

## 数字生命的视觉核心

---

## 1. 设计愿景

### 1.1 核心理念
云枢的Mascot是整个数字生命的"灵魂窗口"——它不仅是一个视觉形象，更是用户感知系统状态、人格特质和情感变化的直接界面。

### 1.2 设计关键词
- **灵性**: 眼睛有神，表情丰富
- **呼吸感**: 持续微妙的动画，仿佛活着
- **轻盈**: 透明、半透明元素，不压迫
- **可亲**: 友好、温暖、令人安心

### 1.3 设计参考
- **AIRI Desktop Companion**: 透明窗口、视线追踪
- **Beat Saber**: 动态、响应式反馈
- **Notion AI**: 简洁、智慧感
- **东方美学**: 留白、写意、气韵

---

## 2. 视觉设计

### 2.1 整体形态

```
┌─────────────────────────────────────┐
│                                     │
│         ○   ○   ← 眼睛              │
│          ▽     ← 嘴巴（可选）        │
│                                     │
│      ╭──────────╮                   │
│      │ 灵性光晕  │ ← 光晕效果        │
│      │ (呼吸)   │                   │
│      ╰──────────╯                   │
│                                     │
│         ∿∿∿∿∿   ← 底部装饰波纹      │
│                                     │
└─────────────────────────────────────┘
```

### 2.2 配色方案

```css
/* 主色调 - 云枢青 */
:root {
    --mascot-primary: #4fd1c5;      /* 主色 - 眼睛、光晕 */
    --mascot-secondary: #81e6d9;    /* 次色 - 高光 */
    --mascot-glow: rgba(79, 209, 197, 0.4);  /* 发光 */
    
    /* 情绪色 */
    --mascot-happy: #4fd1c5;        /* 开心 - 青色 */
    --mascot-excited: #f687b3;       /* 兴奋 - 粉色 */
    --mascot-calm: #63b3ed;         /* 平静 - 蓝色 */
    --mascot-tired: #a0aec0;        /* 疲惫 - 灰色 */
    --mascot-thinking: #9f7aea;     /* 思考 - 紫色 */
    --mascot-error: #fc8181;        /* 异常 - 红色 */
}
```

### 2.3 眼睛设计（核心）

眼睛是云枢Mascot最重要的元素，它传达：
- **注意力方向**: 视线追踪用户鼠标
- **情绪状态**: 开心、惊讶、思考、疲惫等
- **活跃程度**: 眨眼频率反映思考状态

#### 2.3.1 基础眼睛形状

```css
/* 基础眼睛 */
.eye {
    width: 20px;
    height: 12px;
    background: var(--mascot-primary);
    border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
    position: relative;
    transition: all var(--duration-fast) var(--ease-breathe);
}

/* 眼球 */
.eye::before {
    content: '';
    position: absolute;
    width: 8px;
    height: 8px;
    background: var(--bg-deep);
    border-radius: 50%;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    transition: transform var(--duration-fast);
}

/* 高光 */
.eye::after {
    content: '';
    position: absolute;
    width: 3px;
    height: 3px;
    background: white;
    border-radius: 50%;
    top: 30%;
    left: 60%;
}
```

#### 2.3.2 眼睛情绪变体

```css
/* 开心 - 弯成月牙 */
.eye.happy {
    height: 6px;
    border-radius: 50% 50% 50% 50% / 100% 100% 0% 0%;
}

/* 惊讶 - 变大 */
.eye.surprised {
    width: 24px;
    height: 16px;
}

/* 思考 - 眯眼向下看 */
.eye.thinking {
    height: 8px;
    transform: translateY(2px);
}

/* 疲惫 - 半闭 */
.eye.tired {
    height: 4px;
    opacity: 0.7;
}

/* 警惕 - 眯眼横线 */
.eye.alert {
    height: 3px;
    width: 24px;
}

/* 异常 - 发红 */
.eye.error {
    background: var(--mascot-error);
    animation: shake 0.5s infinite;
}
```

### 2.4 光晕效果

云枢周围有一个微妙的发光效果，表示它的"生命力"。

```css
.mascot-glow {
    position: absolute;
    inset: -20px;
    background: radial-gradient(
        ellipse at center,
        var(--mascot-glow) 0%,
        transparent 70%
    );
    animation: breathe 3s ease-in-out infinite;
    pointer-events: none;
}

@keyframes breathe {
    0%, 100% {
        opacity: 0.3;
        transform: scale(1);
    }
    50% {
        opacity: 0.6;
        transform: scale(1.1);
    }
}
```

### 2.5 底部波纹

表示云枢的"呼吸"或"存在痕迹"。

```css
.mascot-waves {
    position: absolute;
    bottom: -10px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: 4px;
}

.wave {
    width: 30px;
    height: 4px;
    background: linear-gradient(
        90deg,
        transparent,
        var(--mascot-primary),
        transparent
    );
    border-radius: 2px;
    opacity: 0.3;
}

.wave:nth-child(1) { animation: wave 2s ease-in-out infinite; }
.wave:nth-child(2) { animation: wave 2s ease-in-out 0.2s infinite; }
.wave:nth-child(3) { animation: wave 2s ease-in-out 0.4s infinite; }

@keyframes wave {
    0%, 100% { transform: scaleX(1); opacity: 0.3; }
    50% { transform: scaleX(1.2); opacity: 0.5; }
}
```

---

## 3. 动画系统

### 3.1 基础动画

#### 3.1.1 呼吸动画（持续）

```css
.mascot {
    animation: mascot-breathe 3s ease-in-out infinite;
}

@keyframes mascot-breathe {
    0%, 100% {
        transform: scale(1);
        opacity: 0.9;
    }
    50% {
        transform: scale(1.03);
        opacity: 1;
    }
}
```

#### 3.1.2 眨眼动画（随机）

```css
.eye {
    animation: blink 4s ease-in-out infinite;
    animation-delay: var(--blink-delay, 0s);
}

@keyframes blink {
    0%, 45%, 55%, 100% {
        transform: scaleY(1);
    }
    50% {
        transform: scaleY(0.05);
    }
}

/* 不同眼睛不同步 */
.left-eye { --blink-delay: 0s; }
.right-eye { --blink-delay: 0.1s; }
```

#### 3.1.3 视线追踪

```javascript
// JavaScript 视线追踪逻辑
document.addEventListener('mousemove', (e) => {
    const mascot = document.querySelector('.mascot');
    const eye = mascot.querySelector('.eye-ball');
    
    // 计算鼠标相对于眼睛中心的方向
    const rect = mascot.getBoundingClientRect();
    const eyeCenterX = rect.left + rect.width / 2;
    const eyeCenterY = rect.top + rect.height / 3;
    
    const dx = e.clientX - eyeCenterX;
    const dy = e.clientY - eyeCenterY;
    
    // 归一化到 -1 到 1
    const normalizedX = Math.max(-1, Math.min(1, dx / 100));
    const normalizedY = Math.max(-1, Math.min(1, dy / 100));
    
    // 应用到 CSS 变量
    eye.style.setProperty('--gaze-x', normalizedX);
    eye.style.setProperty('--gaze-y', normalizedY);
});
```

```css
.eye-ball {
    transform: translate(
        calc(var(--gaze-x, 0) * 5px),
        calc(var(--gaze-y, 0) * 3px)
    );
}
```

### 3.2 情绪动画

#### 3.2.1 开心

```css
.mascot.happy {
    animation: happy-bounce 0.5s ease-in-out;
}

@keyframes happy-bounce {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-10px); }
}

.eye.happy {
    height: 6px;
    border-radius: 50% 50% 50% 50% / 100% 100% 0% 0%;
    animation: happy-eye 0.3s ease-in-out;
}
```

#### 3.2.2 思考

```css
.mascot.thinking {
    animation: thinking-tilt 2s ease-in-out infinite;
}

@keyframes thinking-tilt {
    0%, 100% { transform: rotate(0deg); }
    30% { transform: rotate(-3deg); }
    60% { transform: rotate(3deg); }
}

.eye.thinking {
    height: 8px;
    animation: thinking-blink 3s ease-in-out infinite;
}
```

#### 3.2.3 疲惫

```css
.mascot.tired {
    animation: tired-droop 3s ease-in-out infinite;
}

@keyframes tired-droop {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(5px); }
}

.eye.tired {
    height: 3px;
    opacity: 0.5;
    animation: tired-heavy 4s ease-in-out infinite;
}
```

#### 3.2.4 异常

```css
.mascot.error {
    animation: error-shake 0.3s ease-in-out infinite;
}

@keyframes error-shake {
    0%, 100% { transform: translateX(0); }
    25% { transform: translateX(-3px); }
    75% { transform: translateX(3px); }
}

.eye.error {
    background: var(--mascot-error);
}
```

### 3.3 交互动画

#### 3.3.1 悬停效果

```css
.mascot:hover {
    transform: scale(1.05);
    transition: transform var(--duration-fast) var(--ease-bounce);
}

.mascot:hover .mascot-glow {
    opacity: 0.8;
    transform: scale(1.2);
}
```

#### 3.3.2 点击效果

```css
.mascot:active {
    transform: scale(0.95);
}
```

---

## 4. 布局与尺寸

### 4.1 尺寸规格

```css
/* 极小 - 用于嵌入其他组件 */
.mascot-xs {
    width: 40px;
    height: 60px;
}
.mascot-xs .eye { width: 8px; height: 5px; }

/* 小 - 侧边栏 */
.mascot-sm {
    width: 60px;
    height: 90px;
}
.mascot-sm .eye { width: 12px; height: 7px; }

/* 中 - 独立窗口（默认）*/
.mascot-md {
    width: 100px;
    height: 150px;
}
.mascot-md .eye { width: 18px; height: 10px; }

/* 大 - 全屏展示 */
.mascot-lg {
    width: 150px;
    height: 225px;
}
.mascot-lg .eye { width: 24px; height: 14px; }

/* 特大 - 特殊场景 */
.mascot-xl {
    width: 200px;
    height: 300px;
}
.mascot-xl .eye { width: 32px; height: 18px; }
```

### 4.2 定位模式

```css
/* 悬浮 - 右下角 */
.mascot-floating {
    position: fixed;
    right: 24px;
    bottom: 24px;
    z-index: 999;
}

/* 吸附 - 左侧 */
.mascot-docked-left {
    position: fixed;
    left: 24px;
    top: 50%;
    transform: translateY(-50%);
    z-index: 999;
}

/* 吸附 - 右侧 */
.mascot-docked-right {
    position: fixed;
    right: 24px;
    top: 50%;
    transform: translateY(-50%);
    z-index: 999;
}

/* 居中 - 全屏模式 */
.mascot-centered {
    position: fixed;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    z-index: 999;
}
```

---

## 5. 状态系统

### 5.1 状态映射

| 系统状态 | Mascot表情 | 动画 | 颜色 |
|----------|------------|------|------|
| 空闲 | 平静眨眼 | 3-5秒眨眼 | 云枢青 |
| 倾听 | 专注看用户 | 视线追踪 | 云枢青 |
| 思考 | 眼睛向上 | 眨眼变慢 | 云枢青 |
| 说话 | 开心眨眼 | 较快眨眼 | 云枢青 |
| 处理任务 | 专注 | 无眨眼加速 | 云枢青 |
| 低电量 | 疲惫半闭 | 眨眼变慢 | 灰色 |
| 过热 | 警惕 | 眨眼加快 | 橙色 |
| 异常 | 警惕发红 | 颤抖 | 红色 |
| 兴奋 | 大眼发光 | 弹跳 | 粉色 |

### 5.2 状态切换

```javascript
class MascotStateManager {
    constructor() {
        this.currentState = 'idle';
        this.mascotEl = document.querySelector('.mascot');
    }
    
    setState(newState) {
        // 移除旧状态
        this.mascotEl.classList.remove(this.currentState);
        
        // 添加新状态
        this.currentState = newState;
        this.mascotEl.classList.add(newState);
        
        // 触发状态变化事件
        this.onStateChange(newState);
    }
    
    onStateChange(state) {
        switch(state) {
            case 'happy':
                this.playAnimation('happy-bounce');
                break;
            case 'thinking':
                this.playAnimation('thinking-tilt');
                break;
            case 'tired':
                this.playAnimation('tired-droop');
                break;
            case 'error':
                this.playAnimation('error-shake');
                break;
        }
    }
}
```

---

## 6. 技术实现

### 6.1 CSS 架构

```css
/* Mascot 核心 */
.mascot {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: pointer;
}

/* 眼睛容器 */
.mascot-eyes {
    display: flex;
    gap: 16px;
    margin-bottom: 8px;
}

/* 单个眼睛 */
.eye {
    position: relative;
    /* ... */
}

/* 光晕 */
.mascot-glow {
    position: absolute;
    inset: -20px;
    /* ... */
}

/* 波纹 */
.mascot-waves {
    position: absolute;
    bottom: -10px;
    /* ... */
}

/* 状态变体 */
.mascot.happy { /* ... */ }
.mascot.thinking { /* ... */ }
.mascot.tired { /* ... */ }
.mascot.error { /* ... */ }
```

### 6.2 JavaScript API

```javascript
class YunshuMascot {
    constructor(options = {}) {
        this.element = options.element;
        this.size = options.size || 'md';
        this.position = options.position || 'floating';
        
        this.stateManager = new MascotStateManager(this);
        this.eyeTracker = new EyeTracker(this);
        this.animationController = new AnimationController(this);
        
        this.init();
    }
    
    init() {
        this.render();
        this.attachEvents();
        this.startAnimations();
    }
    
    // 状态控制
    setMood(mood) {
        this.stateManager.setState(mood);
    }
    
    // 表情控制
    setExpression(expression) {
        this.element.querySelectorAll('.eye').forEach(eye => {
            eye.className = `eye ${expression}`;
        });
    }
    
    // 视线追踪
    trackGaze(x, y) {
        this.eyeTracker.update(x, y);
    }
    
    // 交互
    onClick(callback) {
        this.element.addEventListener('click', callback);
    }
}
```

### 6.3 Vue 组件示例

```vue
<template>
  <div 
    class="mascot"
    :class="[sizeClass, mood]"
    @click="handleClick"
  >
    <div class="mascot-glow" />
    
    <div class="mascot-eyes">
      <div 
        class="eye left-eye" 
        :class="expression"
        :style="eyeStyle"
      />
      <div 
        class="eye right-eye" 
        :class="expression"
        :style="eyeStyle"
      />
    </div>
    
    <div class="mascot-waves">
      <div class="wave" />
      <div class="wave" />
      <div class="wave" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  size: { type: String, default: 'md' },
  mood: { type: String, default: 'idle' }
});

const emit = defineEmits(['click']);

const gazeX = ref(0);
const gazeY = ref(0);

const sizeClass = computed(() => `mascot-${props.size}`);

const expression = computed(() => {
  const map = {
    idle: '',
    happy: 'happy',
    thinking: 'thinking',
    tired: 'tired',
    error: 'alert'
  };
  return map[props.mood] || '';
});

const eyeStyle = computed(() => ({
  '--gaze-x': gazeX.value,
  '--gaze-y': gazeY.value
}));

const handleMouseMove = (e) => {
  // 计算视线方向
  const rect = document.querySelector('.mascot').getBoundingClientRect();
  gazeX.value = (e.clientX - rect.left) / rect.width - 0.5;
  gazeY.value = (e.clientY - rect.top) / rect.height - 0.5;
};

const handleClick = () => {
  emit('click');
};

onMounted(() => {
  document.addEventListener('mousemove', handleMouseMove);
});

onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove);
});
</script>
```

---

## 7. 资源清单

### 7.1 需要的资源

- [ ] SVG 矢量眼睛设计（多种表情）
- [ ] 动画关键帧设计
- [ ] 光晕纹理 PNG
- [ ] 波纹动画序列

### 7.2 设计工具建议

- **Figma**: 主设计工具
- **After Effects**: 动画设计
- **Lottie**: 动画导出
- **Spine**: 2D骨骼动画

---

## 8. 质量标准

### 8.1 动画标准

| 指标 | 目标值 |
|------|--------|
| 帧率 | 60fps |
| 眨眼周期 | 3-7秒随机 |
| 状态切换时间 | < 300ms |
| 交互响应时间 | < 100ms |

### 8.2 性能标准

| 指标 | 目标值 |
|------|--------|
| 渲染占用 | < 5% CPU |
| 内存占用 | < 50MB |
| 动画性能 | GPU 加速 |

---

**文档版本**: v1.0
**设计团队**: 云枢设计组
**最后更新**: 2026-05-30
