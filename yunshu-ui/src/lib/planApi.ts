/**
 * 计划模式 API（E1-1）
 * ------------------------------------------------
 * 与后端 `agent/server_routes/routes_plan_mode.py` 三条端点一一对应。
 * 端点字面量统一在 `src/api/endpoints.ts`（本文件不写 `/api` 字面量）。
 */
import { request } from './apiClient';
import {
  PLAN_MODE_DECISION,
  PLAN_MODE_SET,
  PLAN_MODE_STATE,
} from '@/api/endpoints';
import type { PlanState } from './sse';

export type PlanAction = 'approve' | 'reject' | 'feedback' | 'rollback';

export interface PlanDecisionResponse extends PlanState {
  ok: boolean;
  /** 决策后应由前端作为普通用户消息发送的内容（批准/拒绝/反馈才有） */
  next_message?: string | null;
}

/** 读计划状态（只读；无计划时返回 idle 默认态） */
export async function getPlan(sessionId: string, signal?: AbortSignal): Promise<PlanState> {
  return request<PlanState>(
    `${PLAN_MODE_STATE}?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

/** 进入 / 退出计划模式 */
export async function setPlanMode(sessionId: string, enabled: boolean): Promise<PlanState> {
  return request<PlanState>(PLAN_MODE_SET, {
    method: 'POST',
    body: { session_id: sessionId, enabled },
  });
}

/** 批准 / 拒绝 / 反馈 / 回滚 */
export async function decidePlan(
  sessionId: string,
  action: PlanAction,
  opts: { feedback?: string; version?: number } = {},
): Promise<PlanDecisionResponse> {
  return request<PlanDecisionResponse>(PLAN_MODE_DECISION, {
    method: 'POST',
    body: {
      session_id: sessionId,
      action,
      ...(opts.feedback ? { feedback: opts.feedback } : {}),
      ...(opts.version ? { version: opts.version } : {}),
    },
  });
}
