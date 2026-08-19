# 云枢前端开发规范（Frontend Rules）

> 适用范围：`yunshu-ui/` 下所有 React 页面与组件。硬性约定，代码评审以此为准。

## 1. 设计 Token（唯一样式源）

- 所有**颜色、间距、圆角、阴影**必须取自 Tailwind 主题变量（定义于 `yunshu-ui/tailwind.config.js`、`yunshu-ui/tailwind.preset.cjs`、`yunshu-ui/src/index.css`）。
- 语义色清单（深浅双模式由 `:root` / `.dark` 自动切换）：
  - 底色：`bg-background`、`bg-card`、`bg-muted`
  - 文本：`text-foreground`、`text-card-foreground`、`text-muted-foreground`
  - 主色：`bg-primary`、`text-primary-foreground`、`ring-primary/40` 等
  - 危险：`bg-danger`、`border-danger`、`text-danger`
  - 描边：`border-border`
- **严禁硬编码色值**：`#ffffff`、`bg-white`、`bg-blue-300`、`text-gray-500` 等一律禁止。
  唯一例外：仅允许出现在 `tailwind.config.js`、`tailwind.preset.cjs`、`src/index.css` 的 Token 定义中。
- 圆角统一两种：控件 `rounded-md`（6px）、容器/卡片 `rounded-lg`（8px）。
- 阴影统一两种：常态 `shadow-card`、悬停抬升 `shadow-card-hover`。禁止自造散点阴影。

## 2. 基础组件（唯一 UI 出口）

所有页面**禁止直接写原生 `<button>`、`<input>`、卡片容器**，必须从 `@/components/ui` 导入：

- `<Button />`：`variant = primary | default | danger | ghost`，`size = sm | md`，支持 `loading`。
- `<Input />`：支持 `label`、`error`、`loading`（等待态右侧 spinner）。
- `<Card />`：统一卡片容器（`rounded-lg` + `shadow-card`）。
- `<ThemeToggle />`：深浅模式切换（持久化键 `localStorage['yunshu-theme']`）。

基础组件 API 变更须向后兼容；新增基础组件先评审再进 `ui/`。

## 3. 组件复用规则

- 同类 UI 结构（如「标题 + 说明 + 操作按钮组」「表格行 + 操作」）在代码中出现**超过 2 次**时，必须优先抽象为可复用组件。
- 抽象位置：业务级组件放 `yunshu-ui/src/components/`，通用基础组件放 `yunshu-ui/src/components/ui/`（经 `index.ts` 统一导出）。
- 组件类名合并统一使用 `cn()`（`yunshu-ui/src/lib/cn.ts`），禁止用模板字符串拼接 className。

## 4. 深浅双模式

- 深浅切换 = 在 `<html>` 上增删 `class="dark"`；组件内不得写死明暗样式，一律走语义 Token。
- 主题持久化键唯一：`localStorage['yunshu-theme']`，取值 `'light' | 'dark'`，默认深色。
- **禁止引入其他 theme 键**（如裸 `theme`）造成双写冲突；跨窗口同步监听（storage 事件）只认 `yunshu-theme`。

## 5. 异常处理与鉴权规范（401 / 登出）

- **401 统一由响应拦截器处理**（`yunshu-ui/src/utils/request.ts`）：清除凭证（userStore.logout → clearToken 清 localStorage + hash 跳 `#/login` + Toast）。页面禁止各自处理 401 或重复跳转。
- **凭证清除唯一出口**：一律调用 `request.ts` 的 `clearToken()` / `userStore.logout()`；禁止页面直接 `localStorage.removeItem('token')`（避免 token key 常量引用不一致，曾因此出现 `TOKEN_KEY` 未定义）。
- **禁止 userStore ↔ request 静态循环依赖**：request 中访问 userStore 必须用动态 `import('@/store/userStore')`，仅在拦截器回调运行时加载。
- **鉴权日志埋点**：401 触发登出时记录凭证（脱敏：前 8 + … + 后 4）与调用堆栈，格式 `[auth] 401 触发 logout：token=...`，便于排查会话失效来源。

## 6. 禁止事项

- 禁止引入重型 UI 库（Ant Design、Element Plus 等）。
- 禁止新增全局 CSS 类直接控制颜色（布局/动画类除外，如既有 `glass-panel`）。
- 样式需求未被 Token 覆盖时，先扩展 Token 层，禁止绕过 Token 直接写死样式。
