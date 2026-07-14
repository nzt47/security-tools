/**
 * HistoryTab — 右内侧悬浮触发器 + 历史问话面板容器
 *
 * 从旧版 templates/index.html L585-591 + L4128-4147 移植。
 * 行为：mouseenter 立即 open，mouseleave 300ms 后 close（与旧版对齐）
 *
 * 不变量【不易】：
 * - 触发器固定在 .chat-body 右侧（position: absolute, right: 0）
 * - 面板从右侧滑入（animation: historySlideIn）
 * - Escape 键关闭面板
 */
import React, { useEffect } from 'react';
import { useHistoryPanel } from '../../hooks/useHistoryPanel';
import { HistoryPanel } from './HistoryPanel';
import './HistoryPanel.css';

export const HistoryTab: React.FC = () => {
  const {
    isOpen,
    isClosing,
    filtered,
    loading,
    error,
    search,
    activeIdx,
    formatHistTime,
    actions,
  } = useHistoryPanel();

  // Escape 键关闭面板
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') actions.closePanel();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, actions]);

  return (
    <>
      {/* 右内侧悬浮触发器 */}
      <div
        className="cht-tab"
        title="历史问话"
        onMouseEnter={actions.openPanel}
        onMouseLeave={actions.scheduleAutoClose}
      >
        <span className="trigger-icon">🕐</span>
        <span className="trigger-label">记录</span>
      </div>

      {/* 历史问话面板 */}
      {isOpen && (
        <div
          onMouseEnter={actions.cancelAutoClose}
          onMouseLeave={actions.scheduleAutoClose}
        >
          <HistoryPanel
            items={filtered}
            loading={loading}
            error={error}
            search={search}
            activeIdx={activeIdx}
            isClosing={isClosing}
            formatHistTime={formatHistTime}
            onSearch={actions.setSearch}
            onClose={actions.closePanel}
            onNavigate={actions.navigateTo}
            onCopy={actions.copyText}
            onDelete={actions.deleteItem}
          />
        </div>
      )}
    </>
  );
};

export default HistoryTab;
