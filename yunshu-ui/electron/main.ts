/**
 * Electron 主进程 —— 云枢桌面壳
 * ------------------------------------------------
 * 职责：
 *   1. 创建主窗口（安全配置：contextIsolation / nodeIntegration 关闭）
 *   2. 监听 IPC 'window:detach-panel' → 动态创建独立 BrowserWindow
 *   3. 作为跨窗口状态总线：StateSync 广播给除源窗口外的所有窗口
 *   4. 维护窗口元信息（主窗口 / 独立窗口 + 面板 ID），供 renderer 查询
 *
 * 安全基线：任何新建窗口都走同一份安全 webPreferences。
 */
import { app, BrowserWindow, ipcMain, type WebContents } from 'electron';
import { appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { registerDevReloadShortcut } from './devShortcut';
import { IPC } from '../src/electron/ipc';
import type {
  DetachPanelRequest,
  DetachablePanelId,
  WindowKind,
  StateSyncPayload,
} from '../src/electron/ipc';

// ESM 产物（package.json "type": "module"）没有 __dirname，用 import.meta 派生
const __dirname = import.meta.dirname ?? process.cwd();

/**
 * 生产标准 userData 路径（【不易】约束：数据目录是持久化锚点，须稳定不随包名漂移）
 * ------------------------------------------------
 * Electron 默认 userData = %APPDATA%/<package.json name>，本包名是 "Yunshu-ui"，
 * 会导致 localStorage/缓存落在 AppData\Roaming\Yunshu-ui（命名不规范、且改名即丢数据）。
 * 显式固定为 %APPDATA%\云枢，与产品名一致：
 *  - localStorage（Mosaic 布局 / 代码编辑器内容）→ AppData\Roaming\云枢\Local Storage
 *  - 必须在 app ready 前调用（Chromium 在首窗创建时按此路径初始化存储）
 * 注：验证环境若以 --user-data-dir 命令行参数启动，Chromium 会覆盖此配置（仅测试用）。
 */
app.setPath('userData', path.join(app.getPath('appData'), '云枢'));

/** 独立窗口元信息：webContents.id → 面板 ID（用于 WindowMeta 查询与去重） */
const detachedWindows = new Map<number, DetachablePanelId>();

/** 分离瞬间的状态快照：webContents.id → snapshot（新窗口启动时一次性拉取，补偿广播时序） */
const pendingInitialState = new Map<number, StateSyncPayload>();

/** 日志目录（app ready 后初始化）；为空表示文件不可用，降级为仅控制台输出 */
let logDir = '';

/** 初始化日志目录：%APPDATA%\云枢\logs（与 userData 同根，生命周期随应用数据目录） */
function initLogDir() {
  try {
    logDir = path.join(app.getPath('userData'), 'logs');
    mkdirSync(logDir, { recursive: true });
  } catch {
    logDir = ''; // 目录创建失败不阻塞主流程
  }
}

/**
 * 统一结构化日志：stdout 单行 JSON + 落盘 %APPDATA%\云枢\logs\app-yyyy-MM-dd.log（按天轮转）
 * 格式固定为：{ts, level, module, event, ...自定义字段}，所有主进程日志必须走此函数。
 */
function log(level: 'info' | 'error', event: string, fields: Record<string, unknown> = {}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), level, module: 'main', event, ...fields });
  console.log(line);
  if (logDir) {
    try {
      const day = new Date().toISOString().slice(0, 10);
      appendFileSync(path.join(logDir, `app-${day}.log`), `${line}\n`, 'utf8');
    } catch {
      // 写文件失败不影响主流程（磁盘满/权限变更等）
    }
  }
}

/** 统一安全窗口配置（【不易】约束：所有窗口必须一致） */
function secureWebPreferences(): Electron.WebPreferences {
  return {
    preload: path.join(__dirname, 'preload.cjs'), // CJS 产物（见 vite.config.ts preload 配置）
    contextIsolation: true, // 渲染层与 preload 隔离
    nodeIntegration: false, // 渲染层禁用 Node
    sandbox: false, // preload 需要 require electron（sandbox 下仅限内置模块）
  };
}

/** 加载前端：dev 走 Vite dev server（HMR），prod 走打包产物 */
function loadRenderer(win: BrowserWindow, hashRoute?: string) {
  // vite-plugin-electron 1.x 注入 VITE_DEV_SERVER_URL（0.x 为 ELECTRON_RENDERER_URL，兜底兼容）
  const devUrl = process.env['VITE_DEV_SERVER_URL'] ?? process.env['ELECTRON_RENDERER_URL'];
  if (devUrl) {
    const target = hashRoute ? `${devUrl}#${hashRoute}` : devUrl;
    log('info', 'renderer-loading', { mode: 'dev', url: target });
    void win.loadURL(target);
  } else {
    const filePath = path.join(__dirname, '../dist/index.html');
    log('info', 'renderer-loading', { mode: 'prod', url: `file://${filePath}${hashRoute ? `#${hashRoute}` : ''}` });
    void win.loadFile(filePath, { hash: hashRoute });
  }

  // 加载结果打点：失败时输出错误码/描述（定位白屏、路径错误），成功时输出最终 URL
  win.webContents.on('did-fail-load', (_e, code, description, url) => {
    log('error', 'renderer-load-failed', { code, description, url });
  });
  win.webContents.on('did-finish-load', () => {
    log('info', 'renderer-loaded', { url: win.webContents.getURL() });
  });
}

function createMainWindow() {
  console.log(`[main] 创建主窗口 (1440x900, title=云枢 · 工作台)`);
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 980,
    minHeight: 640,
    title: '云枢 · 工作台',
    backgroundColor: '#04060d',
    webPreferences: secureWebPreferences(),
  });
  loadRenderer(win);
  registerDevReloadShortcut(win); // 所有主窗口（含 macOS activate 重建）统一注册
  return win;
}

/** 创建独立面板窗口（面板 detach 到系统级窗口） */
function createDetachedWindow(req: DetachPanelRequest): number {
  const win = new BrowserWindow({
    width: 720,
    height: 860,
    title: req.title,
    backgroundColor: '#04060d',
    webPreferences: secureWebPreferences(),
  });
  loadRenderer(win, req.route);

  const wcId = win.webContents.id;
  detachedWindows.set(wcId, req.panelId as DetachablePanelId);
  // 暂存分离瞬间快照，供新窗口 renderer 启动时通过 GetInitialState 拉取
  if (req.initialSnapshot) {
    pendingInitialState.set(wcId, req.initialSnapshot);
  }

  win.on('closed', () => {
    detachedWindows.delete(wcId);
    pendingInitialState.delete(wcId);
  });
  return wcId;
}

/** 判断 webContents 对应的窗口种类 */
function windowKindOf(wc: WebContents): { kind: WindowKind; panelId?: DetachablePanelId } {
  if (detachedWindows.has(wc.id)) {
    return { kind: 'detached', panelId: detachedWindows.get(wc.id) };
  }
  return { kind: 'main' };
}

function registerIpcHandlers() {
  // ── 面板分离：渲染层拖拽到边缘/停靠区后发起的 detach 请求 ──
  ipcMain.handle(IPC.DetachPanel, (event, req: DetachPanelRequest) => {
    // 请求参数校验（白名单面板 ID + 合法路由前缀），防渲染层注入非法值
    const panelId = ['chat', 'think', 'nav', 'code'].includes(req?.panelId) ? req.panelId : null;
    const route = typeof req?.route === 'string' && req.route.startsWith('/detached/')
      ? req.route
      : null;
    if (!panelId || !route) {
      throw new Error(`非法的 detach 请求: ${JSON.stringify(req)}`);
    }
    console.log(`[main] 分离面板 → 新窗口: ${panelId} @ ${route}`);
    return createDetachedWindow({ panelId, title: req.title || panelId, route });
  });

  // ── 跨窗口状态总线：转发快照给除源窗口外的所有窗口 ──
  ipcMain.on(IPC.StateSync, (event, payload: StateSyncPayload) => {
    const sourceId = event.sender.id;
    for (const win of BrowserWindow.getAllWindows()) {
      const wc = win.webContents;
      if (wc.id !== sourceId && !wc.isDestroyed()) {
        wc.send(IPC.StateSync, payload);
      }
    }
  });

  // ── 窗口元信息查询 ──
  ipcMain.handle(IPC.WindowMeta, (event) => {
    const { kind, panelId } = windowKindOf(event.sender);
    return { isElectron: true, kind, detachedPanelId: panelId };
  });

  // ── 独立窗口启动时拉取分离瞬间快照（一次性） ──
  ipcMain.handle(IPC.GetInitialState, (event): StateSyncPayload | null => {
    const snapshot = pendingInitialState.get(event.sender.id);
    pendingInitialState.delete(event.sender.id);
    return snapshot ?? null;
  });
}

// 主窗口关闭时退出应用（独立窗口关闭不影响，直到主窗口关闭）
app.on('window-all-closed', () => {
  app.quit();
});

app.whenReady().then(() => {
  initLogDir();
  registerIpcHandlers();
  createMainWindow();

  // macOS 惯例：点击 Dock 图标无窗口时重建主窗口
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});
