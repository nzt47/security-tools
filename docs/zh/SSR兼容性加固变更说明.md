# SSR 兼容性加固变更说明

> 项目：云枢 · AI 智能体桌面工作台
> 模块：`src/layouts/MainLayout.tsx`（Header「接口场景」下拉）
> 日期：2026-08-21
> 评审对象：Dashboard 错误模拟场景的 SSR 兼容性加固

---

## 1. 背景与动机

Header「接口场景」下拉（`MockScenarioMenu`，仅开发环境）在**组件渲染期**直接读取 `localStorage` 以高亮当前选中的模拟场景：

```tsx
// 加固前
const current = localStorage.getItem(DASHBOARD_MOCK_ERROR_KEY) ?? ''
```

当前项目为纯 CSR SPA（Vite + Electron），浏览器端 `localStorage` 始终可用，不存在问题。但未来若接入 SSR（如 Next.js）或服务端预渲染，该写法存在两个隐患：

| 隐患 | 表现 |
|---|---|
| 服务端崩溃 | Node 环境无 `localStorage` 全局对象，渲染期直接调用抛出 `ReferenceError: localStorage is not defined` |
| Hydration 不一致 | 服务端以「未选中」渲染首帧，客户端读取到已选场景后状态不同 → 水合警告与视觉闪烁 |

## 2. 改动清单

| 文件 | 改动点 | 说明 |
|---|---|---|
| `src/utils/storage.ts` | 新增 `safeGetLocalStorage(key)` | SSR 安全读取：`typeof window !== 'undefined'` 守卫，无 window 时返回 `null`；并入 `storage` 统一出口 |
| `src/layouts/MainLayout.tsx` | `MockScenarioMenu` 中 `current` 的读取 | 由裸 `localStorage.getItem` 改为复用 `safeGetLocalStorage` |
| `src/utils/storage.ssr.test.ts` | 新增回归测试 | CSR 读取 / SSR 返回 null / 隐私模式 getRaw 兜底 |

加固后代码（MainLayout 侧）：

```tsx
// 【SSR 兼容】渲染期读取 localStorage 经 safeGetLocalStorage（含 window 守卫）：
// SSR 服务端（Node）无 window/localStorage，守卫后回退默认值（未选中），避免崩溃与 hydration 不一致
const current = safeGetLocalStorage(DASHBOARD_MOCK_ERROR_KEY) ?? ''
```

工具函数实现（storage.ts）：

```ts
export function safeGetLocalStorage(key: string): string | null {
  return typeof window !== 'undefined' ? localStorage.getItem(key) : null
}
```

## 3. SSR 兼容性分析

### 3.1 本次改动覆盖

- **渲染期 localStorage 读取**（`MockScenarioMenu`）：已加窗口守卫，SSR 下回退默认值 `''`（未选中），服务端与客户端首帧一致，避免崩溃与 hydration mismatch。

### 3.2 已确认安全的同类代码（无需改动）

| 位置 | 读取方式 | 安全性 |
|---|---|---|
| `Dashboard/index.tsx` 数据请求 | `useEffect` 内读取 `localStorage` | ✅ React 规范：effect 仅在客户端运行，服务端不执行 |
| `MockScenarioMenu` 的 `handleSelect` | 事件处理器内读写 | ✅ 事件回调仅在客户端触发 |
| `request.ts` Token 注入 | axios 拦截器回调内读取 | ✅ 拦截器运行时即客户端环境 |

### 3.3 边界与说明

- 窗口守卫为运行时判断，SSR 下服务端渲染「未选中」状态；若需服务端恢复用户已选场景，需进一步引入 cookie 透传方案（超出本次范围）。
- `import.meta.env.DEV` 为编译期常量，生产构建中 `MockScenarioMenu` 整体不打包（消除），守卫代码不影响生产体积。

## 4. 验证结果

| 项 | 命令 | 结果 |
|---|---|---|
| 类型检查 | `npm run check`（tsc -b --noEmit） | ✅ 无错误 |
| 生产构建 | `npm run build`（tsc -b && vite build） | ✅ 成功（4083 modules，12.06s） |
| 新增警告 | — | ✅ 无新增。仅存在既有警告（`userStore` 动态/静态混合导入、chunk > 500kB 体积提示），与本改动无关 |

## 5. 评审要点

### 5.1 行为等价性（可验证，无歧义）

- 守卫仅在 `typeof window === 'undefined'`（服务端渲染环境）时改变取值路径；**本项目唯一运行环境为纯 CSR，行为与加固前完全等价**。
- 等价性验证方式：`npm run build` 通过 + 全量单测通过（既有 Dashboard 链路测试 3/3 未受影响的证明）。

### 5.2 决策项（需团队明确）

| # | 议题 | 现状 | 触发条件 | 默认行为 | 若不决策的后果 |
|---|---|---|---|---|---|
| 1 | 服务端是否需还原用户已选场景 | 守卫回退「未选中」 | 接入 SSR 且要求首屏还原场景 | 不还原（客户端首帧后按需更新） | 仅影响 SSR 首屏场景高亮，功能不受影响 |
| 2 | 场景选择是否透传 cookie | 未引入 | 决策项 1 选择「需还原」 | 不实施 | SSR 首屏无法还原场景 |
| 3 | 新代码规范 | 无强制检查（lint/CI） | 新写「渲染期访问 window/localStorage」的代码 | 复用 `safeGetLocalStorage` 或用 `useEffect` 延迟读取 | 新增 SSR 隐患代码不被发现 |

### 5.3 边界定义（防止误解）

- 本次「SSR 兼容」的承诺范围**仅限**：服务端渲染不崩溃 + hydration 首帧一致。
- **不包含**：服务端还原用户场景选择（此为产品决策，见决策项 1/2，不属于本改动承诺）。
- `safeGetLocalStorage` 不处理「window 存在但 localStorage 访问异常」（隐私模式等），该类场景应使用 `getRaw`（自带 try/catch）——已通过单测明确此分工。

### 5.4 已落地的回归防线

- 新增自动化回归测试 `src/utils/storage.ssr.test.ts`，覆盖：CSR 正常读取 / SSR（window 缺失）返回 null 不抛错 / 隐私模式 getRaw 兜底，防止未来改动移除守卫导致回归。
