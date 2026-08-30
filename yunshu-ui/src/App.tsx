import React from 'react';
import { SlotHost, SlotProvider } from './plugins';
import { SLOT_IDS, mountAppSlots } from './plugins/slots';
import { mountPanels } from './plugins/panels';
import { ToastContainer } from './components/Status';
import { useChatApp } from './hooks/useChatApp';
import './styles/theme.css';
import './App.css';

// ─── 外壳插槽 + 面板挂载（App.tsx 模块顶层，渲染前执行一次；mountToSlot 幂等，HMR 安全）───
mountAppSlots();
mountPanels();

const App: React.FC = () => {
  // 聊天/会话状态与逻辑在 useChatApp（保留在 App），经插槽 props 下传（见 PLAN-2 §4）
  const chat = useChatApp();

  // ─── 渲染（外壳布局由插槽驱动；面板由 PanelSwitcher 统一驱动，见 plugins/panels.tsx）───
  return (
    <SlotProvider>
      <div className="app">
        <div className="app-container">
          {/* 侧边栏 */}
          <aside className="sidebar">
            <div className="sidebar-header">
              <h1 className="app-title">云枢</h1>
              <SlotHost slotId={SLOT_IDS.topbar} props={{ status: chat.systemStatus }} />
            </div>

            {/* 侧栏内容：面板切换器 / Mascot / 会话列表（profile 可调顺序/显隐） */}
            <SlotHost
              slotId={SLOT_IDS.sidebar}
              props={{
                mood: chat.mood,
                sessions: chat.sessions,
                sessionId: chat.sessionId,
                loadingSessions: chat.loadingSessions,
                onNewSession: chat.handleNewSession,
                onSwitchSession: chat.handleSwitchSession,
              }}
            />
          </aside>

          {/* 主聊天区 */}
          <main className="main-content">
            <SlotHost
              slotId={SLOT_IDS.main}
              props={{
                messages: chat.messages,
                inputValue: chat.inputValue,
                streaming: chat.streaming,
                onSendMessage: chat.handleSendMessage,
                onInputChange: chat.setInputValue,
              }}
            />
          </main>
        </div>

        <ToastContainer toasts={chat.toasts} onClose={chat.handleCloseToast} />
      </div>
    </SlotProvider>
  );
};

export default App;
