/**
 * 会话智能 API（E1-4c）：轮次大纲 / 每轮统计 / 导出
 * ------------------------------------------------
 * 对应后端 `agent/server_routes/routes_session_insight.py`。
 * 导出走 `fetch` + `downloadFile`（**不能**用 `window.open`：/api 全站在 enforce 下需要 Bearer 令牌）。
 */
import { request } from './apiClient';
import { authHeader } from './apiToken';
import { sessionExport, sessionOutline, sessionStats } from '@/api/endpoints';
import { downloadFile } from '@/utils/system';

export interface OutlineRound {
  round: number;
  user: string;
  tools: string[];
  conclusion: string;
  ts: string;
  messages: number;
}

export interface SessionOutline {
  ok: boolean;
  session_id: string;
  title: string;
  rounds: OutlineRound[];
  round_count: number;
  truncated: boolean;
}

export interface StatsRound {
  round: number;
  tokens_in: number;
  tokens_out: number;
  tools: number;
  tool_names: string[];
  ts: string;
}

export interface SessionStats {
  ok: boolean;
  session_id: string;
  round_count: number;
  rounds: StatsRound[];
  totals: { tokens_in: number; tokens_out: number; tools: number; rounds: number };
  truncated: boolean;
}

/** 轮次大纲 */
export async function getOutline(sessionId: string, signal?: AbortSignal): Promise<SessionOutline> {
  return request<SessionOutline>(sessionOutline(sessionId), { signal });
}

/** 每轮统计 */
export async function getStats(sessionId: string, signal?: AbortSignal): Promise<SessionStats> {
  return request<SessionStats>(sessionStats(sessionId), { signal });
}

/**
 * 导出并触发下载（md / zip）。
 * 返回服务端给出的文件名（从 Content-Disposition 解析；拿不到就用兜底名）。
 */
export async function downloadSession(sessionId: string, format: 'md' | 'zip'): Promise<string> {
  const res = await fetch(sessionExport(sessionId, format), { headers: { ...authHeader() } });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`导出失败: HTTP ${res.status} ${body.slice(0, 120)}`);
  }
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const name = match?.[1] || `session.${format}`;
  const blob = await res.blob();
  downloadFile(name, [blob], format === 'zip' ? 'application/zip' : 'text/markdown');
  return name;
}
