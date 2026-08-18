/**
 * Web 模式下的 Electron API Mock —— 本地联调"独立窗口同步"逻辑
 * ------------------------------------------------
 * 用法：构建/启动时设置 VITE_MOCK_ELECTRON=1，main.tsx 自动注入。
 * 机制：用 BroadcastChannel 模拟主进程事件总线（同 origin 多标签页互通），
 *       用 localStorage 暂存"分离瞬间快照"（新标签页加载完成后拉取）。
 *
 * 联调步骤（本地双标签页）：
 *   1. 启动：$env:VITE_MOCK_ELECTRON="1"; npm run dev
 *   2. 打开 http://localhost:5173/static/（主工作台）
 *   3. 点击任意面板工具条"独立窗口"按钮 → 自动打开 #/detached/<panelId> 新标签页
 *   4. 在主标签页发消息 → 150ms 内独立窗口标签页收到同步快照
 *   5. 在独立窗口标签页发消息 → 主标签页同步
 * 关闭：删除 localStorage 的 'yunshu:mock:*' 键或关闭标签页即可。
 */
import type { DetachPanelRequest, StateSyncPayload, WindowMeta } from './ipc';
import type { WindowElectronAPI } from './types';

const CHANNEL = 'yunshu:mock-ipc';
const SNAPSHOT_KEY = 'yunshu:mock:initial-snapshot';

/** 注入 mock API（若已有真实 electronAPI 则不覆盖），返回是否注入成功 */
export function installMockElectron(): boolean {
  if (typeof window === 'undefined' || window.electronAPI) return false;
  window.electronAPI = createMockAPI();
  console.info('[云枢·Mock] Electron API 已注入（Web 双标签页联调模式）');
  return true;
}

function createMockAPI(): WindowElectronAPI {
  // 模拟主进程状态总线：同 origin 所有标签页共享此通道
  const channel = new BroadcastChannel(CHANNEL);

  return {
    /** 模拟 detach：暂存快照 + 新标签页打开独立窗口路由 */
    async detachPanel(req: DetachPanelRequest): Promise<number> {
      localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(req.initialSnapshot ?? null));
      // 与 Electron 主窗口相对路径一致：同页签下追加 hash 路由
      const url = `${location.pathname}#/detached/${req.panelId}`;
      window.open(url, '_blank');
      // mock 窗口 id（真实场景为主进程 webContents.id）
      return Date.now() + Math.floor(Math.random() * 1000);
    },

    async getWindowMeta(): Promise<WindowMeta> {
      const hash = window.location.hash;
      const isDetached = hash.startsWith('#/detached/');
      return {
        isElectron: true,
        kind: isDetached ? 'detached' : 'main',
        detachedPanelId: isDetached
          ? (hash.slice('#/detached/'.length) as WindowMeta['detachedPanelId'])
          : undefined,
      };
    },

    /** 模拟主进程暂存快照：新窗口启动时一次性拉取 */
    async getInitialState(): Promise<StateSyncPayload | null> {
      const raw = localStorage.getItem(SNAPSHOT_KEY);
      localStorage.removeItem(SNAPSHOT_KEY);
      if (!raw) return null;
      try {
        return JSON.parse(raw) as StateSyncPayload;
      } catch {
        return null;
      }
    },

    /** 广播状态快照（模拟主进程转发；本标签页也会收到，由 sync.ts 的 JSON 对比防回环） */
    broadcastState(payload: StateSyncPayload): void {
      channel.postMessage(payload);
    },

    onStateSync(cb: (payload: StateSyncPayload) => void): () => void {
      const listener = (e: MessageEvent<StateSyncPayload>) => cb(e.data);
      channel.addEventListener('message', listener);
      return () => channel.removeEventListener('message', listener);
    },
  };
}
