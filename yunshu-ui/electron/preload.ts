/**
 * 预加载脚本：contextBridge 安全暴露窗口管理 API
 * ------------------------------------------------
 * 安全基线（【不易】约束）：
 *   - contextIsolation: true   —— 渲染层与 preload 隔离，无法直接访问 Node
 *   - nodeIntegration: false   —— 渲染层禁用 Node 集成
 *   - 只暴露白名单 API，不暴露 ipcRenderer 本体（防任意 channel 注入）
 */
import { contextBridge, ipcRenderer } from 'electron';
import { IPC } from '../src/electron/ipc';
import type {
  DetachPanelRequest,
  StateSyncPayload,
  WindowMeta,
} from '../src/electron/ipc';

/** 通过 contextBridge 暴露给渲染层的 API 形状（renderer 侧声明见 src/electron/types.ts） */
const electronAPI = {
  /** 请求主进程将面板分离为独立系统窗口，返回新窗口 id */
  detachPanel: (req: DetachPanelRequest): Promise<number> =>
    ipcRenderer.invoke(IPC.DetachPanel, req),

  /** 查询当前窗口元信息（主窗口 / 独立窗口 / 面板 ID） */
  getWindowMeta: (): Promise<WindowMeta> => ipcRenderer.invoke(IPC.WindowMeta),

  /** 独立窗口启动时拉取分离瞬间的状态快照（一次性，取后即清） */
  getInitialState: (): Promise<StateSyncPayload | null> =>
    ipcRenderer.invoke(IPC.GetInitialState),

  /** 向其它窗口广播状态快照（主进程作为事件总线转发，不回传源窗口） */
  broadcastState: (payload: StateSyncPayload): void =>
    ipcRenderer.send(IPC.StateSync, payload),

  /** 订阅其它窗口广播的状态快照，返回取消订阅函数 */
  onStateSync: (cb: (payload: StateSyncPayload) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, payload: StateSyncPayload) => {
      cb(payload);
    };
    ipcRenderer.on(IPC.StateSync, listener);
    return () => {
      ipcRenderer.removeListener(IPC.StateSync, listener);
    };
  },
};

export type ElectronAPI = typeof electronAPI;

contextBridge.exposeInMainWorld('electronAPI', electronAPI);
