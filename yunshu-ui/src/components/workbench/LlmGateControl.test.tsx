/**
 * LlmGateControl（顶栏 LLM 禁用时段）行为测试（2026-11-20）
 * ------------------------------------------------------------------
 * 钉住：开关读写 /api/llm-gate；配置弹窗能增删时段并保存；读数失败不假装可用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import LlmGateControl from './LlmGateControl'

const fetchMock = vi.fn()

const CONFIG = {
  enabled: true,
  windows: [{ days: [0, 1, 2, 3, 4], start: '09:00', end: '12:00' }],
  skip_holidays: true,
  lead_seconds: 300,
}

const STATE = {
  enabled: true,
  blocked: false,
  now: '2026-11-20T13:00:00+08:00',
  weekday_cn: '周五',
  holiday: false,
  window: null,
  next_block: '2026-11-20T14:00',
  next_unblock: null,
  seconds_until_blocked: 3600,
  message: '当前允许 LLM 请求；下一个禁用时段：2026-11-20 14:00。',
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

function install(overrides: { config?: unknown; state?: unknown } = {}) {
  fetchMock.mockImplementation(async (_input: unknown, init?: RequestInit) => {
    if ((init?.method || 'GET') === 'GET') {
      return jsonResponse({ ok: true, config: overrides.config ?? CONFIG, state: overrides.state ?? STATE })
    }
    return jsonResponse({ ok: true, config: overrides.config ?? CONFIG, state: overrides.state ?? STATE })
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  install()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('LLM 禁用时段 · 顶栏', () => {
  it('开关反映配置，拨动后 POST enabled', async () => {
    render(<LlmGateControl />)
    const sw = await screen.findByRole('switch', { name: 'LLM 禁用时段' })
    expect(sw.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(sw)
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST')
      expect(post).toBeTruthy()
      expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ enabled: false })
    })
  })

  it('配置弹窗可增删时段并保存', async () => {
    render(<LlmGateControl />)
    fireEvent.click(await screen.findByRole('button', { name: '设置 LLM 禁用时段' }))

    expect(await screen.findByText('禁用时段（北京时间）')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /添加时段/ }))
    expect(screen.getByLabelText('第 2 条时段开始时间')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => {
      const post = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST')
      expect(post).toBeTruthy()
      const body = JSON.parse(String((post?.[1] as RequestInit).body))
      expect(body.windows.length).toBe(2)
      expect(body.lead_seconds).toBe(300)
    })
  })
})
