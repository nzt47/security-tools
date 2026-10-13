/**
 * ContextManagerBar · 会话级读数回归测试
 * ------------------------------------------------------------------
 * 2026-10-13 首版：轮数 / 调用频数 / tok·s⁻¹ / 缓存命中。
 * 2026-10-14 改为 **DeepSeek Harness 同构的三组读数**：
 *   会话统计（模型用时 / 工具调用用时 / 首 token 平均 / 输出速度）、
 *   Token 用量（缓存命中 / 未缓存输入 / 缓存读取 / 输出）、
 *   上下文已用（系统提示词 / 工具定义 / 对话消息）；
 * 并把「压缩次数」从**全局累计**改为**当前窗口**（compress_rounds_window）。
 * 数据源：/api/context/status（session_stats / context_breakdown / compress_rounds_window）
 *        + /api/llm-monitor/stats。
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
  // 展开状态持久化在 LocalStorage（见组件 STORAGE_KEY）⇒ 用例间必须隔离，
  // 否则第 2 个用例会带着"已展开"启动，expand() 反而把它折叠回去。
  localStorage.clear()
  envMock.mockResolvedValue({
    current_tokens: 12000,
    token_limit: 131072,
    percentage: 9.2,
    per_message_send_limit: 131072,
    per_message_recv_limit: 393216,
    compress_threshold: 0.8,
    compress_rounds: 7,
    compress_rounds_window: 2,
    session_stats: {
      turns: 3,
      model_ms: 12345,
      tool_ms: 4567,
      ttft_ms: 820,
      ttft_samples: 3,
    },
    context_breakdown: {
      window_tokens: 131072,
      system_tokens: 1234,
      tools_tokens: 6700,
      history_tokens: 12000,
    },
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

describe('ContextManagerBar · DSH 三组读数', () => {
  it('展开后显示 会话统计 / Token 用量 / 上下文已用 三组', async () => {
    render(<ContextManagerBar />)
    expand()

    await screen.findByText('会话统计')
    await waitFor(() => expect(screen.getByText('缓存命中')).toBeTruthy())

    expect(screen.getByText('Token 用量')).toBeTruthy()
    expect(screen.getByText('上下文已用')).toBeTruthy()

    // 会话统计：模型用时 12.3s / 工具 4.6s / TTFT 820ms / TPS 42.5
    expect(screen.getByText('模型用时')).toBeTruthy()
    expect(screen.getByText('12.3 s')).toBeTruthy()
    expect(screen.getByText('工具调用用时')).toBeTruthy()
    expect(screen.getByText('4.6 s')).toBeTruthy()
    expect(screen.getByText('首 token 平均（TTFT）')).toBeTruthy()
    expect(screen.getByText('820 ms')).toBeTruthy()
    expect(screen.getByText('输出速度（TPS）')).toBeTruthy()
    expect(screen.getByText('42.5 tok/s')).toBeTruthy()

    // Token 用量：命中 75% / 未缓存 1000 / 缓存读取 3000 / 输出 9000
    expect(screen.getByText('75%')).toBeTruthy()
    expect(screen.getByText('未缓存输入')).toBeTruthy()
    expect(screen.getByText('1,000')).toBeTruthy()
    expect(screen.getByText('缓存读取')).toBeTruthy()
    expect(screen.getByText('3,000')).toBeTruthy()
    expect(screen.getByText('输出')).toBeTruthy()
    expect(screen.getByText('9,000')).toBeTruthy()

    // 上下文已用：系统提示词 / 工具定义 / 对话消息
    expect(screen.getByText('系统提示词')).toBeTruthy()
    expect(screen.getByText('1,234 tok')).toBeTruthy()
    expect(screen.getByText('工具定义')).toBeTruthy()
    expect(screen.getByText('6,700 tok')).toBeTruthy()
    expect(screen.getByText('对话消息')).toBeTruthy()
    expect(screen.getByText('12,000 tok')).toBeTruthy()

    // 窗口概览 + 压缩次数取「当前窗口」(2)，不是全局累计 (7)
    expect(screen.getByText('窗口已用')).toBeTruthy()
    expect(screen.getByText('窗口 Token')).toBeTruthy()
    expect(screen.getByText('轮数')).toBeTruthy()
    expect(screen.getByText('当前窗口压缩')).toBeTruthy()
    expect(screen.getByText('2 次')).toBeTruthy()
    expect(screen.queryByText('7 次')).toBeNull()
    // 9% 折叠条与展开网格各出现一次 ⇒ 用 getAllByText
    expect(screen.getAllByText('9%').length).toBeGreaterThan(0)
  })

  it('机制说明开关已移除', async () => {
    render(<ContextManagerBar />)
    expand()
    await screen.findByText('会话统计')
    expect(screen.queryByText('机制说明')).toBeNull()
    expect(screen.queryByText('收起机制说明')).toBeNull()
  })
})
