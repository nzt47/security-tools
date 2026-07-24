/**
 * ChatHeader — 顶部 header
 *
 * 从旧版 templates/index.html L593-621 + sessions.css L1-120 移植。
 * 结构：chat-header-left（点击下拉会话列表）+ chat-header-right（toggle 按钮）
 *
 * 不变量【不易】：
 * - thinkingOn/toolcallsOn 从 useChatStore 读取，setDisplayPref 持久化
 * - 点击外部关闭下拉（与旧版 document click listener 对齐）
 */
import React, { useState, useEffect, useRef } from 'react';
import { useChatStore } from '../../store/useChatStore';
import { SessionsDropdown } from './SessionsDropdown';
import type { Session } from '../../store/useChatStore';
import './ChatHeader.css';

export interface ChatHeaderProps {
  sessions: Session[];
  sessionId: string;
  loadingSessions: boolean;
  onSwitchSession: (id: string) => void;
  onNewSession: () => void;
  onRenameSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
}

export const ChatHeader: React.FC<ChatHeaderProps> = ({
  sessions,
  sessionId,
  loadingSessions,
  onSwitchSession,
  onNewSession,
  onRenameSession,
  onDeleteSession,
}) => {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const headerRef = useRef<HTMLDivElement>(null);
  const thinkingOn = useChatStore((s) => s.thinkingOn);
  const toolcallsOn = useChatStore((s) => s.toolcallsOn);
  const setDisplayPref = useChatStore((s) => s.setDisplayPref);

  const currentSession = sessions.find((s) => s.id === sessionId);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!dropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (headerRef.current && !headerRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, [dropdownOpen]);

  const toggleDropdown = () => setDropdownOpen((prev) => !prev);
  const closeDropdown = () => setDropdownOpen(false);

  return (
    <div className="chat-header" ref={headerRef}>
      <div
        className={`chat-header-left ${dropdownOpen ? 'open' : ''}`}
        onClick={toggleDropdown}
      >
        <span id="chat-header-title">💬 {currentSession?.title || '对话'}</span>
        <span className="chat-header-arrow">▾</span>
        <SessionsDropdown
          open={dropdownOpen}
          sessions={sessions}
          sessionId={sessionId}
          loadingSessions={loadingSessions}
          onSwitchSession={(id) => {
            onSwitchSession(id);
            closeDropdown();
          }}
          onNewSession={onNewSession}
          onRenameSession={onRenameSession}
          onDeleteSession={onDeleteSession}
        />
      </div>

      <div className="chat-header-right">
        <span className="chat-header-count">{sessions.length} 个会话</span>
        <div className="chat-header-toggles">
          <span
            className={`cht-toggle ${thinkingOn ? 'on' : 'off'}`}
            onClick={() => setDisplayPref('thinking', !thinkingOn)}
            title="显示/隐藏 Thought 思考过程和模式标签"
          >
            <span className="cht-toggle-icon">💭</span>
            <span className="cht-toggle-label">Thought</span>
          </span>
          <span
            className={`cht-toggle ${toolcallsOn ? 'on' : 'off'}`}
            onClick={() => setDisplayPref('toolcalls', !toolcallsOn)}
            title="显示/隐藏工具调用步骤"
          >
            <span className="cht-toggle-icon">🔧</span>
            <span className="cht-toggle-label">工具</span>
          </span>
        </div>
      </div>
    </div>
  );
};

export default ChatHeader;
