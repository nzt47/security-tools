/**
 * 历史问话 API 客户端
 *
 * 对接后端 app_server.py 的 /api/history* 端点：
 * - GET    /api/history          → 当前会话的问答对列表（user + Yunshu + timestamp）
 * - DELETE /api/history/{index}  → 删除指定 _real_index 的问话记录
 *
 * 不变量【不易】：字段名与后端 api_history 返回值严格对齐（user/Yunshu/_real_index）
 */
import { request } from './apiClient';

export interface HistoryEntry {
  user: string;
  Yunshu: string;
  mode: string;
  timestamp: string;
  _real_index: number;
}

export const historyApi = {
  list: (signal?: AbortSignal) =>
    request<HistoryEntry[]>('/api/history', { signal }),

  delete: (index: number) =>
    request<{ ok: boolean }>(`/api/history/${index}`, { method: 'DELETE' }),
};
