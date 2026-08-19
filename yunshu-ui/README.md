# 云枢（CloudHub）AI 智能体工作台

> Electron + React + TypeScript + Vite 桌面工作台。Mosaic 多面板布局、SSE 流式对话、面板可拖拽为独立系统窗口并跨窗口实时同步。

## 技术栈

- **UI**：React 18 + TypeScript + Vite 6 + Tailwind CSS
- **布局**：react-mosaic-component（四面板：导航 / 对话 / 思考过程 / 代码编辑器）
- **状态**：Zustand 5（`useLayoutStore`，跨窗口经 Electron 主进程事件总线广播）
- **桌面壳**：Electron 43（contextIsolation + 白名单 IPC；面板 detach → 独立 BrowserWindow）
- **后端**：Flask SSE 服务（`POST /api/chat/stream`）

## 开发

```bash
npm install
npm run dev            # Web 模式（Vite dev server，proxy /api → 127.0.0.1:5678）
npm run dev:electron   # Electron 桌面模式（ELECTRON=1）
npm run check          # 类型检查（renderer）
npm run check:electron # 类型检查（electron 主进程）
npm test               # Vitest 单元测试
```

## 打包发布

```bash
npm run dist:electron  # 构建 + electron-builder 产出 NSIS 安装包（release/）
```

electron-builder 配置（`package.json` → `build`）：

- `files` 白名单 `dist/**`、`dist-electron/**`、`package.json`，并显式排除 `!node_modules/**`（依赖已由 Vite 打进 bundle，运行时不需要）；
- `build.sourcemap: false`（桌面版无线上排障需求，关闭避免 .map 进 asar）；
- `electronLanguages: ["zh-CN", "en-US"]`（只保留中文/英文语言包，剔除其余 locales）；
- 安装包目标：Windows NSIS（x64）。

## 性能优化（实测数据，2026-08-16）

打包体积优化前后实测（优化项：排除 node_modules + 关闭 sourcemap + 精简语言包）：

| 指标 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| 安装包 `云枢 Setup 0.1.0.exe` | 98.6MB | **91.5MB** | NSIS LZMA 压缩后 |
| `resources/app.asar` | 64.9MB | **1.06MB**（-98%） | node_modules（~60MB）全剔除 |
| locales 语言包 | 全量 | 2 个（zh-CN / en-US） | ~1.1MB |
| win-unpacked（解压目录） | — | 302.9MB | 其中 Chromium 运行时 ~300.7MB（固定开销） |

**结构结论**：安装包 91.5MB 中约 **85% 是 Electron/Chromium 运行时**（300.7MB 压缩后 ~88MB），应用代码（app.asar 1.06MB）占比可忽略。体积优化空间已基本耗尽，后续如追求更小安装包需从运行时侧入手（Electron 二进制裁剪、nsis 压缩档位、增量更新等，详见《Electron打包体积优化建议.md》）。

**回归验证**：优化后安装版实测通过——preload 注入（electronAPI 5 API）、面板 detach 独立窗口、file:// 下 SSE 流式对话均正常（详见《SSE流式断流乱序排查指南》9.3.3）。

## Toast 组件（src/components/Toaster.tsx）

全局提示组件：右上角淡入淡出、3 秒自动消失、Tailwind 样式（success=绿 / error=红 / info=蓝），在 `main.tsx` 全局挂载一次。axios 拦截器（`src/utils/request.ts`）的错误提示统一走 `toast.error/info`，业务代码也可直接调用。

```ts
import { toast } from '@/components/Toaster'
toast.success('保存成功') // 或 toast.error(...) / toast.info(...)
```

### 去重逻辑（2026-08-18 新增）

- **背景**：弱网、React StrictMode 双挂载等导致的**并发重复请求**会触发多次同文案错误；无去重时右上角堆叠多个相同 Toast。
- **改动**：`push()` 对同一 `type + message` 的**已在展示** Toast 直接跳过（3 秒消失后去重集合清理，不影响后续再次提示）。
- **实测**：StrictMode 双请求（2 个并发 `GET /user/info`）→ 只弹 1 个 Toast。
- **注意**：改动提示策略/文案时**勿移除该去重**。

### 弱网 / 重复请求测试步骤

1. 启动 dev server + 后端，浏览器打开 `http://localhost:5173/static/`，`localStorage.removeItem('token')` 清空 token。
2. 切到 `#/profile`（挂载时 StrictMode 下并发发出 2 个 `GET /api/user/info`，均返回业务错误 `code:401`）。
3. 在控制台轮询 `document.querySelectorAll('[role="alert"]').length`：Toast 生命周期（3s）内最大值应为 **1**（去重前为 2 个堆叠）。
4. 模拟弱网：DevTools → Network → Throttling（Slow 3G），或给 mock/后端加延迟；重复请求场景下同文案 Toast 仍只出现 1 次。
5. 佐证：console 中 `[perf] GET /user/info` 日志条数 ≥ Toast 数量——去重只去重弹窗，不影响请求本身。

## 目录结构

```
electron/          # 主进程（窗口管理 / IPC / 状态总线 / userData 配置）
src/
  electron/        # IPC 契约（ipc.ts）与跨窗口同步适配（sync.ts）
  lib/             # Mosaic 布局常量 / SSE 客户端
  stores/          # Zustand store（layout 持久化 + messages + thinking）
  components/      # 面板组件（Chat / Thinking / CodeEditor / Sidebar）
  WorkbenchApp.tsx # 主窗口（Mosaic 多面板）
  DetachedChatApp.tsx # 独立窗口（#/detached/<panelId> 单面板）
scripts/           # 打包脚本 / CDP 验证脚本
docs/zh/           # 架构设计 / 排查指南 / 测试报告
```
