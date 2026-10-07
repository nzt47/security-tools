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

import { CONTEXT_STATUS, CONTEXT_CONFIG, CONTEXT_COMPRESS } from '@/api/endpoints';
// 【P1-front 第七批】GET /api/context/status 已带 X-Envelope: v2；
// 【P1-front 第九批】POST /api/context/config 与 /api/context/compress 也已迁移。
// 本模块走的是 lib/apiClient.request()（它**不拆信封**、返回原始体），
// 故三个方法都用 unwrapEnvelopeBody 显式拆 —— 否则那些字段会**静默读到 undefined**
// （`percentage`/`status_level` 变成 undefined，面板不报错只是显示空）。
// 纪律与页面层同源：**只认信封**，不是信封体就抛错，不做「兼容两态」的宽解析。
import { unwrapEnvelopeBody } from '@/api/envelope';

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
  status: async (signal?: AbortSignal) =>
    unwrapEnvelopeBody<ContextStatus>(
      await request<unknown>(CONTEXT_STATUS, { signal }),
      CONTEXT_STATUS,
    ),

  saveConfig: async (config: ContextConfig) =>
    unwrapEnvelopeBody<{ ok: boolean }>(
      await request<unknown>(CONTEXT_CONFIG, { method: 'POST', body: config }),
      CONTEXT_CONFIG,
    ),

  compress: async () =>
    unwrapEnvelopeBody<CompressResult>(
      await request<unknown>(CONTEXT_COMPRESS, { method: 'POST' }),
      CONTEXT_COMPRESS,
    ),
};
