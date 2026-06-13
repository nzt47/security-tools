import React, { useState } from 'react';
import { Mascot } from './components/Mascot';
import { ChatWindow, Message } from './components/Chat';
import { StatusIndicator } from './components/Status';
import { ToastContainer, ToastData } from './components/Status';
import './styles/theme.css';
import './App.css';

const App: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      type: 'assistant',
      content: '我是来自网天的云枢',
      timestamp: new Date(),
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [mood, setMood] = useState<'idle' | 'thinking' | 'happy' | 'excited'>('idle');
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const handleSendMessage = (message: string) => {
    const newUserMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: message,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, newUserMessage]);
    setInputValue('');
    setMood('thinking');

    // 模拟助手回复
    setTimeout(() => {
      const responses = [
        '让我想想...这个问题很有趣！',
        '好的，我明白了。让我帮你分析一下。',
        '这是个很好的问题！让我来解答。',
        '我理解了。你还有其他想了解的吗？',
      ];
      const randomResponse = responses[Math.floor(Math.random() * responses.length)];

      const newAssistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: randomResponse,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, newAssistantMessage]);
      setMood('idle');
    }, 1500);
  };

  const handleMoodChange = (newMood: any) => {
    console.log('Mood changed:', newMood);
  };

  const handleCloseToast = (id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  };

  const addToast = (type: ToastData['type'], message: string) => {
    const newToast: ToastData = {
      id: Date.now().toString(),
      type,
      message,
    };
    setToasts((prev) => [...prev, newToast]);
  };

  return (
    <div className="app">
      <div className="app-container">
        {/* 侧边栏 - Mascot 显示区 */}
        <aside className="sidebar">
          <div className="sidebar-header">
            <h1 className="app-title">云枢</h1>
            <StatusIndicator status="online" size="small" />
          </div>

          <div className="mascot-wrapper">
            <Mascot
              initialMood={mood}
              tracking
              glow
              breathing
              size="large"
              onMoodChange={handleMoodChange}
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

          <div className="sidebar-actions">
            <button
              className="action-btn"
              onClick={() => addToast('info', '这是一个提示消息')}
            >
              测试提示
            </button>
            <button
              className="action-btn"
              onClick={() => addToast('success', '操作成功！')}
            >
              测试成功
            </button>
          </div>
        </aside>

        {/* 主内容区 - 聊天窗口 */}
        <main className="main-content">
          <ChatWindow
            messages={messages}
            onSendMessage={handleSendMessage}
            inputValue={inputValue}
            onInputChange={setInputValue}
          />
        </main>
      </div>

      {/* Toast 通知 */}
      <ToastContainer toasts={toasts} onClose={handleCloseToast} />
    </div>
  );
};

export default App;
