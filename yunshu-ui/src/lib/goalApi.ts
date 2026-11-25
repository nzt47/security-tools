/**
 * 目标 API（E1-2d）
 * ------------------------------------------------
 * 与后端 `agent/server_routes/routes_goal.py` 一一对应；端点字面量在
 * `src/api/endpoints.ts`（本文件不写 `/api` 字面量）。
 */
import { request } from './apiClient';
import { GOAL_ACCEPT, GOAL_MODE, GOAL_STATE } from '@/api/endpoints';
import type { GoalState } from './sse';

/** 读目标状态（无目标时安全返回 idle） */
export async function getGoal(sessionId: string, signal?: AbortSignal): Promise<GoalState> {
  return request<GoalState>(
    `${GOAL_STATE}?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

/** 暂停 / 恢复 / 清除 */
export async function setGoalMode(
  sessionId: string,
  action: 'pause' | 'resume' | 'clear',
  reason?: string,
): Promise<GoalState> {
  return request<GoalState>(GOAL_MODE, {
    method: 'POST',
    body: { session_id: sessionId, action, ...(reason ? { reason } : {}) },
  });
}

/** 追加验收条件 */
export async function acceptGoal(sessionId: string, item: string): Promise<GoalState> {
  return request<GoalState>(GOAL_ACCEPT, {
    method: 'POST',
    body: { session_id: sessionId, item },
  });
}
