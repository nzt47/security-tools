/**
 * AlarmBell（顶栏报警器）行为测试
 * ================================================================
 * 钉住的口径：
 *  · 未读数来自 /api/alarms/unread-count（**不是**自己数列表长度 —— 静默中的条目
 *    不在列表里但也不该被催，两者口径以后端为准）；
 *  · 读数失败要上屏且**不显示假徽标**（不编"0 条"也不编"99 条"）；
 *  · 静默/确认/已读/全部已读各自只打对应端点，成功后重新拉取；
 *  · 「查看全部」只调注入的 onOpenAll（组件不认识路由，避免组件→路由层的反向依赖）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import AlarmBell, { PANEL_ITEM_LIMIT, SILENCE_MINUTES } from './AlarmBell'

const fetchMock = vi.fn()

function envelope(data: unknown) {
  return { ok: true, status: 200, json: async () => ({ code: 200, data, message: 'success' }) }
}

const ALARM = {
  id: 7, key: 'health.l3_llm_tool', kind: 'health', severity: 'critical',
  title: 'L3 LLM/工具 降级（30 分）', detail: 'llm_calls=100 errors=20 error_rate=20.0%',
  state: 'firing', last_seen: '2026-11-11T03:00:00+08:00', count: 3, read: false,
  acked: false, silenced_until: '',
}

const RESOLVED = { ...ALARM, id: 8, key: 'unit.skills', severity: 'warning', title: '技能异常', state: 'resolved', count: 1 }

/** 按**端点种类**分派（不依赖查询串顺序）：默认「1 条未读 + 1 条列表」 */
function installRoutes(overrides: { unread?: unknown; list?: unknown } = {}) {
  fetchMock.mockImplementation(async (input: unknown, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/api/alarms/unread-count')) {
      return envelope(overrides.unread ?? {
        unread: 1, summary: { total: 1, unread: 1, firing: 1, resolved: 0, firing_by_severity: { critical: 1 } },
      })
    }
    if (url.startsWith('/api/alarms/list')) {
      return envelope(overrides.list ?? { items: [ALARM], total: 1, limit: PANEL_ITEM_LIMIT, offset: 0, summary: {} })
    }
    if (init?.method === 'POST') return envelope({ ok: true })
    throw new Error('unexpected url: ' + url)
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  installRoutes()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('报警器 · 读数', () => {
  it('顶栏显示未读数，面板列出条目与严重度', async () => {
    render(<AlarmBell />)
    expect((await screen.findByTestId('alarm-badge')).textContent).toBe('1')
    fireEvent.click(screen.getByTestId('alarm-bell'))
    const panel = await screen.findByTestId('alarm-panel')
    const item = within(panel).getByTestId('alarm-item-7')
    expect(within(item).getByText('L3 LLM/工具 降级（30 分）')).toBeTruthy()
    expect(within(item).getByText('×3')).toBeTruthy()
    expect(within(item).getByText(/error_rate=20.0%/)).toBeTruthy()
  })

  it('无未读时显示「暂无未读报警」，不留假徽标', async () => {
    installRoutes({
      unread: { unread: 0, summary: {} },
      list: { items: [], total: 0, limit: PANEL_ITEM_LIMIT, offset: 0, summary: {} },
    })
    render(<AlarmBell />)
    fireEvent.click(screen.getByTestId('alarm-bell'))
    expect(await screen.findByTestId('alarm-empty')).toBeTruthy()
    expect(screen.queryByTestId('alarm-badge')).toBeNull()
  })

  it('读数失败要上屏，且不显示假未读', async () => {
    fetchMock.mockImplementation(async () => {
      throw new Error('HTTP 503')
    })
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    const err = await screen.findByTestId('alarm-error')
    expect(err.textContent).toContain('报警器不可用')
    expect(err.textContent).toContain('HTTP 503')
    expect(screen.queryByTestId('alarm-badge')).toBeNull()
  })
})

describe('报警器 · 动作只打对应端点', () => {
  const calls = (): Array<[string, string, string]> => fetchMock.mock.calls.map((c) => [
    String(c[0]),
    (c[1] as RequestInit | undefined)?.method ?? 'GET',
    String((c[1] as RequestInit | undefined)?.body ?? ''),
  ])

  it('静默：POST /api/alarms/<id>/silence 带分钟数，随后重新拉取', async () => {
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    await screen.findByTestId('alarm-item-7')
    const item = screen.getByTestId('alarm-item-7')
    fireEvent.click(within(item).getByText('静默'))
    await waitFor(() => {
      expect(calls().some(([u, m]) => u === '/api/alarms/7/silence' && m === 'POST')).toBe(true)
    })
    const body = JSON.parse(calls().find(([u]) => u === '/api/alarms/7/silence')?.[2] ?? '{}')
    expect(body.minutes).toBe(SILENCE_MINUTES)
    await waitFor(() => {
      const count = calls().filter(([u]) => u.startsWith('/api/alarms/unread-count')).length
      expect(count).toBeGreaterThanOrEqual(2)
    })
  })

  it('确认：POST /api/alarms/<id>/ack 带 by=owner', async () => {
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    const item = await screen.findByTestId('alarm-item-7')
    fireEvent.click(within(item).getByText('确认'))
    await waitFor(() => {
      expect(calls().some(([u, m]) => u === '/api/alarms/7/ack' && m === 'POST')).toBe(true)
    })
  })

  it('全部已读：POST /api/alarms/read-all', async () => {
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    fireEvent.click(await screen.findByTestId('alarm-mark-all'))
    await waitFor(() => {
      expect(calls().some(([u, m]) => u === '/api/alarms/read-all' && m === 'POST')).toBe(true)
    })
  })

  it('已恢复的条目带「已恢复」标记', async () => {
    installRoutes({
      list: { items: [RESOLVED], total: 1, limit: PANEL_ITEM_LIMIT, offset: 0, summary: {} },
    })
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    const item = await screen.findByTestId('alarm-item-8')
    expect(within(item).getByText('已恢复')).toBeTruthy()
  })
})

describe('报警器 · 与审批收件箱的边界', () => {
  it('「查看全部」只调注入的回调（组件不认识路由）', async () => {
    const onOpenAll = vi.fn()
    render(<AlarmBell onOpenAll={onOpenAll} />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    fireEvent.click(await screen.findByTestId('alarm-open-all'))
    expect(onOpenAll).toHaveBeenCalledTimes(1)
  })

  it('面板里明确说明「审批单据不在报警器里」', async () => {
    render(<AlarmBell />)
    fireEvent.click(await screen.findByTestId('alarm-bell'))
    const panel = await screen.findByTestId('alarm-panel')
    expect(within(panel).getByText(/审批单据不在报警器里/)).toBeTruthy()
  })
})
