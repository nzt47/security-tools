/**
 * 审批动作客户端（2026-10-13）—— 供对话内联审批弹窗使用
 * ------------------------------------------------------------------
 * 复用既有审批安全链（会话 → CSRF 双重提交 → 一次性链接 → 二次认证 → Actor 矩阵），
 * 但把"去审批面板、签链接、取确认码"这几步**收进一次点击**：
 *   approveRecord(recordId)  ⇒ 开/续会话 → 签一次性链接 → 批准（destructive 自动取确认码）
 *   rejectRecord(recordId, note) ⇒ 同上，但驳回（note 必填，审计要求）
 * 判定与安全边界**一字未改**，只是把同一链路包成一步给"对话里的点头"用。
 */
import { ApiError, request } from './apiClient'
import { approvalCsrfHeaders, withApprovalSession } from './approvalChain'

const BASE = '/api/approval'

function post(path: string, body: Record<string, unknown>) {
  // 每次请求现取 CSRF 头：会话可能刚被 openApprovalSession 重开
  return request(`${BASE}${path}`, { method: 'POST', body, headers: approvalCsrfHeaders() })
}

async function issueLink(recordId: string): Promise<string> {
  const r = (await post('/link', { record_id: recordId })) as { link?: { token?: string } }
  return r?.link?.token || ''
}

/** 批准一条审批记录（destructive 时自动签发并使用二次认证确认码）。 */
export async function approveRecord(recordId: string, note = ''): Promise<void> {
  if (!recordId) throw new Error('缺少审批单号')
  await withApprovalSession(async () => {
    const body: Record<string, unknown> = { link_token: await issueLink(recordId), note }
    try {
      await post(`/${encodeURIComponent(recordId)}/approve`, body)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'second_factor_required') {
        const sf = (await post('/second-factor', { record_id: recordId })) as { code?: string }
        body.second_factor = sf?.code || ''
        await post(`/${encodeURIComponent(recordId)}/approve`, body)
        return
      }
      throw e
    }
  })
}

/** 驳回一条审批记录（note 必填）。 */
export async function rejectRecord(recordId: string, note: string): Promise<void> {
  if (!recordId) throw new Error('缺少审批单号')
  await withApprovalSession(async () => {
    await post(`/${encodeURIComponent(recordId)}/reject`, {
      link_token: await issueLink(recordId), note,
    })
  })
}
