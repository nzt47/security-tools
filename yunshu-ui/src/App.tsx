import React, { useState, useEffect, useRef } from 'react';
import { Mascot } from './components/Mascot';
import { ChatWindow, Message } from './components/Chat';
import { StatusIndicator } from './components/Status';
import { ToastContainer, ToastData } from './components/Status';
import { useChatStream } from './hooks/useChatStream';
import SkillManagement from './components/SkillsMgmt/SkillManagement';
import './styles/theme.css';
import './App.css';

const API_BASE = '';  // 同域，空字符串

const App: React.FC = () => {
  // ─── 状态 ───
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [mood, setMood] = useState<'idle' | 'thinking' | 'happy' | 'excited'>('idle');
  const [toasts, setToasts] = useState<ToastData[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionId, setSessionId] = useState<string>('');
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [systemStatus, setSystemStatus] = useState<string>('offline');
  const [skillMgmtOpen, setSkillMgmtOpen] = useState(false);

  const { state, send, reset } = useChatStream(API_BASE);

  // 防止重复触发的锁
  const lastResponseRef = useRef('');

  // ─── 初始化 ───
  useEffect(() => {
    const savedId = localStorage.getItem('yunshu_session_id') || '';
    if (savedId) setSessionId(savedId);
    loadSessions(savedId);
    checkHealth();
  }, []);

  // ─── streaming 完成 → 添加 assistant 消息 ───
  useEffect(() => {
    // streaming 从 true→false 且有回复文本
    if (!state.streaming && state.text && state.text !== lastResponseRef.current) {
      lastResponseRef.current = state.text;
      const newMsg: Message = {
        id: `assistant-${Date.now()}`,
        type: 'assistant',
        content: state.text,
        timestamp: new Date(),
        reasoning: state.reasoning || undefined,
        toolSteps: state.toolSteps.length > 0 ? state.toolSteps : undefined,
      };
      setMessages(prev => [...prev, newMsg]);
      setMood('idle');
    }
    // 错误处理
    if (!state.streaming && state.error) {
      addToast('error', state.error);
      setMood('idle');
    }
  }, [state.streaming]);

  // ─── 会话切换 → 加载消息 ───
  useEffect(() => {
    if (sessionId) {
      loadMessages(sessionId);
    }
  }, [sessionId]);

  // ─── API ───

  const loadSessions = async (activeId?: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`);
      const data = await res.json();
      setSessions(data.sessions || []);
      if (!activeId && data.current_id) {
        setSessionId(data.current_id);
        localStorage.setItem('yunshu_session_id', data.current_id);
      }
    } catch (e) {
      console.error('加载会话列表失败:', e);
    } finally {
      setLoadingSessions(false);
    }
  };

  const loadMessages = async (sid: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${sid}/messages`);
      if (!res.ok) {
        if (res.status === 404) {
          // 会话可能已被删除
          localStorage.removeItem('yunshu_session_id');
          setSessionId('');
        }
        return;
      }
      const data = await res.json();
      const msgs: Message[] = (data || []).map((msg: any, i: number) => ({
        id: `msg-${i}-${msg.timestamp || Date.now()}`,
        type: msg.role === 'user' ? 'user' : 'assistant',
        content: msg.content || '',
        timestamp: new Date(msg.timestamp),
        reasoning: msg.role === 'assistant' ? (msg.reasoning || undefined) : undefined,
        toolSteps: msg.role === 'assistant' ? (msg.tool_steps || undefined) : undefined,
      }));
      setMessages(msgs);
    } catch (e) {
      console.error('加载消息失败:', e);
    }
  };

  const checkHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (res.ok) {
        setSystemStatus('online');
        // 再获取一次状态信息
        fetch(`${API_BASE}/api/status`).then(r => r.json()).then(d => {
          // 可以扩展更多状态
        }).catch(() => {});
      }
    } catch {
      setSystemStatus('offline');
    }
  };

  // ─── 动作 ───

  const handleSendMessage = (message: string) => {
    const userMsg: Message = {
      id: `user-${Date.now()}`,
      type: 'user',
      content: message,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInputValue('');
    setMood('thinking');
    send(message, sessionId);
  };

  const handleNewSession = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新对话' }),
      });
      const session = await res.json();
      const newId = session.id;
      localStorage.setItem('yunshu_session_id', newId);
      setSessionId(newId);
      setMessages([]);
      reset();
      addToast('success', '已创建新会话');
      loadSessions(newId);
    } catch (e) {
      addToast('error', '创建会话失败');
    }
  };

  const handleSwitchSession = async (sid: string) => {
    if (sid === sessionId) return;
    try {
      await fetch(`${API_BASE}/api/sessions/current`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid }),
      });
      localStorage.setItem('yunshu_session_id', sid);
      setSessionId(sid);
      reset();
    } catch (e) {
      addToast('error', '切换会话失败');
    }
  };

  // ─── Toast ───

  const addToast = (type: ToastData['type'], message: string) => {
    const t: ToastData = { id: Date.now().toString(), type, message };
    setToasts(prev => [...prev, t]);
  };

  const handleCloseToast = (id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  };

  const handleMoodChange = (newMood: any) => {
    console.log('Mood changed:', newMood);
  };

  // ─── 当前会话 ───
  const currentSession = sessions.find(s => s.id === sessionId);

  // ─── 渲染 ───
  return (
    <div className="app">
      <div className="app-container">
        {/* 侧边栏 */}
        <aside className="sidebar">
          <div className="sidebar-header">
            <h1 className="app-title">云枢</h1>
            <StatusIndicator status={systemStatus as any} size="small" />
          </div>

          {/* 技能管理入口 */}
          <div style={{ padding: '8px 12px' }}>
            <button
              onClick={() => setSkillMgmtOpen(true)}
              style={{
                width: '100%',
                background: 'var(--bg-hover, #232730)',
                border: '1px solid var(--border-subtle, #2a2e38)',
                color: 'var(--text-primary, #e8eaed)',
                padding: '8px 12px',
                borderRadius: 6,
                cursor: 'pointer',
                fontSize: 13,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--accent-primary, #4a9eff)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--border-subtle, #2a2e38)';
              }}
              type="button"
              title="打开技能管理与工作流学习面板"
            >
              <span>⚙</span> 技能管理
            </button>
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

          {/* 会话管理 */}
          <div className="session-panel">
            <div className="session-panel-header">
              <span className="session-panel-title">会话</span>
              <button
                className="session-new-btn"
                onClick={handleNewSession}
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
                sessions.map(s => (
                  <div
                    key={s.id}
                    className={`session-item ${s.id === sessionId ? 'active' : ''}`}
                    onClick={() => handleSwitchSession(s.id)}
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
        </aside>

        {/* 聊天区 */}
        <main className="main-content">
          {(() => {
            // streaming 时追加 typing 占位消息
            const displayMsgs = state.streaming
              ? [...messages, { id: 'typing', type: 'assistant' as const, content: '', timestamp: new Date(), typing: true }]
              : messages;
            return (
              <ChatWindow
                messages={displayMsgs}
                onSendMessage={handleSendMessage}
                inputValue={inputValue}
                onInputChange={setInputValue}
                disabled={state.streaming}
              />
            );
          })()}
        </main>
      </div>

      <ToastContainer toasts={toasts} onClose={handleCloseToast} />

      {/* 技能管理与工作流学习面板 */}
      {skillMgmtOpen && (
        <SkillManagement onClose={() => setSkillMgmtOpen(false)} />
      )}
    </div>
  );
};

export default App;
