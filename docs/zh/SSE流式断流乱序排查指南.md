# 云枢工作台 SSE 流式链路 · 断流/乱序排查指南

> 适用范围：`yunshu-ui` 前端工作台（WorkbenchApp）与 Flask 后端 `/api/chat/stream` 之间的流式输出链路。
> 排查目标：定位**断流**（数据长时间不达 / 提前终止）与**乱序**（分片序号跳变 / 重复）两类问题。

## 1. 链路与代码位置

```
用户输入
  └─ ChatPanel.handleSend()
       └─ useLayoutStore.sendMessage(text)            src/stores/useLayoutStore.ts
            ├─ 创建 user + assistant(streaming) 消息
            ├─ emitStreamLog({kind:'send', ...})      ← 发送日志
            └─ for await (event of createChatStream)  src/lib/sse.ts（真实 fetch + ReadableStream 解析）
                 ├─ 请求发出/连接建立/流结束          ← 传输层日志（HTTP 状态 / connectMs / bytesReceived）
                 ├─ chunk  → append 到消息 content + emitStreamLog({kind:'chunk', seq, text, accumulated})
                 ├─ thinking → 更新右侧面板 + emitStreamLog({kind:'thinking', ...})
                 └─ done   → 置 done + emitStreamLog({kind:'done', accumulated})
                     │       └─ 异常分支 → emitStreamLog({kind:'error'|'abort', ...})
                     ▼
              ChatPanel 日志订阅器                        src/components/workbench/panels/ChatPanel.tsx
              useEffect(() => subscribeStreamLog(...))   ← 断流/乱序检测 + 打印全在此处
```

后端：[app_server.py `POST /api/chat/stream`](c:\Users\Administrator\agent\app_server.py)，`text/event-stream`，事件 `thinking / chunk / done`，chunk 带自增 `seq`。

## 2. 日志体系

所有日志以 `[云枢·SSE]` 为前缀，按 `console.group` 折叠（每次对话一组）。

### 2.1 传输层日志（src/lib/sse.ts）

| 日志 | 触发点 | 关键字段 | 级别 |
|---|---|---|---|
| `请求发出` | fetch 发起前 | url、ts | debug |
| `连接建立` | HTTP 响应到达 | **httpStatus、connectMs** | debug |
| `流结束` | ReadableStream 读完 | **bytesReceived、durationMs** | debug |

> `connectMs` 排查"建连慢"；`bytesReceived` 为传输层字节（含 SSE 帧与分隔符），与业务层 `totalChars` 对账可定位解析丢失。

### 2.2 业务层日志（store 事件 + ChatPanel 打印）

| kind | 触发点 | 关键字段 | 级别 |
|---|---|---|---|
| `send` | 消息发出 | streamId、question、ts | debug（group 标题） |
| `thinking` | 每个推理节点 running/done | title、status、detail | debug |
| `chunk` | 每个分片到达 | **seq、len、accumulated、gapMs、ts** | debug |
| `done` | 流正常结束 | chunks、totalChars、durationMs、rateCharsPerSec | info |
| `error` | 流异常终止 | detail、accumulated | error |
| `abort` | 用户点"停止生成" | accumulated | warn |

> 注意：`send`/`chunk`/`thinking` 及全部传输层日志为 `console.debug` 级，浏览器控制台需勾选 **Verbose** 层级才可见；`done/error/abort` 为 info/warn/error，默认可见。

## 3. 断流检测

判定规则（`ChatPanel.tsx` chunk 分支）：

```ts
const gapMs = trace.lastChunkTs ? e.ts - trace.lastChunkTs : 0;
if (gapMs > 3000) {
  console.warn(`[云枢·SSE] ⚠ 疑似断流：距上个 chunk 达 ${gapMs}ms`, ...);
}
```

- **正常值**：当前演示后端每片间隔 17–25ms；真实 LLM 场景 50–500ms 均属正常。
- **触发条件**：相邻两片间隔 > 3000ms（阈值可按业务调大，避免长思考停顿误报）。
- **含义**：`gapMs` 只反映"前端两次读到数据之间"的空窗，不代表后端停止——需要结合后端日志（`[workbench][SSE]` 前缀）与 `durationMs` 判断断在传输层还是生成层。

常见根因与对策：

| 现象 | 可能原因 | 对策 |
|---|---|---|
| 全程 gapMs 持续大 | 反向代理缓冲（nginx/gunicorn 未关缓冲） | 确认 `X-Accel-Buffering: no`；代理层关 `proxy_buffering off` |
| 中段出现一次大 gap 后恢复 | 后端长思考/工具调用 | 属正常，可将阈值调大；或后端在思考期间发心跳注释行 `: keep-alive` |
| gap 越来越大最终 error | 后端崩溃 / 连接被掐 | 看后端日志与 `error` 事件 detail |
| 有 done 但字符数偏少 | 后端提前 break | 对比 `totalChars` 与后端预期输出长度 |

## 4. 乱序 / 丢包检测

判定规则（`ChatPanel.tsx` chunk 分支，依赖后端 chunk 的 `seq`）：

```ts
if (e.seq <= trace.lastSeq) {
  console.warn(`[云枢·SSE] ⚠ 乱序/重复：seq=${e.seq}（上次 ${trace.lastSeq}）`);
} else if (trace.lastSeq >= 0 && e.seq - trace.lastSeq > 1) {
  console.warn(`[云枢·SSE] ⚠ 疑似丢包：seq ${trace.lastSeq} → ${e.seq} 跳变`);
}
```

- **乱序/重复**（`seq` 不大于上次）：同一片被重复下发，或生成器多线程并发写响应。
- **跳变**（间隔 > 1）：中间片丢失（缓冲丢弃）或 `seq` 生成逻辑漏号。
- SSE 基于 HTTP 长连接按序传输，**天然有序**——出现乱序几乎都来自应用层（生成器并发/重试），跳变则多为解析层丢事件块。可配合后端 `_workbench_demo_stream` 的 `seq` 递增核对。

## 5. 汇总指标解读

```ts
console.info('[云枢·SSE] ✅ 流式完成', { chunks, totalChars, durationMs, rateCharsPerSec });
```

- `chunks`：收到的分片总数，应与后端发出数量一致（当前演示恒为 133）。
- `totalChars`：累计字符数，用于与后端输出长度对账，差量大 = 丢片。
- `durationMs`：send → done 全程耗时；`rateCharsPerSec = totalChars / durationMs * 1000` 评估吞吐（演示约 125–128 字符/秒）。

## 6. 实战案例：跨会话误报"疑似断流"

**现象**：第 2 次对话首个 chunk 打印 `⚠ 疑似断流：距上个 chunk 达 104750ms`，但本会话内 gap 全部 17–25ms。

**根因**：`send` 分支重置了 `lastSeq / chunkCount / totalChars / startTs`，**漏重置 `lastChunkTs`**，新会话首个 chunk 的 gapMs 沿用了上次会话最后一片的时间戳。

**修复**：`send` 分支补一行：

```ts
trace.lastChunkTs = 0; // 防止跨会话首个 chunk 用上次的时间戳误报断流
```

**教训**：会话级统计变量必须在 `send` 事件统一清零，缺一不可。

## 7. 排查 Checklist

1. 打开控制台，勾选 **Verbose**，过滤 `[云枢·SSE]`。
2. 发送一条消息，确认 `send` → 多条 `chunk#N` → `✅ 流式完成` 顺序出现。
3. 无 `✅` 但有 `❌`：读 `detail`（如 `SSE 请求失败: HTTP 500`），查后端日志。
4. 有 `✅` 但出现 `⚠ 断流/乱序`：按第 3/4 节逐条对照。
5. 对账 `totalChars` 与后端预期输出；抽查 `gapMs` 分布。
6. 若怀疑代理层：直连后端 `curl -N -X POST http://127.0.0.1:5678/api/chat/stream -H "Content-Type: application/json" -d '{"message":"hi"}'` 对比行为。
7. 复查本次会话 `ts` 是否与上次会话的 ts 相差巨大（跨会话残留统计）。

## 8. 已知正常噪音

- 浏览器 Network 面板对 SSE 正常关闭标记 `net::ERR_ABORTED`，属常态，JS 层无感知、不影响业务。
- 日志均为前端统计，不直接反映后端生成耗时；后端耗时以 `app_server.py` 中 `[workbench][SSE]` 日志为准。

## 9. 已知问题与修复记录（2026-08-16）

> 以下问题均在"双标签页联调 / 安装版实测"中发现并已修复，留存排查路径供回归参考。

### 9.1 重复开窗：点击一次"独立窗口"弹出两个窗口

- **现象**：Web mock 双标签页联调中，点击一次"独立窗口"按钮打开 2 个相同 `#/detached/chat` 标签页。
- **根因**：`DetachButton` 内部先调用 `api.detachPanel`（开窗第 1 次），随后调用 `onDetached(panelId)`，而上层 `WorkbenchApp.detachPanel` 又执行一次 `api.detachPanel`（开窗第 2 次）→ IPC 双重调用。
- **修复**：`DetachButton` 不再内部调用 IPC，仅回调 `onDetached`，由上层统一执行 IPC 建窗 + 布局摘除（[DetachButton.tsx](c:\Users\Administrator\agent\yunshu-ui\src\components\workbench\DetachButton.tsx)）。
- **回归**：修复后单次点击恰好打开 1 个标签页 ✅

### 9.2 layout=null 时摘除失效：面板不移除

- **现象**：未自定义布局（store 中 `layout` 为 null，渲染走 `DEFAULT_LAYOUT` 兜底）时 detach 后对话面板仍保留在主窗口。
- **根因**：`detachPanel` 用 `getState().layout`（可能为 null）调 `removePanelFromLayout`，null 输入直接返回 null → `setLayout(null)` 无变化。
- **修复**：以实际渲染的布局树为基准 `removePanelFromLayout(layout ?? DEFAULT_LAYOUT, panelId)`（[WorkbenchApp.tsx](c:\Users\Administrator\agent\yunshu-ui\src\WorkbenchApp.tsx#L80-L83)）。
- **对照实验**：布局自定义后 detach 成功，主窗口仅剩 nav|think 并持久化 ✅

### 9.3 安装版 preload 崩溃：`electronAPI` 未注入（重要）

- **现象**：Windows 安装版实测，UI 渲染正常但显示"Web 模式"、"独立窗口"按钮消失，控制台 `window.electronAPI` 为 undefined。
- **根因**：vite-plugin-electron 将 `preload.ts` 打包为 **CJS 内容（`require("electron")`）但扩展名 `.mjs`**。Electron 按 ESM 加载 `.mjs`，`require is not defined` → preload 脚本崩溃 → contextBridge 注入失败。
- **修复**：`vite.config.ts` preload 段强制 `rollupOptions.output.format='cjs'` + `entryFileNames:'[name].cjs'`；主进程 `secureWebPreferences` 引用改为 `preload.cjs`。
- **配套**：`.env.production` 新增 `VITE_API_BASE=http://127.0.0.1:5678`——Electron 以 `file://` 加载页面，相对路径 `/api/chat/stream` 会指向 `file:///api/chat/stream` 而失败，必须配置后端绝对地址。

#### 9.3.1 根因分析（preload CJS/ESM 冲突）

**机制链**（为什么安装版 UI 正常、但桌面能力全部消失）：

```
package.json "type":"module"（项目级）
  → vite-plugin-electron 构建 preload 时，输出扩展名跟随包类型 → .mjs
  → 但 preload 的模块格式是 rollup 的 cjs（内容里是 require('electron')）
  → Electron 主进程按 .mjs 扩展名以 ESM 加载 preload
  → ESM 环境无 require 全局 → ReferenceError: require is not defined
  → preload 脚本抛错，contextBridge.exposeInMainWorld 从未执行
  → window.electronAPI === undefined
  → isElectron() 返回 false → 渲染成"Web 模式"、"独立窗口"按钮隐藏
```

**判定要点**：
- 这类故障 UI 完全不报错、页面正常渲染——因为 renderer 的 React 应用与 preload 是两条独立执行线，preload 崩溃不影响页面加载；
- 区分手段：控制台执行 `typeof window.electronAPI`，undefined 即 preload 未注入；
- 修复落点：保证 **preload 的「模块格式」与「文件扩展名」一致**（本项目统一为 CJS + `.cjs`）。

**回归注意**：`vite.config.ts` 与 `electron/main.ts` 中 preload 文件名（`.cjs`/`.mjs`）必须同步修改，否则路径不匹配会退化为"找不到 preload"。

#### 9.3.2 根因分析（file:// 下 API 地址失效）

**机制链**（为什么 Web/Flask 部署正常、安装版流式对话必失败）：

```
Web 部署：页面由 http(s)://host/static/ 加载
  → 相对路径 /api/chat/stream → http://host/api/chat/stream（同源，正常）

Electron 安装版：页面由 file:///.../resources/app.asar/dist/index.html 加载
  → 相对路径 /api/chat/stream → file:///api/chat/stream
  → fetch 对 file:// 发起请求 → 立即失败（协议不支持 / 无服务监听）
```

**判定要点**：
- 构建产物中搜索 `api/chat/stream`，若前缀为空字符串即未注入 `VITE_API_BASE`；
- 现象是"点发送后无任何回复，控制台 `Failed to fetch` 或 `URL scheme 'file' is not supported`"；
- 修复落点：`.env.production`（或打包机环境变量）必须配置 `VITE_API_BASE=<后端绝对地址>`，桌面版没有 dev proxy 兜底。

**回归注意**：`VITE_API_BASE` 是**构建期**注入（`import.meta.env`），改 `.env.production` 后必须**重新打包**才生效，改源码热更新无效。

#### 9.3.3 回归确认（体积优化后重新打包 + 安装版实测，2026-08-16）

应用 2.1/2.2 优化（排除 node_modules + 关闭 sourcemap）后重新执行 `npm run dist:electron`，安装到独立目录（`/S /D=` 静默安装）后实测：

| 验证项 | 结果 |
|---|---|
| 安装包体积 | 98.6MB → **91.5MB**（app.asar：64.9MB → **1.06MB**，node_modules 已剔除） |
| preload 注入 | `window.electronAPI` 存在，5 个 API（detachPanel/getWindowMeta/getInitialState/broadcastState/onStateSync）齐全，"桌面模式" ✅ |
| detach 独立窗口 | 点击"独立窗口"恰好新增 1 个 `#/detached/chat` 窗口 ✅ |
| 流式对话（file://） | `POST /api/chat/stream` 真实 SSE 出流，无 `Failed to fetch`，无错误标记 ✅ |

结论：9.3 两个根因（preload CJS/ESM 冲突、file:// 下 API 地址失效）的修复在**重新打包后仍有效**，且体积优化未引入回归。

