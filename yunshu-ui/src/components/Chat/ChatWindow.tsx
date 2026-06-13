import React, { useRef, useEffect } from 'react';
import ChatBubble, { BubbleType } from './ChatBubble';
import ChatInput from './ChatInput';
import './ChatWindow.css';

export interface Message {
  id: string;
  type: BubbleType;
  content: string;
  timestamp: Date;
  typing?: boolean;
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
 * 聊天窗口主组件
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

  // 自动滚动到底部
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
          <ChatBubble
            key={message.id}
            type={message.type}
            content={message.content}
            typing={message.typing}
            timestamp={message.timestamp}
          />
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
