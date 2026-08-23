# 云枢 · 91.5MB 安装包后续瘦身方案（Chromium 运行时专题）

> 针对当前安装包 `release/云枢 Setup 0.1.0.exe`（**91.5MB**）的后续瘦身分析。
> 上一轮已完成的优化（排除 node_modules + 关闭 sourcemap + 精简语言包）已把 app.asar 从 64.9MB 压到 1.06MB，**应用层可优化空间已耗尽**；本方案聚焦剩余体积的绝对大头——**Chromium 运行时**。
> 日期：2026-08-16 · 基线：yunshu-ui 0.1.0（Electron 43.4.0 / electron-builder 26.15.3）

---

## 1. 现状拆解（实测，win-unpacked 302.9MB）

| 文件 | 大小 | 类别 | 是否可动 |
|---|---|---|---|
| `云枢.exe`（Electron 主二进制） | **215.1MB** | Chromium + V8 + Node 合并体 | 不可动（发行版固定） |
| `dxcompiler.dll` + `dxil.dll` | 24.4MB + 1.4MB | DirectX Shader Compiler（WebGPU / D3D12 用） | **可删（本应用未用 WebGPU）** |
| `LICENSES.chromium.html` | **19.4MB** | Chromium 开源许可文本 | **可删（纯文本，无运行时用途）** |
| `icudtl.dat` | 10.4MB | ICU 国际化数据（i18n 必需） | 不可动 |
| `libGLESv2.dll` | 7.7MB | ANGLE GL→D3D 转换层 | 不可动（渲染必需） |
| `resources.pak` | 6.9MB | Chromium 内置资源（图标/UI 文案） | 不可动 |
| `vk_swiftshader.dll` | 5.3MB | Vulkan 软件渲染（无 GPU 时兜底） | 可删（有 GPU 环境不需要，见风险） |
| `d3dcompiler_47.dll` | 4.5MB | HLSL shader 编译 | 保留（ANGLE 依赖） |
| `ffmpeg.dll` | 2.9MB | 音视频解码 | 保留（本应用无音视频，可评估删） |
| `v8_context_snapshot.bin` / `snapshot_blob.bin` | 1.1MB | V8 启动快照 | 不可动 |
| `app.asar` | 1.0MB | 应用代码（已优化） | — |
| locales（2 个 pak）+ 其余 | ~1.5MB | 语言包（已精简） | — |

**核心事实**：安装包 91.5MB 中约 **88MB 是 Electron/Chromium 运行时压缩后**，应用代码（1.06MB）占比可忽略。想再降，只能动运行时文件或压缩档位。

---

## 2. 优化方案（按收益/风险排序）

### 2.1 afterPack 钩子剔除冗余运行时文件（预计安装包 -8~12MB）★ 首选

electron-builder 支持 `afterPack` 钩子：打包完成后、压缩成安装包之前，对 `win-unpacked` 目录做清理。对当前体积而言，可安全剔除的项：

| 剔除项 | 节省（解压后） | 说明 / 风险 |
|---|---|---|
| `LICENSES.chromium.html` | 19.4MB | 纯许可文本，运行时零引用；**零风险** |
| `dxcompiler.dll` / `dxil.dll` | 25.8MB | 本应用仅用 DOM/Canvas 渲染，不启用 WebGPU/D3D12 计算；**低风险**（需回归验证渲染正常） |
| `vk_swiftshader.dll` | 5.3MB | 软件 Vulkan 兜底，正常桌面有 GPU 不会加载；**中风险**（无独显/远程桌面环境可能黑屏） |

```js
// scripts/afterPack.mjs —— electron-builder afterPack 钩子（package.json → build.afterPack 指向）
import { rm } from 'node:fs/promises';
import path from 'node:path';

export default async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return;
  const appOutDir = context.appOutDir;
  const drop = [
    'LICENSES.chromium.html',     // 零风险
    'dxcompiler.dll', 'dxil.dll', // 低风险（未用 WebGPU）
    // 'vk_swiftshader.dll',       // 中风险，默认保留，按部署环境评估后开启
  ];
  for (const name of drop) {
    const p = path.join(appOutDir, name);
    try { await rm(p, { force: true }); console.log(`[afterPack] 已剔除 ${name}`); }
    catch (e) { console.warn(`[afterPack] 剔除失败 ${name}:`, e.message); }
  }
}
```

> 配置：`package.json` → `"build": { "afterPack": "scripts/afterPack.mjs" }`
> 原则【不易】：只删"确认无运行时引用"的文件；每删一项都必须安装版回归（启动 / 渲染 / detach / 流式对话）。

### 2.2 NSIS 压缩档位提到 maximum（预计安装包再 -1~3MB）

electron-builder 默认 `compression: normal`，可显式 `"compression": "maximum"`（LZMA 更狠的一档）：

```jsonc
// package.json → build
"compression": "maximum"
```

> 代价：打包时间 +1~2 分钟。收益：二进制（尤其 215MB 的 exe）压缩率略升。

### 2.3 启用差分增量更新（不减小首装包，但减小后续更新包）

当前已产出 `.blockmap`（96KB）。配套 `publish` 配置 + NSIS `differentialPackage` 后，用户升级时只下载差异部分（而非整包 91.5MB）。**首装体积不变**，但长期分发带宽/更新时间显著下降：

```jsonc
"nsis": { "differentialPackage": true }
```

### 2.4 评估 ffmpeg.dll 剔除（2.9MB，低收益）

本应用无音视频功能，`ffmpeg.dll` 实际不加载。可并入 afterPack 剔除；若未来加语音/视频再恢复。**低优先级**（收益小）。

### 2.5 不推荐的方向（说明原因）

| 方向 | 不推荐理由 |
|---|---|
| 换用更小的 Electron 发行版（如 `@electron/rebuild`、自定义 Chromium 裁剪） | Electron 官方无精简发行版；自行裁剪 Chromium 需维护编译链，远超收益 |
| 降级 Electron 大版本（如 43 → 37） | exe 体积差异 <5MB，且丢失新安全补丁与 API，得不偿失 |
| 删 `icudtl.dat` / `resources.pak` | ICU 与 Chromium 内置资源为运行必需，删除即崩溃 |

---

## 3. 预期收益汇总

| 方案 | 预计安装包节省 | 风险 |
|---|---|---|
| 2.1 afterPack（LICENSES + DX shader） | 8~12MB（91.5 → ~80MB） | 低（需回归渲染） |
| 2.1 追加 vk_swiftshader | +1~3MB | 中（无 GPU 环境黑屏） |
| 2.2 compression maximum | 1~3MB | 无（仅打包变慢） |
| 2.3 差分更新 | 首装不变，升级包大幅变小 | 无 |
| **合计（保守）** | **91.5MB → ~78-80MB** | 低 |

> 上限说明：215MB 的 Electron 主二进制 + 渲染必需 DLL 是硬成本，安装包 ~78MB 基本是此方案的工程极限。再往下需改产品形态（如纯 Web 交付，与桌面壳无关）。

---

## 4. 实施与回归清单

1. 写 `scripts/afterPack.mjs` + 配 `afterPack` 字段（先只剔 LICENSES，验证后再加 DX 项）；
2. 打包 → 安装版回归：启动、主窗口四面板渲染、detach 独立窗口、file:// 流式对话、`typeof window.electronAPI`；
3. 通过后加 `compression: maximum` 复测；
4. 评估 vk_swiftshader / ffmpeg 剔除（结合目标用户硬件环境）；
5. 差分更新（2.3）列入发布规划。
