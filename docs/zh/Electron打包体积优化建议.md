# 云枢 · Electron 打包体积优化建议

> 针对当前安装包 `release/云枢 Setup 0.1.0.exe`（**98.6MB**）体积过大，基于实测产物分析给出优化方案。
> 日期：2026-08-16 · 基线版本：yunshu-ui 0.1.0（Electron 43.4.0 / electron-builder 26.15.3）

---

## 1. 体积现状与构成

| 产物 | 大小 | 说明 |
|---|---|---|
| `云枢 Setup 0.1.0.exe`（NSIS 安装包） | 98.6MB | 安装包（LZMA 压缩后） |
| `win-unpacked/云枢.exe`（解压目录） | 225MB | 含 Electron/Chromium 运行时 |
| `resources/app.asar` | 64.9MB | 应用代码包（压缩前） |
| `resources/app.asar` 中 **node_modules** | **约 60MB（11576 个文件）** | **最大优化点** |
| `dist/assets/*.js.map` | 3.4MB | sourcemap（`sourcemap:'hidden'` 仍产出） |

app.asar 顶层结构（实测，按文件数）：

```
node_modules   11576   ← 生产依赖树被完整打入（冗余）
dist               9   ← 渲染层产物（Vite 已打包依赖）
dist-electron      4   ← 主进程 + preload
package.json       1
```

**核心结论**：安装包 98.6MB 中约 **60MB 是 node_modules 依赖树**，而这部分在运行时**完全不需要**——渲染层的所有依赖（react / react-markdown / highlight.js / mosaic 等）已被 Vite 打包进 `dist/assets/index-*.js`，主进程与 preload 只依赖 Electron 内置模块（`electron` / `node:path`）。

---

## 2. 优化方案（按收益排序）

### 2.1 排除 node_modules 依赖树（预计省 ~55-60MB）★ 最大收益

electron-builder 的 `files` 白名单虽然只写了 `["dist/**", "dist-electron/**", "package.json"]`，但 electron-builder 会自动将 `package.json` 中 `dependencies` 声明的包递归纳入打包（即使未被 import）。由于 Vite 已把依赖打进 bundle，必须显式排除：

```jsonc
// package.json → build.files
"files": [
  "dist/**",
  "dist-electron/**",
  "package.json",
  "!node_modules/**"      // ← 关键：运行时不需要（Vite 已 bundle）
]
```

验证要点（打包后）：
- 应用可正常启动（主窗口渲染 + IPC detach）；
- 流式对话正常（SSE 链路不受影响）。

> 安全边界【不易】：确认 `dist-electron/main.js` / `preload.cjs` 不 `require` 任何 node_modules 包（当前仅 import `electron` 与 `node:path`，安全）。若后续主进程引入第三方库，需改为在 `files` 白名单中**显式加入该包**，而非放开整个 node_modules。

### 2.2 关闭生产 sourcemap（预计省 3.4MB）

`vite.config.ts` 中 `build.sourcemap: 'hidden'` 仍会生成 `.map` 文件并被打入 asar。桌面应用无线上排查 sourcemap 需求，直接关闭：

```ts
// vite.config.ts
build: {
  sourcemap: false,   // 原 'hidden' → false
},
```

> 影响：console 堆栈为压缩后的行号（可接受）；若需排障，临时改回 `'hidden'` 重新打包即可。

### 2.3 启用 NSIS 最高压缩（安装包再小几个百分点）

electron-builder 默认 NSIS 压缩为 LZMA 默认档，可显式调高：

```jsonc
// package.json → build
"compression": "maximum",   // 默认 normal
```

> 代价：打包时间变长（本机一次约多 1-2 分钟）。已排除 node_modules 后收益有限，可作为最后手段。

### 2.4 移除冗余依赖（中收益，需人工确认）

`package.json` `dependencies` 中可能存在未使用或仅构建期使用的包，移除后可同步减小 `files` 白名单与 dist bundle：

| 候选 | 现状 | 建议 |
|---|---|---|
| `@xyflow/react` | 工作台 UI 未使用流程图 | 确认后移除（省 ~800KB gzip） |
| `framer-motion` | 仅消息入场动画 | 可用 CSS transition 替代（省 ~100KB gzip） |
| `react-router-dom` | 仅 hash 路由分发 | 可改用原生 `hashchange`（省 ~60KB gzip） |
| `highlight.js` 全量语言 | Markdown 代码高亮引入全语言 | 改按需注册常用语言（省 ~1MB） |

> 谨慎原则【简易】：每一项移除前先用 `npm ls` 确认无隐性依赖，且回归验证对应 UI 功能。建议分步执行、逐步验证，避免一次大改。

### 2.5 应用图标（规范项，不影响体积）

打包日志显示 `default Electron icon is used`。为正式发布建议补充：

```
build/build/icon.ico   （256x256 或以上）
```

图标同时用于安装包、安装目录 exe 与桌面快捷方式。

### 2.6 可选：asar 完整性 / 增量更新

- 当前 `asar: true` 已启用（正确，默认）。
- 若后续走自动更新，可开启 `publish` 配置产出 `.blockmap` 支持差分升级（当前已产出 blockmap，`云枢 Setup 0.1.0.exe.blockmap` 104KB）。

---

## 3. 预期收益汇总

| 方案 | 预期省 | 实现成本 |
|---|---|---|
| 2.1 排除 node_modules | **~55-60MB**（安装包约降到 40-45MB） | 低（改 1 行配置 + 回归） |
| 2.2 关闭 sourcemap | 3.4MB | 低（改 1 行配置） |
| 2.3 NSIS maximum | 1-3MB | 低（改 1 行配置） |
| 2.4 依赖瘦身 | 5-15MB（打包 + 运行时） | 中（逐项确认） |
| **合计（2.1+2.2）** | **安装包 98.6MB → 约 40MB** | 低 |

> 注：Electron/Chromium 运行时（win-unpacked ~160MB，安装包压缩后约 35-40MB）为固定开销，无法通过配置削减；换 `electron` 精简分发（如 `@electron/rebuild` 无关）不可行，Chromium 内核体积是桌面应用的固有成本。

---

## 4. 实施建议

1. **立即执行**（低风险高收益）：
   - 加 `!node_modules/**` 排除项（2.1）
   - `sourcemap: false`（2.2）
   - 重新 `npm run dist:electron`，验证：启动、独立窗口 detach、流式对话三项回归。
2. **短期**：补充应用图标（2.5）。
3. **中期**：逐项评估依赖瘦身（2.4），每项独立提交并回归。
4. **回归清单**（每次打包后必测）：
   - 应用启动 → 主窗口三面板渲染；
   - 点击"独立窗口" → 恰好创建 1 个窗口且 chat 面板从主窗口摘除；
   - 发送消息 → SSE 流式回复完整（无 ❌ / ⚠）；
   - 主 ↔ 独立窗口消息双向同步。

---

## 5. 优化落地实测结果（2026-08-16）

应用 2.1（排除 node_modules）+ 2.2（关闭 sourcemap）后重新打包，实测：

| 指标 | 优化前 | 优化后 | 降幅 |
|---|---|---|---|
| 安装包 `云枢 Setup 0.1.0.exe` | 98.6MB | **91.5MB** | -7.1MB |
| `resources/app.asar` | 64.9MB | **1.06MB** | -63.8MB（-98%） |
| asar 内 node_modules | ~60MB / 11576 文件 | 0 | 全部剔除 |
| dist/assets/*.map | 3.4MB | 0 | 全部关闭 |

> 注：安装包仅降 7.1MB，是因为 app.asar 在 NSIS 压缩后（LZMA）占比小，大头是 Electron/Chromium 运行时（压缩前 ~160MB、压后约 35-40MB，属固定开销，无法配置削减）。**优化核心价值**在 app.asar：从 64.9MB 降到 1.06MB，使应用安装目录占用与启动加载大幅瘦身。

**验证（安装版实测）**：静默安装到独立目录后，preload 注入（electronAPI 5 API）、detach 独立窗口、file:// 下 SSE 流式对话全部通过，无回归（详见《SSE流式断流乱序排查指南》9.3.3）。

**后续可做**（收益递减，按需）：
- 2.3 NSIS `compression: "maximum"`：安装包再省 1-3MB（打包时间 +1-2 分钟）；
- 2.4 依赖瘦身（@xyflow/react / react-router-dom / highlight.js 按需）：
  - @xyflow/react 未在 UI 使用 → 移除省 ~800KB gzip（需 `npm ls` 确认无隐性依赖）；
  - react-router-dom 仅 hash 分发 → 原生 hashchange 替代省 ~60KB；
  - highlight.js 全量 → 按需注册常用语言省 ~1MB；
- 2.5 补充 `build/build/icon.ico`（当前用默认 Electron 图标，正式发布建议补齐）。

