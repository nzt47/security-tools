# 云枢 · afterPack 瘦身打包回归测试清单

> 针对 v2 打包结果（应用 afterPack 钩子剔除 `LICENSES.chromium.html` / `dxcompiler.dll` / `dxil.dll` 后重新打包）的回归验证清单。
> 版本：yunshu-ui 0.1.0（Electron 43.4.0）· 安装包 `release/云枢 Setup 0.1.0.exe`（**80.14MB**，91.5MB → 80.14MB）
> 日期：2026-08-16

---

## 0. 变更摘要（本次打包差异）

| 项 | v1（上一版） | v2（本次） |
|---|---|---|
| 安装包体积 | 91.5MB | **80.14MB**（-11.4MB） |
| win-unpacked | 302.9MB / 22 文件 | 257.6MB / 19 文件 |
| 剔除文件 | — | `LICENSES.chromium.html`（19.4MB）、`dxcompiler.dll`（24.4MB）、`dxil.dll`（1.4MB） |
| 配置变更 | — | `package.json` → `build.afterPack: "scripts/afterPack.mjs"` |

**验证前提**：`npm run check` / `check:electron` 通过；打包日志可见 3 条 afterPack 记录（见 §4）。

---

## 1. 回归用例清单（安装版实测）

> 环境：Windows x64 · 后端 Flask 服务运行于 127.0.0.1:5678 · CDP 端口 9222。
> **执行状态：全部 ✅ 通过（2026-08-16 实测）**

### 1.1 应用启动与渲染（重点：剔除文件不影响启动）

| # | 步骤 | 预期 | 结果 |
|---|---|---|---|
| 1.1.1 | 安装 v2 安装包（静默 `/S /D=<目录>`） | 安装 exit 0，`云枢.exe` 落地 | ✅ |
| 1.1.2 | 启动 `云枢.exe --remote-debugging-port=9222` | 主窗口出现，CDP target 就绪 | ✅ |
| 1.1.3 | 快照 UI（cdp-verify ui） | `appMode` = 桌面模式；四面板标题齐全；4 个"独立窗口"按钮 | ✅ |
| 1.1.4 | 检查 `window.electronAPI` | 存在且 5 个 API 齐全（detachPanel/getWindowMeta/getInitialState/broadcastState/onStateSync） | ✅ |
| 1.1.5 | 页面无渲染错误 | 控制台无 `Failed to load` / 白屏 / 崩溃 | ✅ |

> 说明：dxcompiler/dxil 是 DirectX Shader Compiler，本应用未用 WebGPU/D3D12，剔除后启动与 DOM 渲染不应受影响；LICENSES 为纯文本更无运行时引用。此节重点确认"没删错"。

### 1.2 流式对话（重点：SSE 链路不受影响）

| # | 步骤 | 预期 | 结果 |
|---|---|---|---|
| 1.2.1 | 主窗口输入并发送消息 | 消息发出，状态正常 | ✅ |
| 1.2.2 | 等待 SSE 流式回复 | 收到真实 SSE 出流（`POST /api/chat/stream`），无 `Failed to fetch` / `URL scheme 'file'` | ✅ |
| 1.2.3 | 检查消息区 | `msgCount >= 2`（用户+助手），无错误标记（❌ / ⚠） | ✅ |

### 1.3 独立窗口（detach）功能（重点：IPC + 独立窗口渲染不受影响）

| # | 步骤 | 预期 | 结果 |
|---|---|---|---|
| 1.3.1 | 点击"独立窗口"（chat 面板） | CDP targets 1 → 2，新增 `#/detached/chat` | ✅ |
| 1.3.2 | 主窗口布局 | chat 面板从主窗口摘除（Mosaic 布局更新） | ✅ |
| 1.3.3 | 独立窗口冷启动 | 顶栏"云枢 · 对话" + "独立窗口"标识渲染 | ✅ |
| 1.3.4 | 独立窗口发送消息 | 独立窗口内 SSE 流式回复正常（跨窗口同步消息） | ✅ |

### 1.4 附加：思考链 / 代码编辑器面板（影响面确认）

| # | 步骤 | 预期 | 结果 |
|---|---|---|---|
| 1.4.1 | detach think 面板 | 独立 think 窗口渲染思考节点 | ☐（本次未实测，链路与 chat 共用同一 IPC，风险已由 1.3 覆盖） |
| 1.4.2 | detach code 面板 | 独立 code 窗口编辑器可读 localStorage 内容 | ☐（同上，localStorage 与剔除项无关） |

---

## 2. 验收标准

- 1.1 ~ 1.3 全部通过 → **本次瘦身无回归，可发布**；
- 若 1.1 启动/渲染失败 → 优先怀疑 dxcompiler 剔除过度（回退：afterPack 中移除 dx 项，仅保留 LICENSES）；
- 若 1.2 流式失败 → 与剔除无关（API 地址/后端），按《SSE流式断流乱序排查指南》排查；
- 若 1.3 独立窗口失败 → 与剔除无关（IPC/路由），按架构文档排查。

---

## 3. 回滚预案

- 恢复：删除 `package.json` 的 `afterPack` 字段（或注释掉 `scripts/afterPack.mjs` 中的剔除项）→ 重新打包即回到 91.5MB 基线；
- 保留：`afterPack.mjs` 与文档，便于后续按需调整剔除清单。

---

## 4. 打包日志中的 afterPack 执行记录（实测摘录）

```
[afterPack] 已剔除 LICENSES.chromium.html 于 C:\Windows\TEMP\yunshu-release-v2\win-unpacked
[afterPack] 已剔除 dxcompiler.dll 于 C:\Windows\TEMP\yunshu-release-v2\win-unpacked
[afterPack] 已剔除 dxil.dll 于 C:\Windows\TEMP\yunshu-release-v2\win-unpacked
```

- afterPack 在 `updating asar integrity` 之后、`signing` 之前执行（打包流程正常位置）；
- 剔除后 win-unpacked 校验：3 文件均不存在（Test-Path = False）；
- 安装包构建、blockmap 均正常完成（仅沙盒拦截 Windows Recent 目录写入导致进程退出码非 0，产物完整）。
