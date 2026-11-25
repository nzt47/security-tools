/**
 * 结构化提问 API（E1-3a）
 * ------------------------------------------------
 * 对应后端 `agent/server_routes/routes_ask.py`；端点字面量在 `src/api/endpoints.ts`。
 */
import { request } from './apiClient';
import { ASK_ANSWER, ASK_PENDING } from '@/api/endpoints';
import type { AskState } from './sse';

export interface AskAnswerResponse {
  ok: boolean;
  question_id: string;
  answers: string[];
  custom: string;
  /** 前端应作为普通用户消息发送的内容（与 plan/goal 的 next_message 同一模式） */
  next_message: string;
}

/** 读未答问题（刷新/切会话后恢复卡片） */
export async function getPendingAsk(
  sessionId: string,
  signal?: AbortSignal,
): Promise<AskState | null> {
  const res = await request<{ ok: boolean; pending: AskState | null }>(
    `${ASK_PENDING}?session_id=${encodeURIComponent(sessionId)}`,
    { signal },
  );
  return res.pending ?? null;
}

/** 回答问题（服务端记台账 + 落一条用户消息；返回 next_message） */
export async function answerAsk(
  sessionId: string,
  questionId: string,
  answers: string[],
  custom: string,
): Promise<AskAnswerResponse> {
  return request<AskAnswerResponse>(ASK_ANSWER, {
    method: 'POST',
    body: { session_id: sessionId, question_id: questionId, answers, custom },
  });
}
