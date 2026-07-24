/**
 * ChatPage — 会话任务页面（单栏布局）
 *
 * 从旧版 templates/index.html #view-chat 结构迁移：
 * 顶部 ChatHeader（下拉会话列表 + toggle 按钮）
 * 中部 chat-body（消息列表 + 右内侧悬浮 HistoryTab）
 * 下部 ContextMonitor（折叠/展开，通过 bottomSlot 注入）
 * 底部 ChatInput
 *
 * 不变量【不易】：
 * - 会话管理逻辑（loadSessions/loadMessages/handleNewSession/handleSwitchSession）保持原样
 * - useChatStream hook 用法不变
 * - ChatWindow 组件 API 不变（仅新增 bottomSlot）
 */
import React, { useEffect, useRef } from 'react';
import { ChatWindow } from '../components/Chat';
import { ChatHeader } from '../components/Chat/ChatHeader';
import { ContextMonitor } from '../components/Chat/ContextMonitor';
import { HistoryTab } from '../components/Chat/HistoryTab';
import { useChatStream } from '../hooks/useChatStream';
import { useChatStore } from '../store/useChatStore';
import { trackEvent, TrackEventName } from '../config/observability';
import './ChatPage.css';

const API_BASE = ''; // 同域，dev 模式下 vite proxy /api → 127.0.0.1:5678

const ChatPage: React.FC = () => {
  const {
    messages,
    setMessages,
    addMessage,
    inputValue,
    setInputValue,
    mood,
    setMood,
    sessions,
    setSessions,
    sessionId,
    setSessionId,
    loadingSessions,
    setLoadingSessions,
    setSystemStatus,
    addToast,
  } = useChatStore();

  const { state, send, reset } = useChatStream(API_BASE);
  const lastResponseRef = useRef('');

  // ─── 初始化 ───
  useEffect(() => {
    const savedId = localStorage.getItem('yunshu_session_id') || '';
    if (savedId) setSessionId(savedId);
    loadSessions(savedId);
    checkHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── streaming 完成 → 添加 assistant 消息 ───
  useEffect(() => {
    if (!state.streaming && state.text && state.text !== lastResponseRef.current) {
      lastResponseRef.current = state.text;
      addMessage({
        id: `assistant-${Date.now()}`,
        type: 'assistant',
        content: state.text,
        timestamp: new Date(),
        reasoning: state.reasoning || undefined,
        toolSteps: state.toolSteps.length > 0 ? state.toolSteps : undefined,
      });
      setMood('idle');
    }
    if (!state.streaming && state.error) {
      addToast('error', state.error);
      setMood('idle');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.streaming]);

  // ─── 会话切换 → 加载消息 ───
  useEffect(() => {
    if (sessionId) {
      loadMessages(sessionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    const start = Date.now();
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${sid}/messages`);
      if (!res.ok) {
        trackEvent(TrackEventName.DASHBOARD_LOAD, {
          module: 'messages',
          success: false,
          http_status: res.status,
          duration_ms: Date.now() - start,
        });
        if (res.status === 404) {
          localStorage.removeItem('yunshu_session_id');
          setSessionId('');
        }
        return;
      }
      const data = await res.json();
      trackEvent(TrackEventName.DASHBOARD_LOAD, {
        module: 'messages',
        success: true,
        duration_ms: Date.now() - start,
      });
      setMessages(
        (data || []).map((msg: Record<string, unknown>, i: number) => ({
          id: `msg-${i}-${(msg.timestamp as string) || Date.now()}`,
          type: msg.role === 'user' ? 'user' as const : 'assistant' as const,
          content: (msg.content as string) || '',
          timestamp: new Date(msg.timestamp as string),
          reasoning: msg.role === 'assistant' ? (msg.reasoning as string) || undefined : undefined,
          toolSteps: msg.role === 'assistant' ? (msg.tool_steps as []) || undefined : undefined,
        })),
      );
    } catch (e) {
      trackEvent(TrackEventName.DASHBOARD_LOAD, {
        module: 'messages',
        success: false,
        duration_ms: Date.now() - start,
      });
      console.error('加载消息失败:', e);
    }
  };

  const checkHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (res.ok) {
        setSystemStatus('online');
        fetch(`${API_BASE}/api/status`).catch(() => {});
      } else {
        setSystemStatus('offline');
      }
    } catch {
      setSystemStatus('offline');
    }
  };

  // ─── 动作 ───

  const handleSendMessage = (message: string) => {
    addMessage({
      id: `user-${Date.now()}`,
      type: 'user',
      content: message,
      timestamp: new Date(),
    });
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
    } catch {
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
    } catch {
      addToast('error', '切换会话失败');
    }
  };

  const handleDeleteSession = async (sid: string) => {
    if (!window.confirm('确定要删除此会话及其所有消息吗？')) return;
    try {
      await fetch(`${API_BASE}/api/sessions/${sid}`, { method: 'DELETE' });
      if (sid === sessionId) {
        setSessionId('');
        setMessages([]);
        localStorage.removeItem('yunshu_session_id');
      }
      await loadSessions();
      if (!sessionId) {
        await handleNewSession();
      }
      addToast('success', '会话已删除');
    } catch {
      addToast('error', '删除失败');
    }
  };

  const handleRenameSession = async (sid: string) => {
    const session = sessions.find((s) => s.id === sid);
    const newTitle = window.prompt('输入新标题:', session?.title || '');
    if (!newTitle || newTitle === session?.title) return;
    try {
      await fetch(`${API_BASE}/api/sessions/${sid}/rename`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
      await loadSessions();
      addToast('success', '已重命名');
    } catch {
      addToast('error', '重命名失败');
    }
  };

  // ─── 渲染 ───
  const displayMsgs = state.streaming
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

  const contextMonitorEl = <ContextMonitor />;

  return (
    <div className="chat-page">
      {/* 顶部：会话下拉 + toggle + 配置 */}
      <ChatHeader
        sessions={sessions}
        sessionId={sessionId}
        loadingSessions={loadingSessions}
        onSwitchSession={handleSwitchSession}
        onNewSession={handleNewSession}
        onRenameSession={handleRenameSession}
        onDeleteSession={handleDeleteSession}
      />

      {/* 中部+下部+底部+悬浮：chat-body 容器 */}
      <div className="chat-body">
        {/* 右内侧悬浮触发器 + 历史问话面板 */}
        <HistoryTab />

        {/* 消息列表 + ContextMonitor（通过 bottomSlot 注入） + ChatInput */}
        <ChatWindow
          messages={displayMsgs}
          onSendMessage={handleSendMessage}
          inputValue={inputValue}
          onInputChange={setInputValue}
          disabled={state.streaming}
          bottomSlot={contextMonitorEl}
        />
      </div>
    </div>
  );
};

export default ChatPage;
