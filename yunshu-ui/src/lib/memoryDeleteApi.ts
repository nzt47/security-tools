/**
 * 长期记忆「按 id 删除」客户端（交接单 §9 T3）
 * ---------------------------------------------------------------
 * 契约来自 plugins/memory.py：
 *   POST   /api/memory/manual            → data.msg_id（没有它就无从定位删除）
 *   DELETE /api/memory/messages/<msg_id> → 默认 dry_run=true 回预览；confirm=true 才物理删
 *
 * 【为什么默认 dry-run 写死在请求体里】后端判据是 dry_run 的默认值 true +
 * confirm 必须显式为 true；这里把 dry_run 显式写出来，避免"漏传就当真删"。
 * 前端勾选只是二次确认的界面表达，**不是**唯一防线。
 */
import { authHeader } from './apiToken'
import {
  ENVELOPE_HEADER, ENVELOPE_VERSION, unwrapEnvelopeBody,
} from '../api/envelope'
import { memoryMessage } from '../api/endpoints'

export interface MemoryDeleteCheck {
  ok: boolean
  dry_run: true
  msg_id: string
  found: boolean
  role: string
  chars: number
  /** 前 80 字，供人确认"是不是这一条" */
  preview: string
  note: string
}

export interface MemoryDeleteResult {
  ok: boolean
  dry_run: false
  msg_id: string
  deleted: boolean
  audit?: { seq?: number }
  note?: string
}

export class MemoryDeleteError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'MemoryDeleteError'
    this.status = status
  }
}

async function problemMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string; title?: string }
    return body?.detail || body?.title || ('HTTP ' + res.status)
  } catch {
    return 'HTTP ' + res.status
  }
}

async function call(msgId: string, body: Record<string, unknown>): Promise<unknown> {
  const res = await fetch(memoryMessage(msgId), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new MemoryDeleteError(await problemMessage(res), res.status)
  const version = res.headers.get(ENVELOPE_HEADER)
  if (version !== ENVELOPE_VERSION) {
    throw new MemoryDeleteError('DELETE ' + memoryMessage(msgId) + ' 未声明统一信封（实测 '
      + ENVELOPE_HEADER + '=' + version + '）', res.status)
  }
  return unwrapEnvelopeBody<unknown>(await res.json(), memoryMessage(msgId))
}

/** 先看一眼（dry-run）：返回 preview 与 note，不删任何东西。 */
export async function checkMemoryDelete(msgId: string): Promise<MemoryDeleteCheck> {
  return (await call(msgId.trim(), { dry_run: true })) as MemoryDeleteCheck
}

/** 真删：必须 confirm=true（缺了后端会回 400，这里也显式传）。 */
export async function confirmMemoryDelete(msgId: string): Promise<MemoryDeleteResult> {
  return (await call(msgId.trim(), { dry_run: false, confirm: true })) as MemoryDeleteResult
}
