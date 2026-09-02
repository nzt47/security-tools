/**
 * 历史问话 API 客户端
 *
 * 对接后端 app_server.py / agent/server_routes/routes_sessions.py 的 /api/history* 端点：
 * - GET    /api/history            → 指定会话（默认当前会话）的问答对列表（user + Yunshu + timestamp）
 * - DELETE /api/history/{index}    → 删除指定 _real_index 的问话记录
 *
 * 工作台多会话场景下需显式传 `session` 查询参数，把历史问话面板限定在
 * 会话任务页当前选中的会话内（旧版 ChatPage 仅一个会话，可省略）。
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
  /**
   * 拉取会话问答对列表。
   * @param opts.session 会话 ID（缺省时后端取全局当前会话）
   * @param opts.signal  取消信号
   */
  list: (opts?: { session?: string; signal?: AbortSignal }) =>
    request<HistoryEntry[]>(
      `/api/history${opts?.session ? `?session=${encodeURIComponent(opts.session)}` : ''}`,
      { signal: opts?.signal },
    ),

  /**
   * 删除指定 _real_index 的问话记录（连带其回复）。
   * @param index   问答对真实索引（后端 `_real_index`）
   * @param session 会话 ID（缺省时后端取全局当前会话）
   */
  delete: (index: number, session?: string) =>
    request<{ ok: boolean }>(
      `/api/history/${index}${session ? `?session=${encodeURIComponent(session)}` : ''}`,
      { method: 'DELETE' },
    ),
};
