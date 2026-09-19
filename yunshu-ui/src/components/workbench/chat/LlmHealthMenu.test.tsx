/**
 * LlmHealthMenu 测试 —— LLM 自检面板
 * ------------------------------------------------------------------
 * 需求背景：用户希望"不必开命令行就能执行我给的排查命令"。
 * 该面板把固定判据做成服务端一键自检：
 *   1. 打开即请求 POST /api/diagnostics/llm-check；
 *   2. 展示掩码后的配置（绝不显示密钥原文）；
 *   3. 展示真实探测结果与修复建议（hints）；
 *   4. 工作台会走演示模式时给出醒目提示（与对话流同一判据）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const { LlmHealthMenu } = await import('./LlmHealthMenu')

function installFetch(body: unknown, status = 200) {
  const calls: { url: string; method?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method })
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      } as unknown as Response
    }),
  )
  return calls
}

const OK_BODY = {
  ok: true,
  config: { provider: 'deepseek', model: 'deepseek-chat', base_url: 'https://api.deepseek.com/v1', api_key_masked: 'sk-ab***mnop', api_key_length: 33 },
  workbench_demo_mode: false,
  workbench_note: '当前 key 形态可用 ⇒ 对话会走真实 LLM',
  probe: { ok: true, http_status: 200, latency_ms: 320, endpoint: 'https://api.deepseek.com/v1/chat/completions', model: 'deepseek-chat', hints: [] },
  checked_at: '2026-09-19 23:40:00',
}

const BAD_KEY_BODY = {
  ok: false,
  config: { provider: 'DeepSeek', model: 'deepseek-v4-flash', base_url: 'https://api.deepseek.com/v1', api_key_masked: 'sk-te***cdef', api_key_length: 24 },
  workbench_demo_mode: true,
  workbench_note: '当前 key 会被工作台判为无效 ⇒ 对话走**演示模式**（固定文案，思考过程只有阶段事件）',
  probe: {
    ok: false, http_status: 401, latency_ms: 210,
    endpoint: 'https://api.deepseek.com/v1/chat/completions',
    error: 'HTTP 401',
    raw_message: '{"error":{"message":"Authentication Fails, Your api key: ****cdef is invalid"}}',
    hints: ['API Key 无效/过期（占位 key 常见形态：sk-test…）。请在 .env 更新 LLM_API_KEY 后重启服务。'],
  },
  checked_at: '2026-09-19 23:41:00',
}

describe('LlmHealthMenu', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('打开即自检，正常时显示"正常"与配置摘要', async () => {
    const calls = installFetch(OK_BODY)
    const { container } = render(<LlmHealthMenu />)
    fireEvent.click(screen.getByRole('button', { name: /LLM 自检/ }))
    await waitFor(() => expect(screen.getByText(/LLM 可用/)).toBeInTheDocument())
    expect(calls[0].url).toBe('/api/diagnostics/llm-check')
    expect(calls[0].method).toBe('POST')
    // 配置摘要行（model / base_url）+ 探测行都会出现模型名，故用包含断言
    expect(container.textContent).toContain('model')
    expect(screen.getAllByText(/deepseek-chat/).length).toBeGreaterThan(0)
    expect(screen.getByText(/sk-ab\*\*\*mnop/)).toBeInTheDocument()
    expect(screen.getByText('正常')).toBeInTheDocument()
  })

  it('key 无效时给出上游原文、演示模式提示与修复建议', async () => {
    const { container } = render(<LlmHealthMenu />)
    installFetch(BAD_KEY_BODY)
    fireEvent.click(screen.getByRole('button', { name: /LLM 自检/ }))
    await waitFor(() => expect(screen.getByText(/Authentication Fails/)).toBeInTheDocument())
    // 「工作台会用演示模式」在结论条与提示里各出现一次
    expect(screen.getAllByText(/演示模式/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/API Key 无效/)).toBeInTheDocument()
    expect(screen.getByText('异常')).toBeInTheDocument()
    expect(container.textContent).toContain('sk-te***cdef')
  })

  it('401 时提示去配置 API 令牌（写接口需要鉴权）', async () => {
    installFetch({ ok: false }, 401)
    render(<LlmHealthMenu />)
    fireEvent.click(screen.getByRole('button', { name: /LLM 自检/ }))
    await waitFor(() => expect(screen.getByText(/需要 API 令牌（401）/)).toBeInTheDocument())
  })

  it('响应体里不出现密钥原文（只显示掩码）', async () => {
    installFetch(OK_BODY)
    const { container } = render(<LlmHealthMenu />)
    fireEvent.click(screen.getByRole('button', { name: /LLM 自检/ }))
    await waitFor(() => expect(screen.getByText(/sk-ab\*\*\*mnop/)).toBeInTheDocument())
    expect(container.textContent).not.toMatch(/sk-[a-z]{30,}/)
  })
})
