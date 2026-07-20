# 云枢前端运行时可见性 — DevConsole & StateInspector

为云枢前端（`yunshu-ui/`）构建的运行时可观测性能力，消除前端"状态黑盒"与"网络请求黑盒"，实现组件网络请求、状态变化、渲染耗时的内联实时反馈。

## 一、功能特性

### DevConsole 调试浮层
- **网络请求面板**：实时展示当前页所有 fetch/XHR 请求（URL、method、status、duration、trace_id）
- **错误堆栈面板**：捕获未处理异常（`window.onerror`）与 Promise rejection（`unhandledrejection`），展示堆栈与关联 trace_id
- **性能面板**：展示组件渲染耗时，分级标识（< 16ms 绿 / 16-100ms 黄 / > 100ms 红）
- **浮层交互**：右上角可拖动 🐛 图标唤起/收起，Tab 切换，清空/暂停/过滤
- **容量控制**：单类记录最多保留 200 条（LRU 淘汰），单条渲染 < 16ms

### StateInspector 状态调试面板
- **状态快照**：展示 `localStorage` / `sessionStorage` / 内存状态的实时快照
- **缓存倒计时**：展示 `staleTime` 过期倒计时，归零时提示"将触发重新获取"（对齐文章要求）
- **重试次数**：展示请求队列重试次数（对齐"重试次数可见"要求）
- **变更时间线**：状态变化 diff 视图，高亮 added / removed / updated 字段

## 二、环境配置

### 环境变量

可观测性通过环境变量控制，配置文件位于 `yunshu-ui/`：

| 文件 | 用途 | `VITE_OBSERVABILITY_ENABLED` |
|------|------|------------------------------|
| `.env.development` | 开发环境 | `true` |
| `.env.production` | 生产环境 | `false` |

完整配置项见 [`src/config/observability.ts`](file:///c:/Users/Administrator/agent/yunshu-ui/src/config/observability.ts)：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `VITE_OBSERVABILITY_ENABLED` | 总开关 | 回退到 Vite `DEV` 标志 |
| `VITE_OBS_MAX_RECORDS` | 单面板最大记录数 | `200` |
| `VITE_OBS_SAMPLING_RATE` | 采样率 0~1 | `1`（全量） |

### 生产环境零损耗（tree-shaking）

可观测性浮层在 [`src/main.tsx`](file:///c:/Users/Administrator/agent/yunshu-ui/src/main.tsx) 中通过**条件动态 import** 加载：

```ts
if (import.meta.env.DEV && import.meta.env.VITE_OBSERVABILITY_ENABLED === 'true') {
  import('@/components/ObservabilityDevtools').then(({ default: Obs }) => { /* ... */ });
}
```

生产构建时 `import.meta.env.DEV === false`，整个 `if` 块被 Vite 静态分析移除，动态 import 的 chunk 不会生成，**生产产物中不含任何 DevConsole 代码**。

验证方式：

```bash
cd yunshu-ui
npm run build
# 检查 dist/assets/ 中是否含 ObservabilityDevtools / DevConsole / requestInterceptor 代码
grep -l "DevConsole\|requestInterceptor" dist/assets/*.js || echo "✓ 生产产物不含可观测性代码"
```

## 三、使用方式

### 1. DevConsole 浮层（开发环境自动启用）

启动开发服务器后，页面右上角会出现 🐛 图标：
- **点击**：展开/收起浮层
- **拖动**：移动图标位置
- **Tab 切换**：网络 / 错误 / 性能 / 状态
- **trace_id**：点击 trace_id 单元格可复制，用于关联后端链路

```bash
cd yunshu-ui
npm run dev
```

### 2. StateInspector 接入（业务组件 opt-in）

业务组件通过 `useObservableState` Hook 接入状态观测：

```tsx
import { useObservableState } from '@/components/StateInspector';

function ChatInput() {
  // 状态同步机制：setValue 时旁路上报快照到 StateInspector，不影响业务渲染
  const [input, setInput] = useObservableState('chatInput', '', {
    source: 'memory',
    traceId: currentTraceId,       // 关联后端 trace_id
    expiresAt: Date.now() + 5000,  // 5 秒后过期，倒计时归零提示重新获取
  });

  return <input value={input} onChange={(e) => setInput(e.target.value)} />;
}
```

### 3. 性能埋点

```tsx
import { trackPerf, measureRender } from '@/components/DevConsole';

// 方式一：手动上报
trackPerf('messageParse', 12.5, { msgLen: 1024 });

// 方式二：测量同步函数耗时
const result = measureRender('computeReply', () => expensiveCompute());
```

## 四、架构设计

### 数据流

```
fetch/XHR ──劫持──▶ requestInterceptor ──事件总线──▶ DevConsole store ──▶ NetworkPanel
window.onerror ──监听──▶ requestInterceptor ──事件总线──▶ DevConsole store ──▶ ErrorPanel
useObservableState ──setValue──▶ StateInspector store ──▶ StateInspector 面板
trackPerf/measureRender ──▶ DevConsole store ──▶ PerformancePanel
```

### 状态同步机制

| 机制 | 应用位置 | 说明 |
|------|----------|------|
| 旁路采集 | requestInterceptor | 劫持 fetch/XHR 仅读取不改写请求/响应，保证业务请求语义不变 |
| LRU 淘汰 | DevConsole/StateInspector store | 超过 maxRecords 时移除最旧记录，内存占用有上限 |
| 事件总线 | requestInterceptor → store | 发布订阅解耦，单个监听器失败不影响其他 |
| 错误边界 | DevConsole 浮层 | ErrorBoundary 兜底，面板渲染异常不影响业务页面 |
| 环境隔离 | main.tsx + observability.ts | 双重守卫（DEV 标志 + 环境变量），生产零损耗 |

### trace_id 关联

后端通过 [`tracing_middleware.py`](file:///c:/Users/Administrator/agent/agent/server_routes/tracing_middleware.py) 在响应头注入 `traceparent`（W3C 格式 `00-{trace_id}-{span_id}-{flags}`）。前端 [`requestInterceptor.ts`](file:///c:/Users/Administrator/agent/yunshu-ui/src/utils/requestInterceptor.ts) 解析该头，提取 trace_id 并关联到网络记录与错误记录，实现前后端链路串联。

## 五、API 参考

### `useObservableState<T>(key, initialValue, options?)`

| 参数 | 类型 | 说明 |
|------|------|------|
| `key` | `string` | 状态键名（唯一标识） |
| `initialValue` | `T` | 初始值 |
| `options.expiresAt` | `number` | 过期时间戳（毫秒），0 表示不过期 |
| `options.retryCount` | `number` | 初始重试次数 |
| `options.traceId` | `string \| null` | 关联 trace_id |
| `options.source` | `'localStorage' \| 'sessionStorage' \| 'memory'` | 状态来源 |
| `options.enableDiff` | `boolean` | 是否记录 diff（默认 true） |

**返回**：`[T, (value: T | ((prev: T) => T)) => void]`

### `trackPerf(name, duration, detail?)`

上报一条性能指标。单次埋点耗时 < 1ms，失败不影响主流程。

### `measureRender<T>(name, fn)`

测量同步函数耗时并上报，返回 fn 的返回值。

### `installInterceptors() / uninstallInterceptors()`

安装/卸载 fetch/XHR/错误捕获拦截器。通常由 `ObservabilityDevtools` 自动调用，无需手动调用。

## 六、测试

```bash
cd yunshu-ui
npm test              # 运行全部测试
npm run test:coverage # 生成覆盖率报告
```

测试覆盖：
- `observability.test.ts`：配置解析、校验、回退、采样
- `requestInterceptor.test.ts`：fetch/XHR 劫持、trace_id 解析、错误捕获、事件总线
- `DevConsole/store.test.ts`：LRU 淘汰、暂停、清空、install
- `DevConsole/shared.test.ts`：时间/耗时格式化、badge 分类、复制
- `StateInspector/store.test.ts`：快照 upsert、重试递增、diff 计算
- `useObservableState.test.ts`：初始注册、setValue 上报、函数式更新

覆盖率门槛：行/函数/语句 ≥ 80%，分支 ≥ 70%。

## 七、故障排查

| 问题 | 排查方向 |
|------|----------|
| 浮层未出现 | 检查 `.env.development` 中 `VITE_OBSERVABILITY_ENABLED=true`；确认 `npm run dev` 而非 `vite build` |
| 网络面板无记录 | 确认请求非跨域（CORS 场景 `traceparent` 头不可见）；检查是否暂停采集 |
| trace_id 为空 | 后端未注入 `traceparent` 头；或请求被 `_excluded_paths` 排除（见 `tracing_middleware.py`） |
| 生产包含 DevConsole | 确认 `.env.production` 中 `VITE_OBSERVABILITY_ENABLED=false`；检查 `main.tsx` 条件 import |
| 面板渲染异常 | 查看控制台 `[DevConsole] 面板渲染异常` 日志，ErrorBoundary 已兜底不影响业务 |

## 八、文件清单

```
yunshu-ui/src/
├── config/
│   └── observability.ts              # 可观测性配置
├── utils/
│   └── requestInterceptor.ts         # fetch/XHR 劫持 + 错误捕获
├── components/
│   ├── DevConsole/
│   │   ├── types.ts                  # 共享类型
│   │   ├── store.ts                  # zustand store + LRU + 性能采集
│   │   ├── shared.ts                 # 工具函数（复制/格式化）
│   │   ├── NetworkPanel.tsx          # 网络请求面板
│   │   ├── ErrorPanel.tsx            # 错误堆栈面板
│   │   ├── PerformancePanel.tsx      # 性能面板
│   │   ├── DevConsole.tsx            # 浮层容器（FAB + Tab + Portal）
│   │   ├── DevConsole.css            # 暗色主题样式
│   │   └── index.ts                  # 模块导出
│   ├── StateInspector/
│   │   ├── types.ts                  # 类型定义
│   │   ├── store.ts                  # 快照 store + diff 计算
│   │   ├── hooks/
│   │   │   └── useObservableState.ts # opt-in Hook
│   │   ├── StateInspector.tsx        # 状态面板（快照/时间线）
│   │   └── index.ts                  # 模块导出
│   └── ObservabilityDevtools/
│       └── index.tsx                 # 统一浮层入口
├── vite-env.d.ts                     # 环境变量类型声明
├── .env.development                  # 开发环境变量
├── .env.production.example           # 生产环境变量模板（复制为 .env.production 使用）
├── vitest.config.ts                  # 测试配置
└── src/test/setup.ts                 # 测试 setup
```
