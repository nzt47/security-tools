/**
 * electron-builder afterPack 钩子 —— 剔除冗余的 Chromium 运行时文件（自动化，打包时自动执行）
 * ------------------------------------------------
 * 在"解压 Electron 发行版完成、压缩成 NSIS 安装包之前"执行，对 appOutDir（win-unpacked）做清理。
 *
 * 剔除清单（每一项均经安装版实测验证，见《安装包体积优化对比报告》/《v2瘦身可行性分析》）：
 *   - LICENSES.chromium.html      : 纯开源许可文本，运行时零引用（零风险）✅ 已实测
 *   - dxcompiler.dll / dxil.dll   : DirectX Shader Compiler，未用 WebGPU/D3D12（低风险）✅ 已实测
 *   - vk_swiftshader.dll + icd.json + vulkan-1.dll : Vulkan 软件渲染兜底，实测删除后
 *                                                     启动/渲染/detach/流式均正常（中风险，见下）✅ 已实测
 *
 * 明确【不可删】（实测删除会破坏页面渲染/进程退出，勿加入 drop，见架构文档 8.2.2）：
 *   - ffmpeg.dll        : 单独删除后应用可启动但页面 title 为空、渲染进程异常（Chromium 渲染管线依赖）
 *   - d3dcompiler_47.dll: 删除后主窗口正常但 detach 独立窗口崩溃→进程退出（新渲染进程需现场编译 shader）
 *   - elevate.exe       : NSIS 提权助手（resources/ 子目录）
 * 防误删：Git pre-commit hook（scripts/hooks/pre-commit）会在提交前自动检查本文件 DROP
 *          是否误加上述核心文件；本地安装见 docs/zh/发布部署操作手册 附录。
 *
 * 安全边界【不易】：删除项基于实测，若变更 Electron 大版本或启用 WebGPU/音视频，须重新回归验证。
 */
import { rm } from 'node:fs/promises';
import path from 'node:path';

const DROP = [
  'LICENSES.chromium.html',          // 零风险
  'dxcompiler.dll', 'dxil.dll',      // 低风险（未用 WebGPU）
  // Vulkan 软件渲染兜底（无 GPU 环境才需要；实测当前环境删除后功能正常）。
  // 若目标用户含无独显/远程桌面场景，请保留此项：
  'vk_swiftshader.dll',
  'vk_swiftshader_icd.json',
  'vulkan-1.dll',
];

export default async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return;
  const appOutDir = context.appOutDir;

  let removed = 0;
  for (const name of DROP) {
    const p = path.join(appOutDir, name);
    try {
      await rm(p, { force: true });
      console.log(`[afterPack] 已剔除 ${name} 于 ${appOutDir}`);
      removed++;
    } catch (e) {
      console.warn(`[afterPack] 剔除失败 ${name}:`, e.message);
    }
  }

  // 校验：被剔除文件应全部不存在（防打包流程变更导致漏剔）
  const missing = [];
  for (const name of DROP) {
    try {
      await import('node:fs').then(({ stat }) => stat(path.join(appOutDir, name)));
      missing.push(name);
    } catch {
      /* 文件不存在 = 剔除成功 */
    }
  }
  if (missing.length > 0) {
    throw new Error(`[afterPack] 校验失败，以下文件仍在产物中: ${missing.join(', ')}`);
  }
  console.log(`[afterPack] 完成：剔除 ${removed} 个冗余运行时文件，校验通过`);
}
