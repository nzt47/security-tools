import React, { useRef, useEffect } from 'react';
import ChatBubble, { BubbleType } from './ChatBubble';
import ChatInput from './ChatInput';
import ThinkingBlock from './ThinkingBlock';
import ToolStepsDisplay from './ToolStepsDisplay';
import type { ToolStep } from './ToolStepsDisplay';
import './ChatWindow.css';

export interface Message {
  id: string;
  type: BubbleType;
  content: string;
  timestamp: Date;
  typing?: boolean;
  /** 思考过程（仅 assistant 消息） */
  reasoning?: string;
  /** 工具调用步骤（仅 assistant 消息） */
  toolSteps?: ToolStep[];
}

export interface ChatWindowProps {
  /** 消息列表 */
  messages: Message[];
  /** 发送消息回调 */
  onSendMessage: (message: string) => void;
  /** 输入值 */
  inputValue: string;
  /** 输入变化回调 */
  onInputChange: (value: string) => void;
  /** 是否禁用输入 */
  disabled?: boolean;
  /** 占位符文本 */
  placeholder?: string;
  /** 自定义类名 */
  className?: string;
}

/**
 * 聊天窗口主组件 — 支持显示思考过程和工具调用步骤
 */
const ChatWindow: React.FC<ChatWindowProps> = ({
  messages,
  onSendMessage,
  inputValue,
  onInputChange,
  disabled = false,
  placeholder = '输入消息...',
  className = '',
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    if (inputValue.trim()) {
      onSendMessage(inputValue);
    }
  };

  return (
    <div className={`chat-window ${className}`}>
      <div className="chat-messages">
        {messages.map((message) => (
          <div key={message.id} className={`message-group ${message.type}`}>
            {/* Assistant 消息：显示思考过程和工具步骤 */}
            {message.type === 'assistant' && message.reasoning && (
              <ThinkingBlock content={message.reasoning} />
            )}
            {message.type === 'assistant' && message.toolSteps && message.toolSteps.length > 0 && (
              <ToolStepsDisplay steps={message.toolSteps} />
            )}
            <ChatBubble
              type={message.type}
              content={message.content}
              typing={message.typing}
              timestamp={message.timestamp}
            />
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <ChatInput
        value={inputValue}
        onChange={onInputChange}
        onSend={handleSend}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus
      />
    </div>
  );
};

export default ChatWindow;
