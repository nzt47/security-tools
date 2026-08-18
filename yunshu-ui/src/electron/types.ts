/**
 * window.electronAPI 类型声明（renderer 侧）
 * ------------------------------------------------
 * 形状与 electron/preload.ts 中 contextBridge 暴露的 API 一一对应。
 * Web 环境下不存在 window.electronAPI，代码须判空（见 isElectron）。
 */
import type { DetachPanelRequest, StateSyncPayload, WindowMeta } from './ipc';

export interface WindowElectronAPI {
  /** 请求主进程将面板分离为独立系统窗口，resolve 新窗口 webContents.id */
  detachPanel(req: DetachPanelRequest): Promise<number>;
  /** 查询当前窗口元信息（主窗口 / 独立窗口 + 面板 ID） */
  getWindowMeta(): Promise<WindowMeta>;
  /** 独立窗口启动时拉取分离瞬间的状态快照（一次性，取后即清） */
  getInitialState(): Promise<StateSyncPayload | null>;
  /** 向其它窗口广播状态快照（主进程转发，不回传源窗口） */
  broadcastState(payload: StateSyncPayload): void;
  /** 订阅其它窗口的状态快照，返回取消订阅函数 */
  onStateSync(cb: (payload: StateSyncPayload) => void): () => void;
}

declare global {
  interface Window {
    electronAPI?: WindowElectronAPI;
  }
}

/** 当前是否运行在 Electron 渲染进程（Web 模式下无 electronAPI，自动降级） */
export function isElectron(): boolean {
  return typeof window !== 'undefined' && !!window.electronAPI;
}
