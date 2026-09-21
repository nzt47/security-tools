/**
 * LlmMonitorPanel 需求回归测试 —— 需求③（LLM 通信监控）的行内行为锁定
 * ------------------------------------------------------------------
 * 该组件此前**没有任何单元测试**（整个 prompt-lab 只有一个 promptLab.test.tsx，
 * 且只覆盖 FactorControl / FactorCard / RadarChart），故新增本文件，把需求③
 * 的四项行为钉成回归：
 *
 *   1. 折叠面板：每次通信一行，点击行头展开当次详细收发内容，再点收起；
 *   2. 时间显示 → token 数量：行头显示本次通信 Token（▲发送 ▾接收），
 *      **不再显示时间戳**（即使记录里带着 timestamp_str）；
 *   3. 会话持久化：后端在服务关闭时落盘的最后一条通信回填后标注「上次会话」，
 *      并在说明文案中给出快照时间（数据源 /api/llm-monitor/stats.persisted）；
 *   4. 统计条：发送/接收 Tokens 汇总可见。
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import LlmMonitorPanel from './LlmMonitorPanel'

const h = vi.hoisted(() => ({
  records: [] as any[],
  stats: {} as any,
}))

vi.mock('../hub/components/ui', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    hubGet: vi.fn(async (url: string) => {
      if (url.startsWith('/api/llm-monitor/stats')) return h.stats
      if (url.startsWith('/api/llm-monitor/records')) return { records: h.records, total: h.records.length }
      return {}
    }),
    hubPost: vi.fn(async () => ({ ok: true })),
  }
})

const RECORD = {
  id: 'r1',
  model: 'deepseek-v4-flash',
  provider: 'deepseek',
  source: 'chat',
  duration_ms: 812,
  request_tokens: 1200,
  response_tokens: 340,
  total_tokens: 1540,
  // 记录里**带着**时间戳：用于断言行头不再显示它（需求：时间 → token）
  timestamp: 1789773504,
  timestamp_str: '2026-09-19 23:00:00',
  timestamp_full: '2026-09-19 23:00:00.123',
  system_prompt: 'SYSTEM-PROMPT-MARKER',
  messages: [{ role: 'user', content: 'USER-MSG-MARKER' }],
  response_text: 'RESP-MARKER',
  restored: true,
}

beforeEach(() => {
  h.records = [RECORD]
  h.stats = {
    enabled: true,
    total: 1,
    avg_duration_ms: 812,
    total_request_tokens: 1200,
    total_response_tokens: 340,
    total_tokens: 1540,
    estimated_cost_usd: 0.00042,
    max_records: 500,
    buffer_usage: '1/500',
    persisted: { file: 'llm_monitor_last.json', persisted_at: '2026-09-19 23:05:00' },
  }
})

afterEach(cleanup)

/** 行头按钮（折叠面板的 clickable header） */
const rowHead = () => document.querySelector('.lm-record-head') as HTMLElement

describe('需求③：LLM 通信监控（折叠面板 / Token 替代时间 / 会话持久化）', () => {
  it('行头显示本次通信 Token 数量，且不再显示时间戳', async () => {
    render(<LlmMonitorPanel />)
    const head = await waitFor(() => {
      const el = rowHead()
      expect(el).toBeTruthy()
      return el
    })

    // ▲发送 1.2k / ▾接收 340（Token 数量）
    const tokens = head.querySelector('.lm-tokens') as HTMLElement
    expect(tokens).toBeTruthy()
    expect(tokens.getAttribute('title')).toBe('本次通信 Token：发送 1200 / 接收 340')
    expect(head.textContent).toContain('1.2k')
    expect(head.textContent).toContain('340')

    // 需求：时间显示已替换为 token 数量 ⇒ 行头不得出现任何时间戳
    expect(head.textContent).not.toContain('2026-09-19')
    expect(head.textContent).not.toContain('23:00:00')
  })

  it('折叠面板：点击行头展开当次详细收发内容，再点收起', async () => {
    render(<LlmMonitorPanel />)
    await waitFor(() => expect(rowHead()).toBeTruthy())

    // 初始折叠：详情里的内容不在文档中
    expect(rowHead().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('SYSTEM-PROMPT-MARKER')).toBeNull()
    expect(screen.queryByText('RESP-MARKER')).toBeNull()

    // 展开
    fireEvent.click(rowHead())
    await waitFor(() => expect(rowHead().getAttribute('aria-expanded')).toBe('true'))
    expect(screen.getByText('SYSTEM-PROMPT-MARKER')).toBeTruthy()
    expect(screen.getByText('USER-MSG-MARKER')).toBeTruthy()
    expect(screen.getByText('RESP-MARKER')).toBeTruthy()
    // 详情看板标题带上本次 token 数
    expect(screen.getByText(/📤 发送到 LLM（1\.2k tokens）/)).toBeTruthy()
    expect(screen.getByText(/📥 LLM 返回（340 tokens）/)).toBeTruthy()

    // 收起
    fireEvent.click(rowHead())
    await waitFor(() => expect(screen.queryByText('RESP-MARKER')).toBeNull())
  })

  it('会话持久化：回填的最后一条通信标注「上次会话」并给出快照时间', async () => {
    render(<LlmMonitorPanel />)
    await waitFor(() => expect(rowHead()).toBeTruthy())

    // 行内「上次会话」标记（restored 记录）
    expect(screen.getByText('上次会话')).toBeTruthy()
    // 工具条汇总提示
    expect(screen.getByText(/含 1 条上次会话遗留记录/)).toBeTruthy()
    // 说明文案：服务关闭时会自动保存会话最后一条通信（含快照时间）
    expect(screen.getByText(/会话最后一条通信/)).toBeTruthy()
    expect(screen.getByText(/2026-09-19 23:05:00/)).toBeTruthy()
  })

  it('统计条汇总发送/接收 Tokens', async () => {
    render(<LlmMonitorPanel />)
    expect(await screen.findByText('发送 Tokens')).toBeTruthy()
    expect(screen.getByText('接收 Tokens')).toBeTruthy()
    expect(screen.getByText('总调用')).toBeTruthy()
  })
})
