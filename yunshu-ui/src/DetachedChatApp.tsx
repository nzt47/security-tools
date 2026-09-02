/**
 * DetachedChatApp —— 独立窗口视图（#/detached/<panelId>）
 * ------------------------------------------------
 * 由 Electron 主进程为"面板分离"创建的新 BrowserWindow 加载本视图，
 * 只渲染被分离的单个面板，冷启动资源最小化（配合 Vite 代码分割）。
 *
 * 面板渲染与主工作台统一（缺陷 ③）：CHAT→ContentPanel / NAV→NavPanel /
 * THINK→ThinkingPanel / CODE→CodeEditorPanel，统一走 renderPanel 单一映射，
 * 独立窗口不再渲染与主工作台不一致的 ChatPanel / SidebarPanel 占位。
 *
 * 数据一致性：
 *  - 启动时经 IPC 拉取分离瞬间的状态快照（getInitialState）
 *  - 之后由 startCrossWindowSync 持续双向同步（主进程事件总线，mock 下为
 *    BroadcastChannel 跨标签页总线），与主工作台会话（messages/thinking）一致
 * Web 环境直接访问本路由时（#/detached/chat），无 IPC 可用，自动降级为空状态。
 */
import { useEffect } from 'react';
import { X } from 'lucide-react';
import { renderPanel } from './components/workbench/panels/renderPanel';
import { useLayoutStore } from './stores/useLayoutStore';
import type { ChatMessage, ThinkingEvent } from './stores/useLayoutStore';
import { PANEL } from './lib/mosaic';
import type { PanelId } from './lib/mosaic';
import { isElectron } from './electron/types';
import { startCrossWindowSync } from './electron/sync';
import './styles/workbench.css';

const PANEL_LABEL: Record<PanelId, string> = {
  [PANEL.CHAT]: '对话',
  [PANEL.THINK]: '思考过程',
  [PANEL.NAV]: '导航',
  [PANEL.CODE]: '代码编辑器',
};

export function DetachedChatApp({ panelId }: { panelId: PanelId }) {
  // 启动跨窗口同步 + 拉取分离瞬间快照。
  // 【Why】不设"仅首次"标志：StrictMode（dev）会先跑 effect→cleanup→再跑 effect，
  // 若用 ref 跳过第二次，cleanup 后同步不会重建，导致窗口间失去实时一致；
  // 直接每次返回 cleanup，重跑时先清理再重建。getInitialState 为取后即清（幂等），
  // 二次调用返回 null，快照恰好应用一次。
  useEffect(() => {
    const cleanup = startCrossWindowSync();
    if (isElectron()) {
      window.electronAPI!
        .getInitialState()
        .then((snapshot) => {
          if (snapshot?.type === 'snapshot') {
            // 快照可能早于本地类型定义，运行时形状一致（见 ipc.ts 契约）
            useLayoutStore.setState({
              messages: snapshot.messages as ChatMessage[],
              thinking: snapshot.thinking as ThinkingEvent[],
            });
          }
        })
        .catch((err) => console.error('[云枢] 拉取初始快照失败:', err));
    }
    return cleanup;
  }, []);

  return (
    <div className="workbench-root">
      {/* 迷你顶栏：独立窗口标识 + 关闭窗口 */}
      <header className="wb-topbar">
        <div className="flex items-center gap-2.5">
          <div className="wb-logo-badge">
            <span className="text-[12px] font-semibold">枢</span>
          </div>
          <span className="wb-brand-title">云枢 · {PANEL_LABEL[panelId]}</span>
          <span className="wb-status-pill">
            <span className="wb-pulse-dot" />
            独立窗口
          </span>
        </div>
        {isElectron() && (
          <button
            type="button"
            className="wb-reset-btn"
            onClick={() => window.close()}
            title="关闭独立窗口"
          >
            <X size={13} />
            关闭窗口
          </button>
        )}
      </header>

      {/* 被分离的面板内容（与主工作台同一渲染映射） */}
      <div className="wb-detached-body">{renderPanel(panelId)}</div>
    </div>
  );
}
