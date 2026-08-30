/**
 * useChatApp —— App 外壳的聊天/会话状态与逻辑（任务 T2.2）
 *
 * 职责：messages / inputValue / mood / toasts / sessions / sessionId /
 * loadingSessions / systemStatus + 流式回复（useChatStream）+ 会话管理。
 *
 * 说明【不易】：本文件内容与改造前 App.tsx 逐字一致（T2.2 之前逻辑内联在
 * App 组件中）；T2.2 只做「外壳插槽化」重构，状态与逻辑保留在 App（经此
 * hook 提供给 App），插槽组件通过 SlotHost props 消费 —— 不做 store 迁移。
 */
import { useState, useEffect, useRef } from 'react';
import type { Message } from '../components/Chat';
import type { ToastData } from '../components/Status';
import { useChatStream } from './useChatStream';
import { trackEvent, TrackEventName } from '../config/observability';

const API_BASE = '';  // 同域，空字符串

export function useChatApp() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [mood, setMood] = useState<'idle' | 'thinking' | 'happy' | 'excited'>('idle');
  const [toasts, setToasts] = useState<ToastData[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionId, setSessionId] = useState<string>('');
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [systemStatus, setSystemStatus] = useState<string>('offline');

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
    const startTime = performance.now();
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${sid}/messages`);
      const duration = performance.now() - startTime;
      if (!res.ok) {
        if (res.status === 404) {
          // 会话可能已被删除
          localStorage.removeItem('yunshu_session_id');
          setSessionId('');
        } else {
          // 非 404 错误记录失败埋点
          trackEvent(TrackEventName.DASHBOARD_LOAD, {
            module: 'messages',
            success: false,
            http_status: res.status,
            duration_ms: Number(duration.toFixed(2)),
          });
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
      trackEvent(TrackEventName.DASHBOARD_LOAD, {
        module: 'messages',
        success: true,
        duration_ms: Number(duration.toFixed(2)),
      });
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

  return {
    messages,
    inputValue,
    setInputValue,
    mood,
    toasts,
    sessions,
    sessionId,
    loadingSessions,
    systemStatus,
    streaming: state.streaming,
    handleSendMessage,
    handleNewSession,
    handleSwitchSession,
    handleCloseToast,
  };
}
