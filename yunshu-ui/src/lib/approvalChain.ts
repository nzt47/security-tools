/**
 * 审批链路的**会话 + CSRF** 客户端（2026-09-22 修复）
 * ------------------------------------------------
 * 【修的是什么】工作台的「审批收件箱」原来**从不开启审批会话**，也不带 CSRF 头，
 * 而 `POST /api/cp/approvals/batch/link` 与 `/batch` 逐条复用单条审批链
 * （会话 → CSRF 双重提交 → 一次性链接 → 二次认证 → Actor 矩阵）⇒ 每次点"批准/驳回"
 * 都必然 401 `unknown_session`。也就是说那个收件箱**结构性批不了任何单**：
 * 单据挂得进来、人却永远批不动（与"找不到入口"是同一类断裂的下一段）。
 *
 * 【为什么"已有会话"用 `cp_approval_csrf` 判断，而不是 session cookie】
 * 后端把 `cp_approval_session` 设成 **HttpOnly**（JS 读不到，这是对的），
 * 而 `cp_approval_csrf` 是**故意可读**的（双重提交 Cookie 模式要求 JS 把它回填到
 * `X-CSRF-Token`）。两者同寿命、成对下发 ⇒ 用可读的那个当"手里有没有会话"的标记。
 *
 * 【为什么要"重开一次并重试"】审批会话与链接**只在后端进程内**、不落盘
 * （`approval_session.py`："重启即失效，fail-closed"）。后端一重启，浏览器手里的
 * Cookie 就指向一个不存在的会话 ⇒ 不自愈的话，人看到的是"审批按钮坏了"。
 * 重试**安全性**：可恢复的三个码都判在状态机**之前**，失败时裁决尚未发生，
 * 重放不会造成二次裁决；`session_mismatch` / `second_factor_*` 等真实状态冲突
 * **不重试**（重试也只会再错一次，还会掩盖原因）。
 */
import { ApiError, request } from './apiClient'

/** 与 `agent/security/approval_session.py` 的常量逐字一致（改后端必须同步改这里） */
export const SESSION_COOKIE = 'cp_approval_session'
export const CSRF_COOKIE = 'cp_approval_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

/** 会话相关的**可恢复**失败码：重开一次会话即可继续 */
const RECOVERABLE_CODES = new Set(['unknown_session', 'session_expired', 'csrf_mismatch'])

/** 读 Cookie（localStorage/Cookie 不可用时静默返回空串，绝不抛） */
function readCookie(name: string): string {
  try {
    const parts = ('; ' + document.cookie).split('; ' + name + '=')
    return parts.length === 2 ? decodeURIComponent(parts.pop()!.split(';').shift() || '') : ''
  } catch {
    return ''
  }
}

/** 供审批请求回填的 CSRF 头（没有令牌时返回空对象：让后端如实报 csrf_mismatch） */
export function approvalCsrfHeaders(): Record<string, string> {
  const token = readCookie(CSRF_COOKIE)
  return token ? { [CSRF_HEADER]: token } : {}
}

/** 后端是否认为我们手里有一个会话（HttpOnly 的 session cookie 读不到 ⇒ 用配对的可读 Cookie 判断） */
export function hasApprovalSession(): boolean {
  return readCookie(CSRF_COOKIE) !== ''
}

let opening: Promise<unknown> | null = null

/** 开启审批会话（并发调用**共享同一次请求**；响应把 session/csrf 写进 Cookie） */
export function openApprovalSession(): Promise<unknown> {
  if (!opening) {
    opening = request('/api/approval/session', { method: 'POST', body: {} }).finally(() => {
      opening = null
    })
  }
  return opening
}

/** 手里没有会话标记时先开一个（有则交给后端判定，失败由 withApprovalSession 兜底重开） */
export async function ensureApprovalSession(): Promise<void> {
  if (hasApprovalSession()) return
  await openApprovalSession()
}

function isRecoverableSessionError(e: unknown): boolean {
  return e instanceof ApiError && RECOVERABLE_CODES.has(e.code)
}

/**
 * 审批动作的统一包装：保证会话存在；命中可恢复码时**重开会话并重试一次**
 *
 * 用法（``fn`` 内部再取 ``approvalCsrfHeaders()``，确保拿到的是**本次**会话的令牌）：
 * ```
 *   return withApprovalSession(() => request(path, { method: 'POST', body, headers: approvalCsrfHeaders() }))
 * ```
 */
export async function withApprovalSession<T>(fn: () => Promise<T>): Promise<T> {
  await ensureApprovalSession()
  try {
    return await fn()
  } catch (e) {
    if (!isRecoverableSessionError(e)) throw e
    await openApprovalSession()
    return fn()
  }
}
