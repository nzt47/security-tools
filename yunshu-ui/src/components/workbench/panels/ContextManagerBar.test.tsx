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
const postMock = vi.hoisted(() => vi.fn(async (): Promise<Record<string, unknown>> => ({ ok: true })))
vi.mock('@/api/envelope', () => ({
  getEnvelope: envMock,
  postEnvelope: postMock,
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
    // 【2026-11-09】current_tokens 的语义改成「最近 200 条正文」（与压缩触发同源），
    // 会话累计单列 session_total_tokens。
    current_tokens: 800,
    session_total_tokens: 12000,
    percentage_note: '分子 = 本会话最近 200 条正文 token（与压缩触发同一口径；工具结果不计入）；会话累计见 session_total_tokens。',
    percentage_window_messages: 200,
    percentage_semantics: 'compress_trigger_window',
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
    // 【2026-11-10】未分会话历史池：冻结口径（不参与压缩/摘要/最近消息）
    legacy_pool: {
      available: true,
      policy: 'frozen',
      legacy_messages: 305,
      legacy_chars: 436675,
      messages_total: 415,
      note: '未分会话历史池：会话分家前写入、没有 session_id 的消息。已冻结——不参与任何会话的压缩触发、摘要与最近消息窗口，也不再写入。',
    },
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
    // 「2 次」现在出现在两处：窗口概览格子 + 常驻的机制说明正文（① 当前窗口已压缩 2 次）
    expect(screen.getAllByText('2 次').length).toBeGreaterThan(0)
    // 累计摘要版本只在机制说明里以「（累计摘要版本 7）」出现，不再作为「7 次」展示
    expect(screen.queryByText('7 次')).toBeNull()
    // 9% 折叠条与展开网格各出现一次 ⇒ 用 getAllByText
    expect(screen.getAllByText('9%').length).toBeGreaterThan(0)
  })

  it('占用率与压缩触发同源：口径说明 + 会话累计同屏', async () => {
    render(<ContextManagerBar />)
    expand()
    await screen.findByText('会话统计')
    // 后端给的 percentage_note 原样上屏（不前端另造一句）
    expect(screen.getByText(/与压缩触发同一口径/)).toBeTruthy()
    // 会话累计单列，避免把「累计」误读成「这一窗用了多少」
    expect(screen.getByText(/会话累计/)).toBeTruthy()
    expect(screen.getByText('12,000')).toBeTruthy()
  })

  it('手动压缩回报「这次走的是哪条路」：复用前缀 / 独立前缀', async () => {
    postMock.mockResolvedValueOnce({
      ok: true, summary_version: 95, messages_compressed: 100,
      used_prefix: true, prefix_messages: 188,
    })
    render(<ContextManagerBar />)
    expand()
    await screen.findByText('会话统计')
    fireEvent.click(screen.getByRole('button', { name: /手动压缩/ }))
    expect(await screen.findByText(/复用对话前缀（188 条同前缀消息，命中价）/)).toBeTruthy()
  })

  it('未分会话历史池：按「冻结」披露条数，不折算成 0', async () => {
    render(<ContextManagerBar />)
    expand()
    await screen.findByText('会话统计')
    expect(screen.getByText(/未分会话历史池/)).toBeTruthy()
    expect(screen.getByText(/已冻结（不参与压缩\/摘要\/最近消息）/)).toBeTruthy()
    expect(screen.getByText('305')).toBeTruthy()
  })

  it('机制说明：开关已移除，但说明正文常驻', async () => {
    render(<ContextManagerBar />)
    expand()
    await screen.findByText('会话统计')
    // 开关（折叠条快捷入口 + 面板内按钮）已移除
    expect(screen.queryByText('机制说明')).toBeNull()
    expect(screen.queryByText('收起机制说明')).toBeNull()
    // 【2026-10-14】正文改为常驻：不点任何东西也应该看得到
    expect(screen.getByText(/上下文管理机制/)).toBeTruthy()
    expect(screen.getByText(/异步后台压缩/)).toBeTruthy()
  })
})
