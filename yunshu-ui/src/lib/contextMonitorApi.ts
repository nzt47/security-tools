/**
 * 上下文监视器 API 客户端
 *
 * 对接后端 app_server.py 的 /api/context/* 端点：
 * - GET  /api/context/status   → 上下文 token 用量、压缩次数、最近消息
 * - POST /api/context/config   → 保存 token_limit / send_limit / recv_limit
 * - POST /api/context/compress → 手动压缩，返回释放的 token 数
 *
 * 不变量【不易】：字段名与后端 api_context_status 返回值严格对齐
 */
import { request } from './apiClient';

export type ContextStatusLevel = 'ok' | 'info' | 'warning' | 'critical';

export interface RecentMessage {
  role: string;
  tokens: number;
  content_preview: string;
}

export interface ContextStatus {
  current_tokens: number;
  token_limit: number;
  percentage: number;
  per_message_send_limit: number;
  per_message_recv_limit: number;
  compress_threshold: number;
  compress_rounds: number;
  status_level: ContextStatusLevel;
  send_tokens: number;
  recv_tokens: number;
  messages_count: number;
  recent_messages: RecentMessage[];
}

export interface ContextConfig {
  token_limit?: number;
  per_message_send_limit?: number;
  per_message_recv_limit?: number;
}

export interface CompressResult {
  ok: boolean;
  freed_tokens?: number;
  error?: string;
}

export const contextMonitorApi = {
  status: (signal?: AbortSignal) =>
    request<ContextStatus>('/api/context/status', { signal }),

  saveConfig: (config: ContextConfig) =>
    request<{ ok: boolean }>('/api/context/config', {
      method: 'POST',
      body: config,
    }),

  compress: () =>
    request<CompressResult>('/api/context/compress', { method: 'POST' }),
};
