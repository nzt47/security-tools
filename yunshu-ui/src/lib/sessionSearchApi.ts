/**
 * 会话全文检索 API（E1-4b）
 * ------------------------------------------------
 * 对应后端 `agent/server_routes/routes_session_search.py`（FTS5 trigram 索引）。
 * 命中项是**消息粒度**：同一会话可能命中多条，UI 里按会话折叠。
 */
import { request } from './apiClient';
import { SESSION_SEARCH } from '@/api/endpoints';

export interface SessionSearchHit {
  session_id: string;
  seq: number;
  role: string;
  /** 命中片段；高亮用 `[` `]` 包住（与后端 snippet 同口径） */
  snippet: string;
  ts?: string;
}

export interface SessionSearchResponse {
  ok: boolean;
  /** match = FTS5 trigram；like = <3 字回落；empty = 空查询 */
  mode: 'match' | 'like' | 'empty' | 'disabled' | 'error' | string;
  query?: string;
  hits: SessionSearchHit[];
  took_ms?: number;
  error?: string;
}

/** 按关键词检索历史会话消息（q 为空时后端回空清单） */
export async function searchSessions(
  query: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<SessionSearchResponse> {
  return request<SessionSearchResponse>(
    `${SESSION_SEARCH}?q=${encodeURIComponent(query.trim())}&limit=${limit}`,
    { signal },
  );
}
