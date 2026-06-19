import { useState, useRef, useCallback, useEffect } from 'react';
import type { ToolStep } from '../components/Chat/ToolStepsDisplay';

export interface StreamState {
  /** 是否正在请求中 */
  streaming: boolean;
  /** 思考过程文本 */
  reasoning: string;
  /** 工具步骤列表 */
  toolSteps: ToolStep[];
  /** 最终回复文本 */
  text: string;
  /** 错误消息 */
  error: string | null;
  /** 思考标签 */
  thinkingLabel: string;
}

export interface UseChatStreamReturn {
  state: StreamState;
  /** 发送消息 */
  send: (message: string, sessionId?: string) => void;
  /** 重置状态 */
  reset: () => void;
  /** 中止当前请求 */
  abort: () => void;
}

/**
 * 对话 Hook — 调用 POST /api/chat 获取 JSON 响应
 * 将后端的 response / reasoning / tool_steps 映射到 StreamState
 */
export function useChatStream(baseUrl = ''): UseChatStreamReturn {
  const [state, setState] = useState<StreamState>({
    streaming: false,
    reasoning: '',
    toolSteps: [],
    text: '',
    error: null,
    thinkingLabel: '',
  });

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState({
      streaming: false,
      reasoning: '',
      toolSteps: [],
      text: '',
      error: null,
      thinkingLabel: '',
    });
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    setState(prev => ({ ...prev, streaming: false }));
  }, []);

  const send = useCallback((message: string, sessionId?: string) => {
    abortRef.current?.abort();

    setState({
      streaming: true,
      reasoning: '',
      toolSteps: [],
      text: '',
      error: null,
      thinkingLabel: '',
    });

    const controller = new AbortController();
    abortRef.current = controller;

    const run = async () => {
      try {
        // 构建 URL：session 以 query param 形式发送（兼容后端当前实现）
        const params = sessionId ? `?session=${encodeURIComponent(sessionId)}` : '';
        const url = `${baseUrl}/api/chat${params}`;

        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        });

        if (!response.ok) {
          const errBody = await response.json().catch(() => ({}));
          setState(prev => ({
            ...prev,
            streaming: false,
            error: errBody.error || `HTTP ${response.status}`,
          }));
          return;
        }

        const data = await response.json();

        setState({
          streaming: false,
          reasoning: data.reasoning || '',
          toolSteps: data.tool_steps || [],
          text: data.response || '',
          error: null,
          thinkingLabel: data.thinking_mode?.label || '',
        });
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          setState(prev => ({
            ...prev,
            streaming: false,
            error: err.message || '请求失败',
          }));
        }
      }
    };

    run();
  }, [baseUrl]);

  return { state, send, reset, abort };
}
