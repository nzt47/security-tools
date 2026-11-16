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
 *   5. 面板右上角 ⋮ 菜单里的"独立窗口"是同样逻辑的兜底入口（Web 下隐藏）
 *
 * 界面 chrome（2026-11-15 B 步，对齐 DeepSeek Harness 观感）：
 *   - 面板标题栏整条去掉 ⇒ 只剩 6px 抓取条（hover 才显形），它替标题栏当 drag source；
 *   - 三个面板动作（独立窗口/放大/关闭）收进右上角悬浮 ⋮ 菜单（PanelMenu）；
 *   - 去卡片边框/圆角/阴影，外留白 10→6 ⇒ 整屏连续；
 *   - 右栏默认收起，点顶栏「右栏」滑出覆盖式 320px 抽屉（RightDockDrawer）；
 *   - 顶栏「导航 / 右栏」开关可摘除/插回面板 ⇒ 修"关掉面板后只能重置布局"的老缺陷。
 *
 * 提示通道（缺陷 ①）：本组件挂载全局 <Toaster/>，系统管理（admin/*，复用
 * pages/system 页面）等 Hub 页面操作的 toast 提示在工作台内可见。
 * 跨窗口会话一致：挂载 startCrossWindowSync，独立窗口（分离/重开）与主工作台
 * 共享 messages/thinking（真实 Electron 或 VITE_MOCK_ELECTRON 双标签联调生效）。
 */
import { useCallback, useEffect, useRef, useState, lazy, Suspense } from 'react';
import type { ReactElement } from 'react';
import { Mosaic, MosaicWindow } from 'react-mosaic-component';
import type { MosaicNode, MosaicPath } from 'react-mosaic-component';
import { Cloud, MessageSquare, PanelLeft, PanelRight, RotateCcw } from 'lucide-react';
import { Button } from '@/components/ui';
import {
  DEFAULT_LAYOUT,
  DOCK_DEFAULT_WIDTH,
  PANEL,
  PANEL_TITLES,
  layoutHasPanel,
  removePanelFromLayout,
} from './lib/mosaic';
import type { PanelId } from './lib/mosaic';
import { useLayoutStore } from './stores/useLayoutStore';
import { useWorkbenchNav } from './workbench/navStore';
import { findNavItem } from './workbench/hubNav';
import { NavUrlSync } from './workbench/useNavUrlSync';
import { useInRouterContext } from 'react-router-dom';
import Toaster from './components/Toaster';
import AlarmBell from './components/AlarmBell';
import { renderPanel } from './components/workbench/panels/renderPanel';
import { startCrossWindowSync } from './electron/sync';

// 可观测性浮层（DevConsole 网络/错误/性能 + StateInspector 状态/时间线）：
// lazy 独立 chunk；组件与 store 内部 isObservabilityEnabled 统一 gate——
// 启用状态由构建期 VITE_OBSERVABILITY_ENABLED 决定（dev 默认开；生产
// 显式设 true 才开，缺省关闭时 DevConsole 返回 null、install() 为空操作）。
const ObservabilityDevtools = lazy(() => import('./components/ObservabilityDevtools'));

/** 当前激活导航项标签（顶栏显示当前位置） */
function ActiveNavLabel() {
  const activeKey = useWorkbenchNav((s) => s.activeKey);
  const item = findNavItem(activeKey);
  return item?.label ?? '会话任务';
}

/**
 * 构建标记（构建期注入的 __YUNSHU_BUILD__）：顶栏小徽标，用于分辨"页面跑的是哪次构建"。
 * 【Why】前端修复上线后，未刷新的页面会一直执行旧 bundle，看起来像"改了没用/还是老问题"。
 * 有构建戳即可一眼确认（悬浮显示完整 ISO 时间与强制刷新提示）。
 */
function BuildStamp() {
  const iso = typeof __YUNSHU_BUILD__ === 'string' ? __YUNSHU_BUILD__ : '';
  if (!iso) return null;
  const d = new Date(iso);
  const label = Number.isNaN(d.getTime())
    ? iso
    : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return (
    <span className="wb-build-stamp" title={`当前页面构建于 ${iso}；若与最新构建不符请强制刷新（Ctrl+F5）`}>
      build {label}
    </span>
  );
}
import { PanelMenu } from './components/workbench/PanelMenu';
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

/**
 * 拖拽预览块 —— 必须是真实 DOM 节点（HTML5 的 setDragImage 要拿它截图），
 * 但 react-mosaic 默认的 renderPreview 会渲染「工具栏 + 标题 + h4」那一套，
 * 而那东西是**在流里可见的**：去掉面板标题栏之后，导航面板里就会冒出「导航」两个字
 * （Owner 2026-11-15 第三次回调现场指出）。这里换成自己的空块：
 * 只画一圈青色虚线，靠 z-index:0 压在 .mosaic-window-body（z-index:1）后面 ⇒ 平时看不见，
 * 拖拽时作为半透明幽灵图（不含任何文字）。
 */
function panelDragGhost(): ReactElement {
  return <div className="wb-drag-ghost" aria-hidden="true" />;
}

/**
 * 面板抓手（面板顶边居中：44×9 命中区 + 28×2 可见线）—— 代替原面板标题栏。
 *
 * 【不易·1】MosaicWindow 会把 renderToolbar 的返回值**包成 drag source**
 * （node_modules/react-mosaic-component/lib/MosaicWindow.mjs:128-141：connectIfDraggable(renderToolbar(...))），
 * 所以返回的节点就是"拖这块能移动面板 / 拖到别人的边上拆分 / 拖到窗口边缘分离成独立窗口"
 * 的命中区。
 * 【不易·1b】致命组合是"**高度 0 且不给节点**"（§11.122 D-1）；**只把高度设 0 不会失效**：
 * 现在 .mosaic-window-toolbar 高度确实是 0（不占版面），但抓取节点仍在（absolute 浮在顶边），
 * 所以它照样是 drag source —— Owner 2026-11-15 回调"细条全去掉、只留手柄"后实测仍能起拖（§11.123 G）。
 *
 * 【不易·2】react-dnd 的 connector **只接受原生 DOM 节点**：这里不能返回 <PanelGrip/> 这种
 * 自定义组件元素，否则运行期直接抛
 * "Only native element nodes can now be passed to React DnD connectors"（本轮实测踩到一次）。
 * 所以抓取条写成**返回 JSX 的普通函数**（函数调用 → 返回的仍是那个 div），不是一个组件。
 *
 * 面板标题文字不再显示（身份靠内容本身辨认），title 仍传给 MosaicWindow（只用于拖拽预览）。
 */
function panelGripElement() {
  return (
    <div
      className="wb-panel-grip"
      data-testid="wb-panel-grip"
      title="拖动面板：拖到别的面板边上拆分，拖到窗口边缘分离为独立窗口"
    />
  );
}
export default function WorkbenchApp() {
  const layout = useLayoutStore((s) => s.layout);
  const setLayout = useLayoutStore((s) => s.setLayout);
  const resetLayout = useLayoutStore((s) => s.resetLayout);
  // 顶栏面板开关（2026-11-15）：状态全部住在布局树里（持久化由 layout 承担）
  const togglePanel = useLayoutStore((s) => s.togglePanel);
  const showPanel = useLayoutStore((s) => s.showPanel);
  const hidePanel = useLayoutStore((s) => s.hidePanel);

  // 可见性以**布局树**为准（面板还在不在树里），而不是另存一份布尔：
  // 这样 ⋮ 菜单的"关闭"、拖到边缘分离、顶栏开关三处永远一致，也不会出现
  // "开关说在、树里没有"的错位。右坞（code）也一样 —— 它是个真 mosaic 列。
  const baseLayout = layout ?? DEFAULT_LAYOUT;
  const navVisible = layoutHasPanel(baseLayout, PANEL.NAV);
  const chatVisible = layoutHasPanel(baseLayout, PANEL.CHAT);
  const dockVisible = layoutHasPanel(baseLayout, PANEL.CODE);

  /** 右坞插回时占的宽度：按视口把 320px 折成百分比（14%~45% 兜底） */
  const dockPercent = () => {
    const usable = Math.max(320, (typeof window === 'undefined' ? 1440 : window.innerWidth) - 12);
    return Math.min(45, Math.max(14, (DOCK_DEFAULT_WIDTH / usable) * 100));
  };

  // 导航状态 ⇄ 地址栏（可深链 #/workbench?panel=governance/approvals；见 useNavUrlSync）。
  // 【为什么用 useInRouterContext 而不是直接调 hook】本组件的既有挂载用例
  // （src/components/Toaster.mount.test.tsx）在**没有 Router** 的裸环境里渲染它，
  // 而 useLocation() 在 Router 之外会**抛异常**（渲染期整棵子树失败）。
  // React 的 hook 规则不允许条件调用 useLocation，故在父组件判上下文、在子组件里调 hooks。
  const inRouter = useInRouterContext();

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

  // ─── 跨窗口状态同步：主工作台也是同步端点 ───
  // 独立窗口（分离出的 BrowserWindow / mock 双标签页）与主工作台实时共享
  // messages/thinking，保证 #/detached/chat 等视图"与主工作台会话一致"。
  // 非 Electron 环境（含未注入 mock）内部为空操作；直接返回 cleanup，StrictMode
  // 下 effect 重跑会先清理再重建订阅，不会留下失效的同步。
  useEffect(() => {
    return startCrossWindowSync();
  }, []);

  /**
   * 渲染单个面板窗口：6px 抓取条（drag source）+ 右上角悬浮 ⋮ 菜单 + 面板内容。
   * 不再传 toolbarControls：三动作已经进 ⋮ 菜单（PanelMenu），标题栏整条去掉。
   */
  const renderTile = (id: PanelId, path: MosaicPath) => (
    <MosaicWindow<PanelId>
      path={path}
      title={PANEL_TITLES[id]}
      renderToolbar={() => panelGripElement()}
      renderPreview={() => panelDragGhost()}
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
      <div className="wb-panel">
        <PanelMenu panelId={id} onDetach={detachPanel} />
        {renderPanel(id)}
      </div>
    </MosaicWindow>
  );

  return (
    <div className="workbench-root">
      {/* 全局 Toast 容器：系统管理（admin/*，复用 pages/system）等 Hub 页面操作
          失败/成功提示在此可见（缺陷 ①）；单例幂等，可多处挂载 */}
      <Toaster />
      {/* 可观测性浮层（DevConsole + StateInspector）：lazy 独立 chunk；
          是否显示由 VITE_OBSERVABILITY_ENABLED 构建期 gate 控制——
          dev 默认启用；生产构建设 VITE_OBSERVABILITY_ENABLED=true 亦启用，
          缺省时组件内 isObservabilityEnabled() 返回 false → 不渲染、install 空操作 */}
      <Suspense fallback={null}>
        <ObservabilityDevtools />
      </Suspense>
      {/* 顶栏 */}
      <header className="wb-topbar">
        <div className="flex items-center gap-2.5">
          {/* 2026-10-31：按 Owner 要求**恢复**顶栏品牌区（左侧导航栏的品牌块仍保持移除）。
              保留 logo 徽标 + 「云枢工作台」字样，状态胶囊 / 当前导航 / 构建戳紧随其后。 */}
          <div className="wb-logo-badge">
            <Cloud size={15} />
          </div>
          <span className="wb-brand-title">云枢工作台</span>
          <span className="wb-status-pill">
            <span className="wb-pulse-dot" />
            {isElectron() ? '桌面模式' : 'Web 模式'}
          </span>
          <span className="ml-1 hidden items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900/70 px-2.5 py-1 text-caption text-cyan-300/90 sm:flex">
            {ActiveNavLabel()}
          </span>
          <BuildStamp />
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-caption text-slate-500 sm:inline">
            {isElectron()
              ? '拖面板顶部抓手移动 · 拖到窗口边缘分离为独立窗口'
              : '拖面板顶部抓手拆分 · 布局自动保存'}
          </span>
          {/* 面板开关（2026-11-15 B 步）：关掉的面板可以从这里再打开。
              此前 ⋮/✕ 关掉面板后没有任何入口能恢复，只能「重置布局」——那是本轮要修的缺陷。
              「主内容」开关只在它真被关掉时才出现（默认顶栏就两个开关，符合 Owner 的观感要求）。 */}
          {/* 纯图标（2026-11-15 二次回调）：顶栏不再出现「导航 / 右栏 / 主内容」这些字样，
              可访问名仍由 aria-label 提供（读屏与测试都按名字找）。 */}
          <div className="wb-panel-toggles">
            {/* 【2026-11-16 统一】图标开关改 ui/Button：选中（面板可见）= primary 高亮，未选 = ghost。
                原来 .wb-panel-toggle.on 是页面自定义的青色描边，和全站按钮不是一回事。 */}
            <Button
              type="button"
              variant={navVisible ? 'primary' : 'ghost'}
              size="icon"
              aria-pressed={navVisible}
              aria-label="导航"
              onClick={() => togglePanel(PANEL.NAV)}
              title="显示 / 隐藏左侧导航（关掉后仍可从这里打开）"
            >
              <PanelLeft size={14} />
            </Button>
            <Button
              type="button"
              variant={dockVisible ? 'primary' : 'ghost'}
              size="icon"
              aria-pressed={dockVisible}
              aria-label="右栏"
              // 打开 = 插回布局树最右（主内容收缩、中间分割条可拖宽）；关闭 = 从树里摘掉
              onClick={() => {
                if (dockVisible) hidePanel(PANEL.CODE);
                else showPanel(PANEL.CODE, dockPercent());
              }}
              title="显示 / 隐藏右栏（打开后是独立一列：主内容收缩，拖分割条可改宽度）"
            >
              <PanelRight size={14} />
            </Button>
            {!chatVisible && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-pressed={false}
                aria-label="主内容"
                onClick={() => togglePanel(PANEL.CHAT)}
                title="主内容面板已被关掉：点这里恢复"
              >
                <MessageSquare size={14} />
              </Button>
            )}
          </div>
          {/* 报警器：系统运行期问题的汇报入口（读 /api/alarms，与消息中心同一张台账）。
              「查看全部」由这里注入导航动作 —— 组件本身不认识路由。 */}
          <AlarmBell onOpenAll={() => useWorkbenchNav.getState().setActiveKey('admin/notifications')} />
          <Button type="button" variant="default" size="sm" onClick={resetLayout} title="恢复默认布局">
            <RotateCcw size={13} />
            重置布局
          </Button>
        </div>
      </header>
      {/* Mosaic 工作区 */}
      <div className="wb-mosaic-body">
        {/* 导航 ⇄ 地址栏同步：只在 Router 上下文里挂载（裸渲染本组件时静默跳过） */}
        {inRouter && <NavUrlSync />}

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
              <Button type="button" variant="default" size="sm" onClick={resetLayout}>
                <RotateCcw size={13} />
                恢复默认布局
              </Button>
            </div>
          }
        />

      </div>
    </div>
  );
}
