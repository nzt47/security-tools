/**
 * hub 请求错误的可读性（2026-11-22）
 * ------------------------------------------------------------------
 * 校验类 400 回的是 {ok:false, errors:[...]}，没有 error/message 字段。
 * 旧实现只读 error/message ⇒ 界面只剩 "HTTP 400"，字段原因被吞掉
 * （新建 LLM 实例就是这么"无声失败"的）。这里锁住三条口径：
 *   errors[] 拼成原因；detail（RFC 9457）同义；两者都没有才保留 HTTP 400。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const { hubPost } = await import('./ui')

const respond = (body: unknown, status = 400) => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }) as unknown as Response))
}

afterEach(() => vi.unstubAllGlobals())

describe('hubPost 的错误文案', () => {
  it('400 + errors[] ⇒ 抛出字段原因（不是 HTTP 400）', async () => {
    respond({ ok: false, errors: ['API 端点 URL 不能为空', '提供商不能为空'] })
    await expect(hubPost('/api/llm/instances', {})).rejects.toThrow('API 端点 URL 不能为空；提供商不能为空')
  })

  it('400 + detail（RFC 9457）⇒ 透传 detail', async () => {
    respond({ type: 'about:blank', status: 400, detail: '实例名称已存在' })
    await expect(hubPost('/api/llm/instances', {})).rejects.toThrow('实例名称已存在')
  })

  it('400 没有任何原因 ⇒ 保留 HTTP 400（不编造）', async () => {
    respond({})
    await expect(hubPost('/api/llm/instances', {})).rejects.toThrow(/^HTTP 400$/)
  })
})
