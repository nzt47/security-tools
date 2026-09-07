/**
 * 开发模式快捷键：Ctrl+R（Win/Linux）/ Cmd+R（macOS）刷新当前窗口
 * ------------------------------------------------
 * 仅在有 Vite dev server 时启用；生产环境（file:// 加载）刷新无意义，且避免误触清空状态。
 * 独立成模块：不依赖 electron 运行时（仅类型引用，编译期擦除），
 * 通过注入的 webContents 交互，便于单元测试。
 */
import type { BrowserWindow } from 'electron';

export function registerDevReloadShortcut(win: BrowserWindow): void {
  const isDev =
    !!process.env['VITE_DEV_SERVER_URL'] || !!process.env['ELECTRON_RENDERER_URL'];
  if (!isDev) return;

  win.webContents.on('before-input-event', (event, input) => {
    if ((input.control || input.meta) && input.key.toLowerCase() === 'r') {
      event.preventDefault();
      console.log(
        JSON.stringify({
          ts: new Date().toISOString(),
          level: 'info',
          module: 'main',
          event: 'reload-shortcut-triggered',
        }),
      );
      win.webContents.reload();
    }
  });
}
