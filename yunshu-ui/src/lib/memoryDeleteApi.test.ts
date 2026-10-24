/** 按 id 删除长期记忆的客户端：默认 dry-run / 信封解析 / 错误 detail 透出 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  MemoryDeleteError, checkMemoryDelete, confirmMemoryDelete,
} from './memoryDeleteApi'

const ENVELOPE = { 'X-Envelope': 'v2', 'Content-Type': 'application/json' }

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = ENVELOPE) {
  return new Response(JSON.stringify(body), { status, headers })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('memoryDeleteApi', () => {
  it('查一下：带 dry_run=true，并把信封里的 preview 取出来', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      code: 200,
      message: '',
      data: {
        ok: true, dry_run: true, msg_id: '2026-10-25T00:00:00.000001+00:00',
        found: true, role: 'user', chars: 12, preview: '要被删的正文…', note: '摘要可能仍留痕',
      },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const d = await checkMemoryDelete(' 2026-10-25T00:00:00.000001+00:00 ')
    expect(d.preview).toContain('要被删的正文')
    expect(d.msg_id).toBe('2026-10-25T00:00:00.000001+00:00')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    // encodeURIComponent 会转义 ':'（%3A）与时区里的 '+'（%2B）——后者若漏转义，后端会把 '+' 当空格
    expect(url).toBe('/api/memory/messages/2026-10-25T00%3A00%3A00.000001%2B00%3A00')
    expect(init.method).toBe('DELETE')
    expect(JSON.parse(String(init.body))).toEqual({ dry_run: true })
  })

  it('确认删除：显式带 dry_run=false 与 confirm=true', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      code: 200, message: '',
      data: { ok: true, dry_run: false, msg_id: 'm1', deleted: true, audit: { seq: 9100 } },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const r = await confirmMemoryDelete('m1')
    expect(r.deleted).toBe(true)
    expect(r.audit?.seq).toBe(9100)
    const init = (fetchMock.mock.calls[0] as [string, RequestInit])[1]
    expect(JSON.parse(String(init.body))).toEqual({ dry_run: false, confirm: true })
  })

  it('缺 confirm 的 400：把 RFC 9457 的 detail 透给界面（而不是 HTTP 400）', async () => {
    // 每次调用都给一个**新的** Response（Response 的 body 只能读一次）
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({
      type: 'https://yunshu.local/problems/confirmation-required',
      title: '需要显式确认',
      detail: '物理删除不可恢复：先 dry_run=true 看 preview，再传 confirm=true',
      status: 400,
    }, 400, { 'Content-Type': 'application/problem+json' }))))

    await expect(confirmMemoryDelete('m1')).rejects.toBeInstanceOf(MemoryDeleteError)
    await expect(confirmMemoryDelete('m1')).rejects.toThrow(/先 dry_run=true/)
  })

  it('没带信封头的成功响应要报错（不允许静默读 undefined）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ ok: true }, 200, { 'Content-Type': 'application/json' }),
    ))
    await expect(checkMemoryDelete('m1')).rejects.toThrow(/未声明统一信封/)
  })
})
