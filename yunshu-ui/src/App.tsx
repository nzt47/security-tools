import React, { useState } from 'react';
import { SlotHost, SlotProvider } from './plugins';
import { SLOT_IDS, mountAppSlots } from './plugins/slots';
import { ToastContainer } from './components/Status';
import { useChatApp } from './hooks/useChatApp';
import SkillManagement from './components/SkillsMgmt/SkillManagement';
import Knowledge from './pages/Knowledge';
import './styles/theme.css';
import './App.css';

// ─── 外壳插槽挂载（App.tsx 模块顶层，渲染前执行一次；mountToSlot 幂等，HMR 安全）───
mountAppSlots();

const App: React.FC = () => {
  // 聊天/会话状态与逻辑在 useChatApp（保留在 App），经插槽 props 下传（见 PLAN-2 §4）
  const chat = useChatApp();
  // 面板开关（T2.3 迁往 panels 插槽）
  const [skillMgmtOpen, setSkillMgmtOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);

  // ─── 渲染（外壳布局由插槽驱动，顺序/显隐由 profile.json 控制）───
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

            {/* 侧栏内容：面板入口 / Mascot / 会话列表（profile 可调顺序/显隐） */}
            <SlotHost
              slotId={SLOT_IDS.sidebar}
              props={{
                mood: chat.mood,
                sessions: chat.sessions,
                sessionId: chat.sessionId,
                loadingSessions: chat.loadingSessions,
                knowledgeOpen,
                onNewSession: chat.handleNewSession,
                onSwitchSession: chat.handleSwitchSession,
                onOpenSkillMgmt: () => setSkillMgmtOpen(true),
                onToggleKnowledge: () => setKnowledgeOpen((v) => !v),
              }}
            />
          </aside>

          {/* 聊天区 / 知识库（任务6 增量，互不干扰） */}
          <main className="main-content">
            {knowledgeOpen ? (
              <Knowledge />
            ) : (
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
            )}
          </main>
        </div>

        <ToastContainer toasts={chat.toasts} onClose={chat.handleCloseToast} />

        {/* 技能管理与工作流学习面板（T2.3 迁往 panels 插槽） */}
        {skillMgmtOpen && (
          <SkillManagement onClose={() => setSkillMgmtOpen(false)} />
        )}
      </div>
    </SlotProvider>
  );
};

export default App;
