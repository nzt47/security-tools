/**
 * SessionsDropdown — 会话下拉列表
 *
 * 从旧版 sessions.js renderSessions + sessions.css L123-235 移植。
 * 绝对定位下拉，hover 显示操作按钮（重命名/删除）。
 *
 * 不变量【不易】：
 * - session-item-actions 默认 opacity:0，hover/active 时 opacity:1（与旧版对齐）
 * - console.log 追踪 hover 事件（按用户要求 A）
 */
import React from 'react';
import type { Session } from '../../store/useChatStore';

export interface SessionsDropdownProps {
  open: boolean;
  sessions: Session[];
  sessionId: string;
  loadingSessions: boolean;
  onSwitchSession: (id: string) => void;
  onNewSession: () => void;
  onRenameSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}

function formatSessionTime(isoStr: string): string {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}小时前`;
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

export const SessionsDropdown: React.FC<SessionsDropdownProps> = ({
  open,
  sessions,
  sessionId,
  loadingSessions,
  onSwitchSession,
  onNewSession,
  onRenameSession,
  onDeleteSession,
}) => {
  if (!open) return null;

  return (
    <div className="sessions-dropdown open">
      <div className="sessions-dropdown-header">
        <span className="sessions-dropdown-header-title">📋 会话</span>
        <button
          className="btn-new-session"
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onNewSession();
          }}
        >
          + 新建
        </button>
      </div>
      <div className="sessions-dropdown-list">
        {loadingSessions ? (
          <div className="sessions-dropdown-status">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="sessions-dropdown-status">暂无会话</div>
        ) : (
          sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${s.id === sessionId ? 'active' : ''}`}
              onClick={() => onSwitchSession(s.id)}
            >
              <div className="session-item-info">
                <span className="session-item-title">{s.title || '未命名会话'}</span>
                <span className="session-item-meta">
                  {s.message_count != null ? `${s.message_count} 条 · ` : ''}
                  {formatSessionTime(s.updated_at || s.created_at || '')}
                </span>
              </div>
              <div className="session-item-actions">
                <button
                  className="session-item-btn"
                  title="重命名"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRenameSession(s.id);
                  }}
                >
                  ✎
                </button>
                <button
                  className="session-item-btn session-item-delete"
                  title="删除"
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.id);
                  }}
                >
                  ✕
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default SessionsDropdown;
