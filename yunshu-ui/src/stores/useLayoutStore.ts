/**
 * 工作台全局状态（Zustand）
 * ------------------------------------------------
 * 职责分层：
 *  - layout   ：Mosaic 布局树 —— 唯一持久化字段（LocalStorage）
 *  - messages ：对话消息，含流式增量（内存态）
 *  - thinking ：右侧"思考过程"面板的推理/工具事件流
 * 持久化仅针对 layout，借助 persist 中间件 + sanitizeLayout 防御脏数据。
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { MosaicNode } from 'react-mosaic-component';
import { DEFAULT_LAYOUT, LAYOUT_STORAGE_KEY, sanitizeLayout, type PanelId } from '../lib/mosaic';
import { createChatStream, type ThinkingStatus } from '../lib/sse';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
  status?: 'streaming' | 'done' | 'error';
}

export interface ThinkingEvent {
  id: string;
  title: string;
  detail?: string;
  status: ThinkingStatus;
  at: number;
}

interface LayoutStore {
  layout: MosaicNode<PanelId> | null;
  messages: ChatMessage[];
  thinking: ThinkingEvent[];
  streaming: boolean;
  activeStreamId: string | null;

  setLayout: (next: MosaicNode<PanelId> | null) => void;
  resetLayout: () => void;
  sendMessage: (text: string) => Promise<void>;
  stopStreaming: () => void;
  clearConversation: () => void;
}

// 仅暴露给 persist 的字段（layout 之外的动态数据不做本地持久化）
type PersistedState = Pick<LayoutStore, 'layout'>;

// 模拟流的中止控制器（非响应式，仅控制生成器）
let abortController: AbortController | null = null;

// ─── 流式过程日志订阅（供 ChatPanel 打印排查断流/乱序，非响应式） ───
export type StreamLogKind = 'send' | 'thinking' | 'chunk' | 'done' | 'error' | 'abort';

export interface StreamLogEvent {
  kind: StreamLogKind;
  ts: number;
  streamId?: string;
  seq?: number;
  text?: string;
  accumulated?: number;
  title?: string;
  status?: ThinkingStatus;
  detail?: string;
}

type StreamLogListener = (e: StreamLogEvent) => void;

const streamLogListeners = new Set<StreamLogListener>();

/** 订阅流式过程日志，返回取消订阅函数 */
export function subscribeStreamLog(listener: StreamLogListener): () => void {
  streamLogListeners.add(listener);
  return () => streamLogListeners.delete(listener);
}

function emitStreamLog(event: StreamLogEvent) {
  streamLogListeners.forEach((fn) => fn(event));
}

export const useLayoutStore = create<LayoutStore>()(
  persist(
    (set, get) => ({
      layout: null,
      messages: [],
      thinking: [],
      streaming: false,
      activeStreamId: null,

      setLayout: (next) => set({ layout: next }),

      resetLayout: () => set({ layout: DEFAULT_LAYOUT }),

      sendMessage: async (text) => {
        const { streaming } = get();
        if (streaming || !text.trim()) return;

        const userMsg: ChatMessage = {
          id: `user-${Date.now()}`,
          role: 'user',
          content: text.trim(),
          createdAt: Date.now(),
          status: 'done',
        };
        const streamId = `asst-${Date.now()}`;
        const assistantMsg: ChatMessage = {
          id: streamId,
          role: 'assistant',
          content: '',
          createdAt: Date.now(),
          status: 'streaming',
        };

        set((state) => ({
          messages: [...state.messages, userMsg, assistantMsg],
          streaming: true,
          activeStreamId: streamId,
          thinking: [],
        }));

        emitStreamLog({ kind: 'send', ts: Date.now(), streamId, detail: text });

        abortController = new AbortController();
        const upsertThinking = (evt: {
          id: string;
          title: string;
          detail?: string;
          status: ThinkingStatus;
        }) => {
          emitStreamLog({
            kind: 'thinking',
            ts: Date.now(),
            streamId,
            title: evt.title,
            status: evt.status,
            detail: evt.detail,
          });
          set((state) => {
            const nextEvent: ThinkingEvent = { ...evt, at: Date.now() };
            const exists = state.thinking.some((t) => t.id === evt.id);
            return {
              thinking: exists
                ? state.thinking.map((t) => (t.id === evt.id ? nextEvent : t))
                : [...state.thinking, nextEvent],
            };
          });
        };

        let accumulated = 0;
        try {
          for await (const event of createChatStream(text, abortController.signal)) {
            if (event.type === 'chunk') {
              accumulated += event.text.length;
              emitStreamLog({
                kind: 'chunk',
                ts: Date.now(),
                streamId,
                seq: event.seq,
                text: event.text,
                accumulated,
              });
              set((state) => ({
                messages: state.messages.map((m) =>
                  m.id === streamId ? { ...m, content: m.content + event.text } : m,
                ),
              }));
            } else if (event.type === 'thinking') {
              upsertThinking(event);
            } else if (event.type === 'done') {
              break;
            }
          }
          emitStreamLog({ kind: 'done', ts: Date.now(), streamId, accumulated });
          set((state) => ({
            messages: state.messages.map((m) =>
              m.id === streamId ? { ...m, status: 'done' as const } : m,
            ),
            streaming: false,
            activeStreamId: null,
          }));
        } catch (err) {
          // 主动停止（AbortError）不视为错误，保留已生成内容
          const isAbort = err instanceof DOMException && err.name === 'AbortError';
          emitStreamLog({
            kind: isAbort ? 'abort' : 'error',
            ts: Date.now(),
            streamId,
            accumulated,
            detail: err instanceof Error ? err.message : String(err),
          });
          set((state) => ({
            messages: state.messages.map((m) =>
              m.id === streamId
                ? { ...m, status: (isAbort ? 'done' : 'error') }
                : m,
            ),
            streaming: false,
            activeStreamId: null,
          }));
        } finally {
          abortController = null;
        }
      },

      stopStreaming: () => {
        abortController?.abort();
      },

      clearConversation: () => {
        abortController?.abort();
        set({ messages: [], thinking: [], streaming: false, activeStreamId: null });
      },
    }),
    {
      name: LAYOUT_STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state): PersistedState => ({ layout: state.layout }),
      // 反序列化校验：非法布局回退默认，保证"刷新不丢布局"且不白屏
      merge: (persisted, current) => {
        const saved = persisted as Partial<PersistedState>;
        return {
          ...current,
          layout: saved.layout ? sanitizeLayout(saved.layout) ?? null : null,
        };
      },
    },
  ),
);
