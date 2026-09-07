/**
 * electron-builder beforePack 钩子 —— 打包前结束同名旧进程（productName=云枢）
 * ----------------------------------------------------------------------------
 * 背景：win-unpacked 运行时文件（如 ffmpeg.dll）被正在运行的旧版应用占用时，
 *       electron-builder 清理/覆盖会报 EPERM 导致打包失败。
 * 逻辑：Windows 下用 taskkill 结束同名进程树（找不到进程时忽略，不阻塞打包）。
 * 注意：钩子必须以「导出函数」形式提供（electron-builder 会调用模块默认导出）。
 */
import { execFileSync } from 'node:child_process';

export default async function beforePack() {
  if (process.platform === 'win32') {
    let killed = false;
    try {
      execFileSync('taskkill', ['/IM', '云枢.exe', '/F', '/T'], { stdio: 'ignore' });
      console.log('[beforePack] 已结束旧进程: 云枢.exe');
      killed = true;
    } catch {
      // taskkill 找不到目标进程时返回非零 → 无旧实例，忽略
    }
    // Windows 强杀进程后文件句柄释放有延迟，等待后再清空 win-unpacked，避免 EPERM
    if (killed) await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}
