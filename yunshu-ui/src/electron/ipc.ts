/**
 * IPC 通道常量与跨窗口消息类型
 * ------------------------------------------------
 * 三方共享契约（【不易】约束）：
 *   electron/main.ts    —— 主进程（监听 detach / 广播状态）
 *   electron/preload.ts —— 预加载（contextBridge 暴露）
 *   src/renderer 侧     —— 通过 window.electronAPI 使用
 * 本模块为纯 TS，不依赖 electron/node API，任何一方均可安全引用。
 */

export const IPC = {
  /** 渲染层请求：将面板分离为独立系统窗口 */
  DetachPanel: 'window:detach-panel',
  /** 跨窗口状态同步（主进程事件总线广播） */
  StateSync: 'window:state-sync',
  /** 渲染层查询：当前窗口元信息（主窗口 or 独立窗口） */
  WindowMeta: 'window:get-meta',
  /** 独立窗口启动时拉取分离瞬间的状态快照（补偿广播时序） */
  GetInitialState: 'window:get-initial-state',
} as const;

export type IpcChannel = (typeof IPC)[keyof typeof IPC];

/** 可被分离为独立窗口的面板 */
export const DETACHABLE_PANELS = {
  CHAT: 'chat',
  THINK: 'think',
  NAV: 'nav',
  CODE: 'code',
} as const;

export type DetachablePanelId = (typeof DETACHABLE_PANELS)[keyof typeof DETACHABLE_PANELS];

export interface DetachPanelRequest {
  /** 面板 ID（与 Mosaic 叶子节点一致） */
  panelId: DetachablePanelId;
  /** 独立窗口标题 */
  title: string;
  /** 独立窗口加载的 hash 路由，如 '/detached/chat' */
  route: string;
  /** 分离瞬间的状态快照，主进程暂存，供新窗口启动时拉取 */
  initialSnapshot?: StateSyncPayload;
}

export type WindowKind = 'main' | 'detached';

export interface WindowMeta {
  isElectron: boolean;
  kind: WindowKind;
  /** 独立窗口对应的面板 ID */
  detachedPanelId?: DetachablePanelId;
}

/**
 * 跨窗口状态同步载荷。
 * 采用"全量快照"策略：聊天消息与思考事件数据量小，
 * 全量同步实现最简单且天然一致，避免增量补丁的合并复杂度。
 */
export interface StateSyncPayload {
  type: 'snapshot';
  messages: unknown[];
  thinking: unknown[];
}
