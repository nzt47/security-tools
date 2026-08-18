import React, { useRef, useEffect } from 'react';
import ChatBubble, { BubbleType } from './ChatBubble';
import ChatInput from './ChatInput';
import ThinkingBlock from './ThinkingBlock';
import ToolStepsDisplay from './ToolStepsDisplay';
import type { ToolStep } from './ToolStepsDisplay';
import { useChatStore } from '../../store/useChatStore';
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
  /** 底部插槽（注入到消息列表和输入框之间，如 ContextMonitor） */
  bottomSlot?: React.ReactNode;
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
  bottomSlot,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const thinkingOn = useChatStore((s) => s.thinkingOn);
  const toolcallsOn = useChatStore((s) => s.toolcallsOn);

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
            {/* Assistant 消息：显示思考过程和工具步骤（受 toggle 控制） */}
            {message.type === 'assistant' && message.reasoning && thinkingOn && (
              <ThinkingBlock content={message.reasoning} />
            )}
            {message.type === 'assistant' && message.toolSteps && message.toolSteps.length > 0 && toolcallsOn && (
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

      {bottomSlot}

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
