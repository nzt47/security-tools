/**
 * registerDevReloadShortcut 单元测试
 * ------------------------------------------------
 * 验证：开发模式下注册 before-input-event；Ctrl+R / Cmd+R 触发刷新；
 * 非 R 键、无修饰键不触发；生产模式（无 dev 环境变量）不注册。
 * 通过注入 fake webContents 隔离，无需真实 Electron 运行时。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BrowserWindow } from 'electron';
import { registerDevReloadShortcut } from './devShortcut';

interface FakeWindow {
  win: BrowserWindow;
  /** 注册的事件名 → 处理器（测试中手动触发） */
  handlers: Map<string, (event: unknown, input: unknown) => void>;
}

/** 构造带 webContents 的假窗口，并捕获 before-input-event 处理器 */
function createFakeWindow(): FakeWindow {
  const handlers = new Map<string, (event: unknown, input: unknown) => void>();
  const win = {
    webContents: {
      on: vi.fn((event: string, handler: (event: unknown, input: unknown) => void) => {
        handlers.set(event, handler);
      }),
      reload: vi.fn(),
    },
  } as unknown as BrowserWindow;
  return { win, handlers };
}

/** 触发 before-input-event 处理器，返回其 preventDefault mock */
function fireKey(
  handlers: FakeWindow['handlers'],
  input: Record<string, unknown>,
): { preventDefault: ReturnType<typeof vi.fn> } {
  const handler = handlers.get('before-input-event');
  if (!handler) throw new Error('before-input-event 未注册');
  const preventDefault = vi.fn();
  handler({ preventDefault }, input);
  return { preventDefault };
}

describe('registerDevReloadShortcut', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('开发模式（VITE_DEV_SERVER_URL）下注册 before-input-event 监听', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    expect(handlers.has('before-input-event')).toBe(true);
  });

  it('兼容旧环境变量名 ELECTRON_RENDERER_URL', () => {
    vi.stubEnv('ELECTRON_RENDERER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    expect(handlers.has('before-input-event')).toBe(true);
  });

  it('Ctrl+R 触发刷新并阻止默认行为', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    const { preventDefault } = fireKey(handlers, { control: true, key: 'r' });
    expect(win.webContents.reload).toHaveBeenCalledTimes(1);
    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it('Cmd+R（macOS）触发刷新，且 key 大小写不敏感', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    fireKey(handlers, { meta: true, key: 'R' });
    expect(win.webContents.reload).toHaveBeenCalledTimes(1);
  });

  it('Ctrl+非 R 键不触发刷新', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    fireKey(handlers, { control: true, key: 'a' });
    expect(win.webContents.reload).not.toHaveBeenCalled();
  });

  it('裸 R（无修饰键）不触发刷新', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', 'http://localhost:5173');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    fireKey(handlers, { key: 'r' });
    expect(win.webContents.reload).not.toHaveBeenCalled();
  });

  it('生产模式（无 dev 环境变量）不注册快捷键', () => {
    vi.stubEnv('VITE_DEV_SERVER_URL', '');
    vi.stubEnv('ELECTRON_RENDERER_URL', '');
    const { win, handlers } = createFakeWindow();
    registerDevReloadShortcut(win);
    expect(handlers.size).toBe(0);
    expect(win.webContents.on).not.toHaveBeenCalled();
  });
});
