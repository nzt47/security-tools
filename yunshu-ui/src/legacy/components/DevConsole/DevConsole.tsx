/**
 * DevConsole 浮层容器
 *
 * 功能：
 * - 右上角可拖动 FAB 图标，点击唤起/收起浮层
 * - Tab 切换：网络 / 错误 / 性能
 * - 通过 React Portal 挂载到 body，不侵入业务布局
 * - FAB badge 显示错误数（红色高亮）
 *
 * 异常处理：组件内部所有错误边界兜底，确保不影响业务页面渲染。
 *
 * 状态同步机制说明：暂停状态由 store 集中管理，FAB 与面板共用同一 store 实例，
 * 保证 UI 与数据状态绝对同步。
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useDevConsoleStore } from './store';
import { isObservabilityEnabled, trackEvent, TrackEventName } from '@/config/observability';
import NetworkPanel from './NetworkPanel';
import ErrorPanel from './ErrorPanel';
import PerformancePanel from './PerformancePanel';
import './DevConsole.css';

/** Tab 描述符（支持外部注入额外 Tab，如 StateInspector） */
export interface TabDescriptor {
  /** 唯一 key */
  key: string;
  /** 显示名称 */
  label: string;
  /** 计数（显示在 label 右侧） */
  count?: number;
  /** 计数是否高亮为错误色 */
  countError?: boolean;
  /** 渲染该 Tab 内容的函数 */
  render: () => React.ReactNode;
}

interface DevConsoleProps {
  /** 初始是否展开 */
  defaultOpen?: boolean;
  /** 额外注入的 Tab（用于组合 StateInspector 等面板） */
  extraTabs?: TabDescriptor[];
}

const DevConsole: React.FC<DevConsoleProps> = ({
  defaultOpen = false,
  extraTabs = [],
}) => {
  const [open, setOpen] = useState(defaultOpen);
  const [activeTab, setActiveTab] = useState<string>('network');

  // 拖动状态
  const [fabPos, setFabPos] = useState<{ x: number; y: number }>({
    x: -1, // -1 表示未拖动过，使用默认 right 定位
    y: -1,
  });
  const dragState = useRef<{
    dragging: boolean;
    moved: boolean;
    startX: number;
    startY: number;
    origX: number;
    origY: number;
  }>({ dragging: false, moved: false, startX: 0, startY: 0, origX: 0, origY: 0 });

  const networkCount = useDevConsoleStore((s) => s.networkRecords.length);
  const errorCount = useDevConsoleStore((s) => s.errorRecords.length);
  const perfCount = useDevConsoleStore((s) => s.perfRecords.length);
  const paused = useDevConsoleStore((s) => s.paused);
  const togglePause = useDevConsoleStore((s) => s.togglePause);
  const clearAll = useDevConsoleStore((s) => s.clearAll);

  // ─── 拖动逻辑 ───
  const handleFabMouseDown = useCallback(
    (e: React.MouseEvent) => {
      // 仅左键触发拖动
      if (e.button !== 0) return;
      const startX = e.clientX;
      const startY = e.clientY;
      const origX = fabPos.x < 0 ? window.innerWidth - 56 : fabPos.x;
      const origY = fabPos.y < 0 ? 16 : fabPos.y;
      dragState.current = {
        dragging: true,
        moved: false,
        startX,
        startY,
        origX,
        origY,
      };
    },
    [fabPos]
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragState.current.dragging) return;
      const dx = e.clientX - dragState.current.startX;
      const dy = e.clientY - dragState.current.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        dragState.current.moved = true;
      }
      const newX = Math.max(
        8,
        Math.min(window.innerWidth - 48, dragState.current.origX + dx)
      );
      const newY = Math.max(
        8,
        Math.min(window.innerHeight - 48, dragState.current.origY + dy)
      );
      setFabPos({ x: newX, y: newY });
    };

    const handleMouseUp = () => {
      if (dragState.current.dragging) {
        // 未移动则视为点击 → 切换展开
        if (!dragState.current.moved) {
          setOpen((prev) => {
            const nextOpen = !prev;
            // 埋点：DevConsole 唤起（D5 指标，单次<1ms，失败静默）
            if (nextOpen) {
              trackEvent(TrackEventName.DEVCONSOLE_OPEN, {
                module: 'dev',
                success: true,
              });
            }
            return nextOpen;
          });
        }
        dragState.current.dragging = false;
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  // ─── 错误边界：捕获子组件渲染异常，避免白屏 ───
  if (!isObservabilityEnabled()) return null;

  const fabStyle: React.CSSProperties =
    fabPos.x < 0
      ? {} // 使用 CSS 默认 right 定位
      : { left: fabPos.x, top: fabPos.y, right: 'auto' };

  return createPortal(
    <ErrorBoundary fallback={null}>
      <div className="devconsole-root">
        {/* 唤起图标 */}
        <div
          className="devconsole-fab"
          style={fabStyle}
          onMouseDown={handleFabMouseDown}
          title="云枢 DevConsole（拖动移动 / 点击展开）"
          role="button"
          tabIndex={0}
        >
          🐛
          {errorCount > 0 && !open && (
            <span className="devconsole-fab-badge">{errorCount}</span>
          )}
        </div>

        {/* 浮层面板 */}
        {open && (
          <div className="devconsole-panel" role="dialog" aria-label="DevConsole">
            {/* 头部 Tab 栏 */}
            <div className="devconsole-header">
              <div className="devconsole-tabs">
                <button
                  type="button"
                  className={`devconsole-tab ${
                    activeTab === 'network' ? 'active' : ''
                  }`}
                  onClick={() => setActiveTab('network')}
                >
                  网络
                  <span className="devconsole-tab-count">{networkCount}</span>
                </button>
                <button
                  type="button"
                  className={`devconsole-tab ${
                    activeTab === 'error' ? 'active' : ''
                  }`}
                  onClick={() => setActiveTab('error')}
                >
                  错误
                  <span
                    className={`devconsole-tab-count ${
                      errorCount > 0 ? 'error' : ''
                    }`}
                  >
                    {errorCount}
                  </span>
                </button>
                <button
                  type="button"
                  className={`devconsole-tab ${
                    activeTab === 'performance' ? 'active' : ''
                  }`}
                  onClick={() => setActiveTab('performance')}
                >
                  性能
                  <span className="devconsole-tab-count">{perfCount}</span>
                </button>
                {extraTabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    className={`devconsole-tab ${
                      activeTab === tab.key ? 'active' : ''
                    }`}
                    onClick={() => setActiveTab(tab.key)}
                  >
                    {tab.label}
                    {tab.count !== undefined && (
                      <span
                        className={`devconsole-tab-count ${
                          tab.countError ? 'error' : ''
                        }`}
                      >
                        {tab.count}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="devconsole-actions">
                <button
                  type="button"
                  className={`devconsole-btn warning ${paused ? 'active' : ''}`}
                  onClick={togglePause}
                  title={paused ? '恢复采集' : '暂停采集'}
                >
                  {paused ? '▶ 恢复' : '⏸ 暂停'}
                </button>
                <button
                  type="button"
                  className="devconsole-btn"
                  onClick={() => setOpen(false)}
                  title="收起"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* 面板内容 */}
            <ErrorBoundary fallback={<div className="devconsole-empty">面板渲染异常</div>}>
              {activeTab === 'network' && <NetworkPanel />}
              {activeTab === 'error' && <ErrorPanel />}
              {activeTab === 'performance' && <PerformancePanel />}
              {extraTabs.map(
                (tab) =>
                  activeTab === tab.key && (
                    <React.Fragment key={tab.key}>
                      {tab.render()}
                    </React.Fragment>
                  )
              )}
            </ErrorBoundary>
          </div>
        )}
      </div>
    </ErrorBoundary>,
    document.body
  );
};

// ─── 轻量错误边界（class 组件，React 要求） ─────────────────────────────

interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback: React.ReactNode;
}
interface ErrorBoundaryState {
  hasError: boolean;
}

class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // DevConsole 内部错误仅记录，不影响业务页面
    console.error('[DevConsole] 面板渲染异常:', error, info);
  }

  render(): React.ReactNode {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

export default DevConsole;
