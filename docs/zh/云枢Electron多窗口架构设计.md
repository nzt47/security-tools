# 云枢（CloudHub）Electron 多窗口架构设计图解（文字版）

> 范围：Electron 主进程、主渲染窗口、独立子窗口三方的**通信机制**与**状态同步方案**。
> 适用版本：yunshu-ui 0.1.0（Electron 43.4 / react-mosaic 7 / Zustand 5）
> 日期：2026-08-16

---

## 1. 总览：三方拓扑

```
┌────────────────────────────────────────────────────────────────────┐
│  Electron 主进程 (electron/main.ts)                                 │
│  ─────────────────────────────────────────────                      │
│  · 窗口生命周期：创建主窗口 / 动态创建独立子窗口                       │
│  · IPC 注册中心：DetachPanel / StateSync / WindowMeta / GetInitialState │
│  · 跨窗口状态总线：把快照转发给"除源窗口外"的所有窗口                  │
│  · 安全校验：detach 面板白名单 + 路由前缀校验                         │
└───────────────┬──────────────────────────┬──────────────────────────┘
                │  ipcMain.handle / on     │  ipcMain.handle / on
        (invoke/send)                (invoke/send)
                ▼                          ▼
┌───────────────────────┐      ┌─────────────────────────────┐
│ 主渲染窗口 (BrowserWindow)│      │ 独立子窗口 (BrowserWindow)    │
│  - WorkbenchApp         │      │  - DetachedChatApp           │
│  - Mosaic 多面板布局     │      │  - 仅渲染被分离的单面板        │
│  - 唯一入口：/ 路由      │◄────►│  - 路由：#/detached/<panelId> │
│  - 布局 + 全部面板       │ 同步  │  - 布局摘除，面板移出主窗口   │
└───────────────────────┘      └─────────────────────────────┘
         ▲                                     ▲
         │      渲染进程内：React + Zustand     │
         └─────────────────────────────────────┘
              preload.ts (contextBridge 白名单 5 API)
```

关键点：**主进程是唯一"桥梁"**。渲染层不直接互相通信，一切跨窗口消息都经 IPC 到主进程，由主进程做转发/校验。这保证 contextIsolation 下的安全边界（渲染层无法触达其它窗口）。

---

## 2. 三方的角色与职责

| 角色 | 代码位置 | 职责 |
|---|---|---|
| 主进程 | [electron/main.ts](../../yunshu-ui/electron/main.ts) | 窗口管理、IPC 监听、状态总线、安全校验 |
| 预加载 | [electron/preload.ts](../../yunshu-ui/electron/preload.ts) | contextBridge 暴露白名单 API，隔离 Node |
| 主渲染窗口 | [src/WorkbenchApp.tsx](../../yunshu-ui/src/WorkbenchApp.tsx) | Mosaic 布局、拖拽拦截、detach 发起、状态广播 |
| 独立子窗口 | [src/DetachedChatApp.tsx](../../yunshu-ui/src/DetachedChatApp.tsx) | 渲染单面板、冷启动拉快照、持续同步 |
| 同步适配器 | [src/electron/sync.ts](../../yunshu-ui/src/electron/sync.ts) | 节流广播 + 防回环合并（主/子窗口共用） |
| IPC 契约 | [src/electron/ipc.ts](../../yunshu-ui/src/electron/ipc.ts) | 通道常量 + 载荷类型（三方共享，纯 TS 无依赖） |

---

## 3. 通信链路详解

### 3.1 面板分离（渲染 → 主进程 → 新窗口）

```
[主窗口] 用户点击"独立窗口" / 拖拽面板到窗口边缘（EDGE_PX=48）
   │
   ├─ DetachButton.onClick / WorkbenchApp.onDrop
   │      ↓  window.electronAPI.detachPanel(req)      ← preload 暴露
   ├─ ipcRenderer.invoke('window:detach-panel', req)  ← preload 内部
   │      ↓
   ├─ [主进程] ipcMain.handle('window:detach-panel')
   │      ├─ 白名单校验 panelId ∈ {chat,think,nav,code} + route 前缀 /detached/
   │      ├─ new BrowserWindow(720×860, secureWebPreferences)   ← 独立窗口
   │      ├─ loadRenderer(win, '/detached/chat')                ← hash 路由
   │      ├─ detachedWindows.set(wcId, panelId)                 ← 元信息注册
   │      └─ pendingInitialState.set(wcId, req.initialSnapshot) ← 快照暂存
   │      ↓ 返回新窗口 webContents.id
   └─ [主窗口] detachPanel 成功 → setLayout(removePanelFromLayout(...))
              → 面板从主窗口布局树摘除（"移出"语义）
```

**难点处理 1——快照时序**：新窗口加载需要时间，期间源窗口可能已继续广播。若只依赖"收到即同步"，新窗口会错过加载前的广播。解决：detach 请求**携带分离瞬间的 messages/thinking 快照**，主进程按窗口暂存；新窗口启动后通过 `GetInitialState` 一次性拉取（取后即删），再进入持续同步。

### 3.2 跨窗口状态同步（双向广播）

```
任一窗口状态变化（messages / thinking）
   │
   ├─ Zustand subscribe 置 dirty 标记
   ├─ setInterval 150ms 节流：仅 dirty 时发送
   │      ↓  window.electronAPI.broadcastState(snapshot)
   ├─ ipcRenderer.send('window:state-sync', payload)      ← 单向，无回执
   │      ↓
   ├─ [主进程] ipcMain.on('window:state-sync')
   │      └─ 遍历所有窗口，转发给除 sourceId 外的窗口
   │      ↓
   └─ 其它窗口 ipcRenderer.on → onStateSync(cb)
          ├─ JSON 对比 local vs incoming
          ├─ 相同 → 跳过（防回环）
          └─ 不同 → setState 合并快照
```

**难点处理 2——防回环**：广播 → 收到 → 再广播会形成无限循环。双层防护：
1. **主进程不回传源窗口**（`wc.id !== sourceId` 过滤）；
2. **接收端 JSON 对比**（内容一致则跳过 setState），即使主进程漏滤，也不会因"状态没变还广播"而震荡。

**难点处理 3——流式高频分片**：SSE 分片约 20ms/片，若每片都广播会打爆 IPC。采用 **150ms 节流合并**：多次状态变化合并为一个快照广播，实时性与开销平衡（实测消息 + 流式回复双向同步稳定）。

---

## 4. 安全设计

| 措施 | 实现 |
|---|---|
| contextIsolation: true | 渲染层与 preload 隔离，无法直接访问 Node |
| nodeIntegration: false | 渲染层禁用 Node 集成 |
| sandbox: false + CJS preload | preload 仅 require electron（已修复 CJS/ESM 扩展名不匹配问题） |
| 白名单 API | 仅暴露 detachPanel / getWindowMeta / getInitialState / broadcastState / onStateSync，不暴露 ipcRenderer 本体 |
| 主进程参数校验 | panelId 白名单 + route 前缀校验，防渲染层注入非法值 |
| 窗口配置统一 | 所有窗口（主/子）共用 secureWebPreferences()，无例外 |

---

## 5. 独立窗口路由分发

```
src/main.tsx（每个渲染进程入口）
  ├─ hash = #/detached/<panelId>
  │    ├─ 白名单校验 chat/think/nav/code
  │    └─ 是 → <DetachedChatApp panelId>  （单面板，冷启动最小化）
  └─ 其它 → <WorkbenchApp>                （主工作台）
```

- 独立窗口只挂载被分离面板对应的组件（代码分割友好，冷启动资源最小化）；
- Web 联调模式（VITE_MOCK_ELECTRON=1）：mockElectron 用 BroadcastChannel + localStorage 模拟主进程总线，双标签页可本地验证同一套同步逻辑。

---

## 6. 状态同步契约（StateSyncPayload）

```ts
interface StateSyncPayload {
  type: 'snapshot';       // 当前仅全量快照策略
  messages: unknown[];    // 对话消息（含流式增量）
  thinking: unknown[];    // 思考/工具调用事件流
}
```

设计取舍【简易】：聊天消息与思考事件数据量小（KB 级），**全量快照**实现最简单且天然一致，避免增量补丁的合并复杂度与乱序问题。若未来出现大体积面板状态（如代码编辑器大文档），可扩展为"按字段订阅"或 hash 差分，但当前不引入（拒绝过度设计）。

---

## 7. 数据流全景时序（一次完整 detach + 对话）

```
主窗口加载 → startCrossWindowSync() 启动订阅/广播
  │
用户点击"独立窗口"（chat）
  ├─ detachPanel(invoke) ──→ 主进程建窗 ──→ 新 BrowserWindow 加载 #/detached/chat
  ├─ 主窗口：removePanelFromLayout 摘除 chat 面板
  └─ 新窗口：DetachedChatApp 挂载
        ├─ startCrossWindowSync()
        ├─ getInitialState(invoke) → 拉取分离瞬间快照 → setState
        └─ 进入持续同步
  │
主窗口发送消息 → Zustand messages 变化
  ├─ sync.ts 150ms 节流 → broadcastState(send)
  └─ 主进程转发 → 独立窗口 onStateSync → 对比 → setState → 渲染
  │
独立窗口输入回复 → 同路径反向广播 → 主窗口同步显示
```

---

## 8. 更新记录

### 8.1 v0.1.1 — 新增 CodeEditor 面板（2026-08-16）

**变更摘要**：默认布局由三面板（nav / chat / think）扩展为四面板（nav / chat / think+code），新增轻量代码编辑器面板，并完整接入 detach 独立窗口链路。

#### 实现细节

| 项 | 说明 |
|---|---|
| 面板 ID | `code`（加入 [mosaic.ts](../../yunshu-ui/src/lib/mosaic.ts#L12-L27) 的 `PANEL` 常量与 `PANEL_TITLES`，成为持久化锚点） |
| 组件 | [CodeEditorPanel.tsx](../../yunshu-ui/src/components/workbench/panels/CodeEditorPanel.tsx)：textarea 编辑 + highlight.js 实时高亮预览（400ms 防抖） |
| 语言支持 | TypeScript / JavaScript / Python / JSON / Bash / 纯文本（`highlight.js/lib/common` 子集，未引全量语言包） |
| 状态持久化 | `localStorage['yunshu:editor:code:v1']`（存 `{lang, content}`，损坏数据回退默认示例） |
| 默认布局 | `nav | chat | (think / code)`，右侧上下分栏 55/45（见 [mosaic.ts](../../yunshu-ui/src/lib/mosaic.ts#L29-L51)） |
| 主窗口渲染 | [WorkbenchApp.tsx](../../yunshu-ui/src/WorkbenchApp.tsx) `renderPanel` 增加 `PANEL.CODE` 分支 |
| 独立窗口渲染 | [DetachedChatApp.tsx](../../yunshu-ui/src/DetachedChatApp.tsx) `PANEL_LABEL` + `renderDetachedPanel` 增加 `code` 分支（顶栏标题"云枢 · 代码编辑器"） |
| detach 白名单 | [electron/main.ts](../../yunshu-ui/electron/main.ts) 主进程校验、[ipc.ts](../../yunshu-ui/src/electron/ipc.ts) `DETACHABLE_PANELS`、[main.tsx](../../yunshu-ui/src/main.tsx) hash 路由白名单三处同步加入 `code` |
| 样式 | [workbench.css](../../yunshu-ui/src/styles/workbench.css) 新增 `.wb-editor-*`（深色等宽 + 半透明预览区，与玻璃拟态主题一致） |

#### 布局变更说明

- 旧默认布局：`nav | (chat / think)`（三面板，20/80 外层，58/42 内层）。
- 新默认布局：`nav | (chat / (think / code))`（四面板，16/84 外层，72/28 中层，55/45 内层）。
- **存量用户兼容**：布局持久化键 `yunshu:mosaic:layout:v1` 不变；已保存的旧三面板布局会被 `sanitizeLayout` 合法放行，用户点击"重置布局"即切到含 code 的新默认布局（新用户首次启动即为四面板）。

#### 编辑器状态同步/持久化设计（重点）

**决策**：编辑器内容**不走** StateSyncPayload（messages/thinking）的跨窗口广播通道，而是利用 **Electron 同 session 多窗口共享 localStorage** 实现"隐式同步"。

- 主窗口编辑 → 写入 localStorage → 独立窗口（同 partition）读取同一 key → 内容一致；
- 持久化在组件内 `useEffect` 防抖写入，无需扩展 IPC 契约（保持 `StateSyncPayload` 形状不变，符合【不易】）；
- 局限：仅"写后读"最终一致，无实时双向合并；对编辑器场景（单人单文档）足够。若未来需要多窗口实时协作编辑，再扩展为按字段广播或 CRDT（当前不引入）。

#### 验证记录

- `npm run check` / `check:electron` 类型检查通过；
- Web 浏览器（mock Electron）：四面板渲染、语法高亮、示例代码/语言切换、`#/detached/code` 独立窗口 detach、主窗口面板摘除全部通过；
- 桌面安装包：重新打包后独立窗口编辑器同步与持久化实测通过（详见联调测试报告）。

### 8.2 v0.1.2 — 资源优化策略（2026-08-16）

**目标**：安装包 98.6MB → 78.61MB（-20%），app.asar 64.9MB → 1.01MB（-98%）。本节省略记录**资源优化的决策与结论**（含"能删/不能删"边界，供后续迭代参考）。

#### 8.2.1 优化手段与结论

| 手段 | 配置/位置 | 收益 | 结论 |
|---|---|---|---|
| 排除 node_modules | `build.files: ["!node_modules/**"]` | app.asar 64.9→1.06MB | ✅ 依赖已由 Vite bundle，运行时不需要 |
| 关闭 sourcemap | `build.sourcemap: false` | ~3.4MB | ✅ 桌面版无线上排障需求 |
| 精简语言包 | `build.electronLanguages: ["zh-CN","en-US"]` | ~全量→1.1MB | ✅ 仅保留中英 |
| afterPack 剔除 | `scripts/afterPack.mjs`（LICENSES/dxcompiler/dxil/vk 三件套） | 安装包 -13MB | ✅ 剔除后逐项实测回归通过 |
| compression maximum | `build.compression: "maximum"` | 0（实测） | ⚠️ 无收益但无害，保留 |
| 清理遗留文件 | 删 `public/storage-test.*` + `files` 排除 `preload.mjs` | ~10KB | ✅ 正确性清理（本不该产出） |

#### 8.2.2 运行时文件"能删/不能删"边界（实测结论，【不易】约束）

> 判据：**安装版 CDP 实测**（主窗口 + detach 独立窗口 + SSE 流式全链路），非静态推断。任何删除项变更后必须重跑该回归。

| 文件 | 结论 | 依据 |
|---|---|---|
| `LICENSES.chromium.html` | ✅ 可删 | 纯许可文本，零运行时引用 |
| `dxcompiler.dll` / `dxil.dll` | ✅ 可删 | 未用 WebGPU/D3D12 计算 |
| `vk_swiftshader.dll` / `vulkan-1.dll` / `icd.json` | ✅ 可删 | 软渲染兜底，实测主/独立窗口均正常 |
| `ffmpeg.dll` | ❌ **保留** | **删除后渲染进程异常**（页面 title 空、probe 无响应）——Electron 的 ffmpeg.dll 是 Chromium 渲染管线依赖，非纯音视频解码 |
| `d3dcompiler_47.dll` | ❌ **保留** | **删除后 detach 独立窗口崩溃**（新渲染进程需现场编译 shader；主窗口正常会误导判断） |
| `elevate.exe` | ❌ **保留** | NSIS 提权助手，与 d3dcompiler 同场景实测 |

**关键教训**：单窗口正常 ≠ 多窗口正常。本架构的面板分离依赖**动态创建新渲染进程**，任何运行时文件剔除必须包含 detach 回归。

#### 8.2.3 体积天花板

安装包 ~78.6MB ≈ Chromium 运行时压缩后（~77.5MB）+ app.asar（~1MB）。低于 75MB 需裁剪 Chromium 二进制（官方无精简发行版，收益为负），**当前 78.6MB 视为工程极限**。详见《非核心资源清理分析.md》。
