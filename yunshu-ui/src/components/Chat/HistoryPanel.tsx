/**
 * HistoryPanel — 历史问话弹出面板（纯展示组件）
 *
 * 从旧版 templates/index.html L4160-4250 移植。
 * 结构：头部（标题+关闭）+ 搜索框 + 列表（每项=文本+复制+删除+时间）
 *
 * 不变量【不易】：跳转通过 navigateTo(msgIdx) 滚动到对应 .message-group.user
 */
import React from 'react';
import type { HistoryEntry } from '../../lib/historyApi';
import './HistoryPanel.css';

export interface HistoryPanelProps {
  items: HistoryEntry[];
  loading: boolean;
  error: string | null;
  search: string;
  activeIdx: number | null;
  isClosing: boolean;
  formatHistTime: (iso: string) => string;
  onSearch: (q: string) => void;
  onClose: () => void;
  onNavigate: (msgIdx: number) => void;
  onCopy: (text: string) => void;
  onDelete: (index: number) => void;
}

export const HistoryPanel: React.FC<HistoryPanelProps> = ({
  items,
  loading,
  error,
  search,
  activeIdx,
  isClosing,
  formatHistTime,
  onSearch,
  onClose,
  onNavigate,
  onCopy,
  onDelete,
}) => {
  return (
    <div className={`chat-history-panel ${isClosing ? 'closing' : ''}`}>
      <div className="chat-history-panel-header">
        <span className="chat-history-panel-title">🕐 历史问话</span>
        <button
          className="chat-history-panel-close"
          onClick={onClose}
          type="button"
          title="关闭"
        >
          ×
        </button>
      </div>

      <div className="chat-history-panel-search">
        <input
          type="text"
          placeholder="搜索问话..."
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>

      <div className="chat-history-panel-list">
        {loading ? (
          <div className="hist-empty">加载中...</div>
        ) : error ? (
          <div className="hist-empty">加载失败，请重试</div>
        ) : items.length === 0 ? (
          <div className="hist-empty">{search ? '无匹配问话' : '暂无历史问话'}</div>
        ) : (
          items.map((item, idx) => {
            const text = item.user || '';
            const preview = text.length > 60 ? text.substring(0, 60) + '...' : text;
            const timeStr = formatHistTime(item.timestamp);
            return (
              <div
                key={`${item._real_index}-${idx}`}
                className={`hist-item ${activeIdx === idx ? 'hist-item-active' : ''}`}
                onClick={() => onNavigate(idx)}
              >
                <div className="hist-item-row">
                  <div className="hist-item-text">{preview}</div>
                  <span
                    className="hist-item-copy"
                    title="复制"
                    onClick={(e) => {
                      e.stopPropagation();
                      onCopy(text);
                    }}
                  >
                    📋
                  </span>
                  <button
                    className="hist-item-del"
                    type="button"
                    title="删除此条"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(item._real_index);
                    }}
                  >
                    🗑
                  </button>
                </div>
                <div className="hist-item-time">{timeStr}</div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default HistoryPanel;
