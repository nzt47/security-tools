import React from 'react';
import './ChatBubble.css';

export type BubbleType = 'user' | 'assistant' | 'system';

export interface ChatBubbleProps {
  /** 气泡类型 */
  type: BubbleType;
  /** 消息内容 */
  content: string;
  /** 是否正在输入 */
  typing?: boolean;
  /** 时间戳 */
  timestamp?: Date;
  /** 是否显示头像 */
  showAvatar?: boolean;
  /** 自定义类名 */
  className?: string;
}

/**
 * 聊天气泡组件
 */
const ChatBubble: React.FC<ChatBubbleProps> = ({
  type,
  content,
  typing = false,
  timestamp,
  showAvatar = true,
  className = '',
}) => {
  const formatTime = (date: Date): string => {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className={`chat-bubble-container ${type} ${className}`}>
      {showAvatar && type === 'assistant' && (
        <div className="chat-avatar assistant-avatar">
          <span>灵</span>
        </div>
      )}

      <div className="chat-bubble-content">
        <div className={`chat-bubble ${type}`}>
          {typing ? (
            <div className="chat-typing">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          ) : (
            <p className="chat-text">{content}</p>
          )}
        </div>

        {timestamp && (
          <span className="chat-timestamp">{formatTime(timestamp)}</span>
        )}
      </div>

      {showAvatar && type === 'user' && (
        <div className="chat-avatar user-avatar">
          <span>我</span>
        </div>
      )}
    </div>
  );
};

export default ChatBubble;
