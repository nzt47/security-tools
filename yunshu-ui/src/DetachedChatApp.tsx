/**
 * DetachedChatApp —— 独立窗口视图（#/detached/<panelId>）
 * ------------------------------------------------
 * 由 Electron 主进程为"面板分离"创建的新 BrowserWindow 加载本视图，
 * 只渲染被分离的单个面板，冷启动资源最小化（配合 Vite 代码分割）。
 *
 * 数据一致性：
 *  - 启动时经 IPC 拉取分离瞬间的状态快照（getInitialState）
 *  - 之后由 startCrossWindowSync 持续双向同步（主进程事件总线）
 * Web 环境直接访问本路由时（#/detached/chat），无 IPC 可用，自动降级为空状态。
 */
import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { ChatPanel } from './components/workbench/panels/ChatPanel';
import { ThinkingPanel } from './components/workbench/panels/ThinkingPanel';
import { SidebarPanel } from './components/workbench/panels/SidebarPanel';
import { CodeEditorPanel } from './components/workbench/panels/CodeEditorPanel';
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

function renderDetachedPanel(panelId: PanelId) {
  switch (panelId) {
    case PANEL.THINK:
      return <ThinkingPanel />;
    case PANEL.NAV:
      return <SidebarPanel />;
    case PANEL.CODE:
      return <CodeEditorPanel />;
    case PANEL.CHAT:
    default:
      return <ChatPanel />;
  }
}

export function DetachedChatApp({ panelId }: { panelId: PanelId }) {
  const synced = useRef(false);

  // 启动跨窗口同步 + 拉取分离瞬间快照（仅首次）
  useEffect(() => {
    if (synced.current) return;
    synced.current = true;

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

      {/* 被分离的面板内容 */}
      <div className="wb-detached-body">{renderDetachedPanel(panelId)}</div>
    </div>
  );
}
