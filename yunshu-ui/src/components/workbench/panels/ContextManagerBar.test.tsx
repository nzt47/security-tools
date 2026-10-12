/**
 * ContextManagerBar · 会话级读数回归测试（2026-10-13）
 * ------------------------------------------------------------------
 * 需求：上下文管理面板要像 DeepSeek Harness 一样显示
 *   轮数 / 调用频数 / tok·s⁻¹ / tok 总数 / 缓存命中 / 窗口已用。
 * 这里把「两个数据源合并上屏」钉住：/api/context/status（turns）+
 * /api/llm-monitor/stats（total / tok_per_s / total_tokens / cache_hit_ratio）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const envMock = vi.hoisted(() => vi.fn())
vi.mock('@/api/envelope', () => ({
  getEnvelope: envMock,
  postEnvelope: vi.fn(async () => ({ ok: true })),
}))

const hubGetMock = vi.hoisted(() => vi.fn())
vi.mock('../../../pages/hub/components/ui', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, hubGet: hubGetMock }
})

import { ContextManagerBar } from './ContextManagerBar'

beforeEach(() => {
  envMock.mockResolvedValue({
    current_tokens: 12000,
    token_limit: 131072,
    percentage: 9.2,
    per_message_send_limit: 131072,
    per_message_recv_limit: 393216,
    compress_threshold: 0.8,
    compress_rounds: 2,
    status_level: 'ok',
    send_tokens: 5000,
    recv_tokens: 7000,
    messages_count: 20,
    turns: 10,
  })
  hubGetMock.mockResolvedValue({
    total: 7,
    total_tokens: 34800,
    total_response_tokens: 9000,
    tok_per_s: 42.5,
    cache_hit_tokens: 3000,
    cache_miss_tokens: 1000,
    cache_reported_calls: 5,
    cache_hit_ratio: 0.75,
  })
})

afterEach(() => {
  cleanup()
  envMock.mockReset()
  hubGetMock.mockReset()
})

const expand = () => {
  const toggle = document.querySelector('button[title="点击展开/折叠上下文监视器"]')
  expect(toggle).toBeTruthy()
  fireEvent.click(toggle as HTMLElement)
}

describe('ContextManagerBar · 会话级读数', () => {
  it('展开后显示 轮数 / 调用频数 / tok/s / tok 总数 / 缓存命中 / 窗口已用', async () => {
    render(<ContextManagerBar />)
    expand()

    // 两路数据都异步到齐后才渲染表格内容
    await screen.findByText('窗口已用')
    await waitFor(() => expect(screen.getByText('7 次')).toBeTruthy())

    expect(screen.getByText('窗口已用')).toBeTruthy()
    expect(screen.getByText('窗口 Token')).toBeTruthy()
    expect(screen.getByText('轮数')).toBeTruthy()
    expect(screen.getByText('调用频数')).toBeTruthy()
    expect(screen.getByText('tok/s')).toBeTruthy()
    expect(screen.getByText('tok 总数')).toBeTruthy()
    expect(screen.getByText('缓存命中')).toBeTruthy()

    // 值：turns=10 / 频数=7 / tok/s=42.5 / 总 tokens=34800 / 缓存=75% / 窗口=9%
    expect(screen.getByText('10')).toBeTruthy()
    expect(screen.getByText('42.5')).toBeTruthy()
    expect(screen.getByText('34,800')).toBeTruthy()
    expect(screen.getByText('75%')).toBeTruthy()
    // 9% 折叠条与展开网格各出现一次 ⇒ 用 getAllByText
    expect(screen.getAllByText('9%').length).toBeGreaterThan(0)
  })
})
