/**
 * WorkbenchApp —— 云枢 Mosaic 工作台根组件（Web + Electron 双模式）
 * ------------------------------------------------
 * 布局模型：MosaicNode 是一棵纯数据树（叶子=面板ID，分支=split/tabs），
 * 因此天然可序列化。链路：
 *   Mosaic.onChange（拖拽/拆分/调整大小）→ useLayoutStore.setLayout
 *   → Zustand persist 中间件写入 LocalStorage → 刷新后 sanitizeLayout 校验恢复
 *
 * Electron 面板分离（detach）：
 *   1. 拖拽面板到窗口边缘 → 原生 dragover（捕获阶段）检测 → 显示停靠高亮
 *   2. 在边缘松开（drop，捕获阶段）→ 拦截默认 DOM 拖放 → IPC detach-panel
 *   3. 主进程创建独立 BrowserWindow 加载 #/detached/<panelId>
 *   4. 本窗口从布局树摘除该面板；状态经 IPC 快照同步到新窗口
 *   5. 工具栏"独立窗口"按钮是同样逻辑的兜底入口（Web 下隐藏）
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Mosaic, MosaicWindow, ExpandButton, RemoveButton } from 'react-mosaic-component';
import type { MosaicNode, MosaicPath } from 'react-mosaic-component';
import { Cloud, FlaskConical, RotateCcw } from 'lucide-react';
import {
  DEFAULT_LAYOUT,
  PANEL,
  PANEL_TITLES,
  removePanelFromLayout,
} from './lib/mosaic';
import type { PanelId } from './lib/mosaic';
import { useLayoutStore } from './stores/useLayoutStore';
import { SidebarPanel } from './components/workbench/panels/SidebarPanel';
import { ChatPanel } from './components/workbench/panels/ChatPanel';
import { ThinkingPanel } from './components/workbench/panels/ThinkingPanel';
import { CodeEditorPanel } from './components/workbench/panels/CodeEditorPanel';
import { DetachButton } from './components/workbench/DetachButton';
import { isElectron } from './electron/types';
import type { DetachablePanelId } from './electron/ipc';
import './styles/workbench.css';

/** 拖拽面板时写入当前被拖拽的源面板 ID（MosaicWindow onDragStart 回调维护） */
let dragSourcePanelId: PanelId | null = null;

/** 拆分面板时生成新节点：暂以"复制当前面板"占位（真实业务可改为新建会话面板） */
const createNode = (viewId: PanelId): MosaicNode<PanelId> => ({
  type: 'split',
  direction: 'row',
  children: [viewId, viewId],
  splitPercentages: [50, 50],
});

function renderPanel(id: PanelId) {
  switch (id) {
    case PANEL.NAV:
      return <SidebarPanel />;
    case PANEL.THINK:
      return <ThinkingPanel />;
    case PANEL.CODE:
      return <CodeEditorPanel />;
    case PANEL.CHAT:
    default:
      return <ChatPanel />;
  }
}

export default function WorkbenchApp() {
  const layout = useLayoutStore((s) => s.layout);
  const setLayout = useLayoutStore((s) => s.setLayout);
  const resetLayout = useLayoutStore((s) => s.resetLayout);

  // 边缘停靠区高亮（拖拽面板到窗口边缘时显示）
  const [detachEdge, setDetachEdge] = useState<'top' | 'bottom' | 'left' | 'right' | null>(null);
  const edgeRef = useRef(detachEdge);
  edgeRef.current = detachEdge;

  /** 将面板分离为独立系统窗口（Electron），成功后在主窗口布局中摘除该面板 */
  // useCallback 保持引用稳定（依赖仅 store 稳定函数），供边缘拖拽 effect 安全引用
  const detachPanel = useCallback(async (panelId: PanelId) => {
    if (!isElectron()) return;
    const api = window.electronAPI!;
    const state = useLayoutStore.getState();
    await api.detachPanel({
      panelId: panelId as DetachablePanelId,
      title: `${PANEL_TITLES[panelId]} · 独立窗口`,
      route: `/detached/${panelId}`,
      initialSnapshot: { type: 'snapshot', messages: state.messages, thinking: state.thinking },
    });
    // 摘除面板：单子上提 / tabs 折叠由 removePanelFromLayout 处理。
    // 以实际渲染的布局树为基准：layout 未自定义（null）时用 DEFAULT_LAYOUT 兜底，
    // 否则 setLayout(null) 不产生变化，面板不会被移除。
    const next = removePanelFromLayout(useLayoutStore.getState().layout ?? DEFAULT_LAYOUT, panelId);
    console.info(`[mosaic] 面板分离成功，从布局摘除：${panelId} → 新布局 ${JSON.stringify(next)}`);
    setLayout(next);
  }, [setLayout]);

  // ─── 边缘拖拽拦截：原生 dragover/drop 捕获阶段先于 react-dnd 执行 ───
  useEffect(() => {
    const EDGE_PX = 48; // 距窗口边缘多少像素视为停靠区

    const onDragOver = (e: DragEvent) => {
      if (!dragSourcePanelId) return;
      const nearTop = e.clientY < EDGE_PX;
      const nearBottom = e.clientY > window.innerHeight - EDGE_PX;
      const nearLeft = e.clientX < EDGE_PX;
      const nearRight = e.clientX > window.innerWidth - EDGE_PX;
      const edge = nearLeft ? 'left' : nearRight ? 'right' : nearTop ? 'top' : nearBottom ? 'bottom' : null;
      setDetachEdge(edge);
      if (edge) e.preventDefault(); // 阻止浏览器默认的"拖放导航"
    };

    const onDrop = (e: DragEvent) => {
      if (edgeRef.current && dragSourcePanelId) {
        e.preventDefault(); // 拦截默认 DOM 拖放 → 转入 IPC detach
        const panelId = dragSourcePanelId;
        console.info(`[mosaic] 边缘停靠触发（${edgeRef.current}），面板 ${panelId} 转入独立窗口流程`);
        setDetachEdge(null);
        void detachPanel(panelId);
      }
    };

    const onDragEnd = () => {
      setDetachEdge(null);
      dragSourcePanelId = null;
    };

    // 捕获阶段监听：确保先于 react-dnd（冒泡阶段）的拖放处理
    window.addEventListener('dragover', onDragOver, true);
    window.addEventListener('drop', onDrop, true);
    window.addEventListener('dragend', onDragEnd, true);
    return () => {
      window.removeEventListener('dragover', onDragOver, true);
      window.removeEventListener('drop', onDrop, true);
      window.removeEventListener('dragend', onDragEnd, true);
    };
  }, [detachPanel]);

  /** 渲染单个面板窗口：记录拖拽源 + 工具条（含"独立窗口"按钮） */
  const renderTile = (id: PanelId, path: MosaicPath) => (
    <MosaicWindow<PanelId>
      path={path}
      title={PANEL_TITLES[id]}
      toolbarControls={[
        <DetachButton key="detach" panelId={id} onDetached={detachPanel} />,
        <ExpandButton key="expand" />,
        <RemoveButton key="remove" />,
      ]}
      createNode={createNode}
      onDragStart={() => {
        dragSourcePanelId = id;
        console.info(`[mosaic] 面板拖拽开始：${PANEL_TITLES[id]}（${id}）`);
      }}
      onDragEnd={() => {
        dragSourcePanelId = null;
        console.info(`[mosaic] 面板拖拽结束：${id}`);
      }}
    >
      {renderPanel(id)}
    </MosaicWindow>
  );

  return (
    <div className="workbench-root">
      {/* 顶栏 */}
      <header className="wb-topbar">
        <div className="flex items-center gap-2.5">
          <div className="wb-logo-badge">
            <Cloud size={15} />
          </div>
          <span className="wb-brand-title">云枢工作台</span>
          <span className="wb-status-pill">
            <span className="wb-pulse-dot" />
            {isElectron() ? '桌面模式' : 'Web 模式'}
          </span>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[11px] text-slate-500 sm:inline">
            {isElectron() ? '拖拽面板到窗口边缘可分离为独立窗口' : '拖拽面板边框拆分 · 布局自动保存'}
          </span>
          <button type="button" className="wb-reset-btn" onClick={() => { window.location.hash = '#/prompt-lab'; }} title="提示词影响因素管理面板">
            <FlaskConical size={13} />
            提示词实验室
          </button>
          <button type="button" className="wb-reset-btn" onClick={resetLayout} title="恢复默认布局">
            <RotateCcw size={13} />
            重置布局
          </button>
        </div>
      </header>

      {/* Mosaic 工作区 */}
      <div className="wb-mosaic-body">
        {/* 边缘停靠高亮 */}
        {detachEdge && (
          <div className={`wb-detach-zone wb-detach-zone-${detachEdge}`}>
            <span>分离为独立窗口</span>
          </div>
        )}

        <Mosaic<PanelId>
          className="mosaic-custom"
          value={layout ?? DEFAULT_LAYOUT}
          onChange={(next) => {
            // 【Why】拖拽/拆分/关闭面板都会触发：记录布局树变化，配合 [mosaic] 其它日志
            // 排查 removeChild 等面板生命周期告警（dev 下可 Console 过滤 [mosaic]）。
            console.info(`[mosaic] 布局变更：${JSON.stringify(next)}`);
            setLayout(next);
          }}
          renderTile={renderTile}
          resize={{ minimumPaneSizePercentage: 12 }}
          zeroStateView={
            <div className="flex h-full flex-col items-center justify-center gap-3">
              <p className="text-sm text-slate-400">所有面板已关闭</p>
              <button type="button" className="wb-reset-btn" onClick={resetLayout}>
                <RotateCcw size={13} />
                恢复默认布局
              </button>
            </div>
          }
        />
      </div>
    </div>
  );
}
