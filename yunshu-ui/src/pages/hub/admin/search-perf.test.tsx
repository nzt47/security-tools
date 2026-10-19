/**
 * 搜索性能 —— 6 条端点
 * 锁：状态/摘要/历史三面并发；启停与手动检测各打对应 POST；运行态由 status 决定。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SearchPerformance from './search-perf'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets(running = false) {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/search-performance/status') return { ok: true, status: { running, interval: 300, check_count: 833, history_count: 100, last_check: null } }
    if (url === '/api/search-performance/summary') return { ok: true, summary: { last_status: 'warning', total_checks: 10, tavily_success_rate: 0, tavily_failed_count: 10 } }
    if (url.startsWith('/api/search-performance/history')) return { history: [{ check_id: 833, status: 'warning', timestamp: '2026-10-03T08:12:10', errors: ['Tavily 搜索失败'] }] }
    return {}
  })
}

describe('SearchPerformance', () => {
  it('三面并发拉取，未运行时只给「启动」', async () => {
    gets(false)
    render(<SearchPerformance />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/search-performance/status'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/search-performance/summary'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/search-performance/history?limit=20'))
    expect(await screen.findByText('未运行')).toBeTruthy()
    expect(screen.getByRole('button', { name: '启动性能监控' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '停止性能监控' })).toBeNull()
  })

  it('运行中只给「停止」（不让人重复启动）', async () => {
    gets(true)
    render(<SearchPerformance />)
    await waitFor(() => expect(screen.getByRole('button', { name: '停止性能监控' })).toBeTruthy())
    expect(screen.queryByRole('button', { name: '启动性能监控' })).toBeNull()
  })

  it('启动与立刻检测各打对应 POST', async () => {
    gets(false)
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SearchPerformance />)
    await screen.findByText('未运行')

    // 【2026-10-19 去 flake】初始取数期间按钮 disabled（disabled={loading}）：
    // CI 慢机上"渲染后立刻 click"会落在 disabled 上被**静默吞掉**，随后断言等不到 POST
    // （本用例在 #1211/#1216 各红一次，重跑即绿）。先等按钮可点，再点。
    const startBtn = screen.getByRole('button', { name: '启动性能监控' }) as HTMLButtonElement
    await waitFor(() => expect(startBtn.disabled).toBe(false))
    fireEvent.click(startBtn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/search-performance/start', {}))

    const checkBtn = screen.getByRole('button', { name: '立刻检测一次' }) as HTMLButtonElement
    await waitFor(() => expect(checkBtn.disabled).toBe(false))
    fireEvent.click(checkBtn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/search-performance/check', {}))
  })
})
