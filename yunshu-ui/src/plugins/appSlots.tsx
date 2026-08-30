/**
 * App 外壳插槽挂载组件（任务 T2.2，T2.3 更新）
 *
 * 每个导出组件对应 App 外壳的一个区块，经 SlotHost 的 props 透传接收
 * App 的状态与回调（聊天/会话状态保留在 App，见 PLAN-2 §4「改造策略」）。
 *
 * 约束【不易】：JSX 结构与改造前 App.tsx 逐字一致，视觉与交互行为不变。
 * - topbar  → StatusEntry    系统状态指示
 * - sidebar → MascotEntry    Mascot + 情绪文案
 * - sidebar → SessionsEntry  会话列表（当前侧栏内联版）
 * - main    → ChatEntry      聊天窗口（含输入框，streaming 时追加 typing 占位）
 *
 * 注：原 SkillBtnEntry / KnowledgeBtnEntry（面板入口按钮）已在 T2.3 迁往
 * panels 插槽体系，由 PanelSwitcher（plugins/panels.tsx 挂载）统一驱动。
 */
import React from 'react';
import { StatusIndicator } from '../components/Status';
import { Mascot } from '../components/Mascot';
import { ChatWindow } from '../components/Chat';
import type { Message } from '../components/Chat';
import type { Session, Mood } from '../store/useChatStore';

// ─── topbar：系统状态指示 ───
export interface StatusEntryProps {
  status?: string;
}

export const StatusEntry: React.FC<StatusEntryProps> = ({ status = 'offline' }) => (
  <StatusIndicator status={status as any} size="small" />
);

// ─── sidebar：Mascot + 情绪文案（mood 由聊天流驱动，回调链路经 props 保持） ───
export interface MascotEntryProps {
  mood?: Mood;
}

export const MascotEntry: React.FC<MascotEntryProps> = ({ mood = 'idle' }) => (
  <>
    <div className="mascot-wrapper">
      <Mascot
        initialMood={mood}
        tracking
        glow
        breathing
        size="large"
        onMoodChange={(m) => console.log('Mood changed:', m)}
        debug={true}
      />
    </div>
    <div className="mascot-info">
      <p className="mascot-greeting">我是来自网天的云枢</p>
      <p className="mascot-status">
        {mood === 'thinking' && '正在思考...'}
        {mood === 'idle' && '等待你的消息'}
        {mood === 'happy' && '今天心情很好！'}
        {mood === 'excited' && '好兴奋啊！'}
      </p>
    </div>
  </>
);

// ─── sidebar：会话列表（当前侧栏内联版；profile 可调整顺序/显隐） ───
export interface SessionsEntryProps {
  sessions?: Session[];
  sessionId?: string;
  loadingSessions?: boolean;
  onNewSession?: () => void;
  onSwitchSession?: (id: string) => void;
}

export const SessionsEntry: React.FC<SessionsEntryProps> = ({
  sessions = [],
  sessionId = '',
  loadingSessions = false,
  onNewSession,
  onSwitchSession,
}) => (
  <div className="session-panel">
    <div className="session-panel-header">
      <span className="session-panel-title">会话</span>
      <button
        className="session-new-btn"
        onClick={onNewSession}
        title="新建对话"
        type="button"
      >
        ✚
      </button>
    </div>
    <div className="session-list">
      {loadingSessions ? (
        <div className="session-list-status">加载中...</div>
      ) : sessions.length === 0 ? (
        <div className="session-list-status">暂无会话</div>
      ) : (
        sessions.map((s) => (
          <div
            key={s.id}
            className={`session-item ${s.id === sessionId ? 'active' : ''}`}
            onClick={() => onSwitchSession?.(s.id)}
          >
            <span className="session-item-title">{s.title}</span>
            <span className="session-item-date">
              {new Date(s.updated_at || s.created_at).toLocaleDateString('zh-CN')}
            </span>
          </div>
        ))
      )}
    </div>
  </div>
);

// ─── main：聊天窗口（含输入框；streaming 时追加 typing 占位消息） ───
export interface ChatEntryProps {
  messages?: Message[];
  inputValue?: string;
  streaming?: boolean;
  onSendMessage: (message: string) => void;
  onInputChange: (value: string) => void;
}

export const ChatEntry: React.FC<ChatEntryProps> = ({
  messages = [],
  inputValue = '',
  streaming = false,
  onSendMessage,
  onInputChange,
}) => {
  const displayMsgs = streaming
    ? [
        ...messages,
        {
          id: 'typing',
          type: 'assistant' as const,
          content: '',
          timestamp: new Date(),
          typing: true,
        },
      ]
    : messages;
  return (
    <ChatWindow
      messages={displayMsgs}
      onSendMessage={onSendMessage}
      inputValue={inputValue}
      onInputChange={onInputChange}
      disabled={streaming}
    />
  );
};
