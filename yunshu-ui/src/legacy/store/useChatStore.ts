/**
 * 会话任务状态 — 从 App.tsx 迁出的全局 store
 *
 * 职责：messages / sessions / sessionId / mood / toasts / systemStatus
 * 目的：路由切换时保留会话状态，避免 ChatPage 卸载丢数据
 *
 * 不变量【不易】：Message 类型从 Chat 组件 re-export，ToastData 从 Status 组件 re-export，
 * 保证与现有组件 API 完全兼容，无需类型转换。
 *
 * 注意：流式状态（streaming/text/reasoning/toolSteps）仍由 useChatStream hook 管理，
 * Phase A 骨架阶段流式中断是已知限制，Phase B 优化为 Service Worker 后台保持。
 */
import { create } from 'zustand';
import type { Message } from '../components/Chat';
import type { ToastData } from '../components/Status';

export type { Message, ToastData };

export interface Session {
  id: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
}

export type Mood = 'idle' | 'thinking' | 'happy' | 'excited';

export type DisplayPrefKey = 'thinking' | 'toolcalls';

interface ChatState {
  messages: Message[];
  inputValue: string;
  mood: Mood;
  toasts: ToastData[];
  sessions: Session[];
  sessionId: string;
  loadingSessions: boolean;
  systemStatus: string;
  /** Thought 思考过程显示开关（持久化 localStorage:yunshu_display_prefs） */
  thinkingOn: boolean;
  /** 工具调用步骤显示开关（持久化 localStorage:yunshu_display_prefs） */
  toolcallsOn: boolean;

  setMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  addMessage: (msg: Message) => void;
  clearMessages: () => void;
  setInputValue: (v: string) => void;
  setMood: (m: Mood) => void;
  addToast: (type: ToastData['type'], message: string) => void;
  removeToast: (id: string) => void;
  setSessions: (updater: Session[] | ((prev: Session[]) => Session[])) => void;
  setSessionId: (id: string) => void;
  setLoadingSessions: (v: boolean) => void;
  setSystemStatus: (s: string) => void;
  setDisplayPref: (key: DisplayPrefKey, value: boolean) => void;
}

/** 从 localStorage 恢复显示偏好（与旧版 getDisplayPref 对齐） */
function loadDisplayPrefs(): { thinking: boolean; toolcalls: boolean } {
  try {
    const saved = JSON.parse(localStorage.getItem('yunshu_display_prefs') || '{}');
    return {
      thinking: saved.thinking !== false,
      toolcalls: saved.toolcalls !== false,
    };
  } catch {
    return { thinking: true, toolcalls: true };
  }
}

const initialPrefs = loadDisplayPrefs();

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  inputValue: '',
  mood: 'idle',
  toasts: [],
  sessions: [],
  sessionId: '',
  loadingSessions: true,
  systemStatus: 'offline',
  thinkingOn: initialPrefs.thinking,
  toolcallsOn: initialPrefs.toolcalls,

  setMessages: (updater) =>
    set((s) => ({
      messages: typeof updater === 'function' ? (updater as (p: Message[]) => Message[])(s.messages) : updater,
    })),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  clearMessages: () => set({ messages: [] }),
  setInputValue: (v) => set({ inputValue: v }),
  setMood: (m) => set({ mood: m }),
  addToast: (type, message) =>
    set((s) => ({ toasts: [...s.toasts, { id: Date.now().toString(), type, message }] })),
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setSessions: (updater) =>
    set((s) => ({
      sessions: typeof updater === 'function' ? (updater as (p: Session[]) => Session[])(s.sessions) : updater,
    })),
  setSessionId: (id) => set({ sessionId: id }),
  setLoadingSessions: (v) => set({ loadingSessions: v }),
  setSystemStatus: (s) => set({ systemStatus: s }),
  setDisplayPref: (key, value) => {
    set(key === 'thinking' ? { thinkingOn: value } : { toolcallsOn: value });
    try {
      const saved = JSON.parse(localStorage.getItem('yunshu_display_prefs') || '{}');
      saved[key] = value;
      localStorage.setItem('yunshu_display_prefs', JSON.stringify(saved));
    } catch { /* 静默 */ }
  },
}));
