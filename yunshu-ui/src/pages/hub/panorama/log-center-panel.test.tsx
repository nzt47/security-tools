/**
 * 日志中心面板 —— /logs 独立日志应用的 10 条接口
 * ------------------------------------------------------------------
 * 锁：五个 Tab 各自打对应端点；查询带参数；内省「手动触发」是 POST 且运行中禁用。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import LogCenterPanel from './log-center-panel'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function baseGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/logs/api/stats') {
      return { total_count: 18, error_rate: 0.05, p95_duration_ms: 12, p99_duration_ms: 30, by_level: { info: 17, error: 1 }, by_category: { system: 18 }, top_sources: [{ source: 'app_server', count: 18 }] }
    }
    if (url === '/logs/api/trends') return { hours: 24, performance_trend: [{ hour: '2026-10-18 01:00', avg_ms: 5 }], error_trend: [] }
    if (url === '/logs/api/errors') return { total: 1, results: [{ id: 1, created_at: '2026-10-18 01:02', level: 'error', operation: 'startup', message: 'boom' }] }
    if (url === '/logs/api/insights') return { results: [{ id: 10, type: 'pattern', summary: '发现模式: frequent_operation', confidence: 0.75 }] }
    if (url === '/logs/api/knowledge') return { results: [{ id: 5, domain: 'system_behavior', finding: '近 24 小时共 18 条操作日志' }] }
    if (url === '/logs/api/actions') return { open_count: 2, high_priority_count: 1, results: [{ id: 1, title: '关注错误率', priority: 'high' }] }
    if (url.startsWith('/logs/api/query')) return { results: [{ id: 33, created_at: '2026-10-18 01:00', level: 'info', category: 'system', operation: 'startup', source: 'app_server', duration_ms: 0, message: 'startup' }] }
    if (url === '/logs/api/introspection/status') return { running: false, thread_alive: true, total_cycles: 3, last_run: '2026-10-18 00:30' }
    return { ok: true }
  })
}

describe('LogCenterPanel', () => {
  it('默认 Tab 拉 stats + trends，并渲染关键指标', async () => {
    baseGets()
    render(<LogCenterPanel />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/stats'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/trends'))
    expect(await screen.findByText('18')).toBeTruthy()
    expect(screen.getByText(/5.00%/)).toBeTruthy()
  })

  it('错误 Tab 拉 errors', async () => {
    baseGets()
    render(<LogCenterPanel />)
    fireEvent.click(screen.getByRole('button', { name: /错误/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/errors'))
    expect(await screen.findByText('boom')).toBeTruthy()
  })

  it('洞察 Tab 同时拉 insights / knowledge / actions', async () => {
    baseGets()
    render(<LogCenterPanel />)
    fireEvent.click(screen.getByRole('button', { name: /洞察/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/insights'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/knowledge'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/actions'))
    expect(await screen.findByText(/frequent_operation/)).toBeTruthy()
    expect(screen.getByText(/近 24 小时共 18 条操作日志/)).toBeTruthy()
  })

  it('查询 Tab 带 limit/level/q 参数', async () => {
    baseGets()
    render(<LogCenterPanel />)
    fireEvent.click(screen.getByRole('button', { name: /操作查询/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/query?limit=20'))

    fireEvent.change(screen.getByLabelText('操作日志关键字'), { target: { value: 'startup' } })
    fireEvent.change(screen.getByLabelText('操作日志级别'), { target: { value: 'error' } })
    fireEvent.click(screen.getByRole('button', { name: '查询操作日志' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/query?limit=20&level=error&q=startup'))
  })

  it('内省 Tab：状态上屏；手动触发走 POST', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, message: '已排入队列' })
    render(<LogCenterPanel />)
    fireEvent.click(screen.getByRole('button', { name: /内省引擎/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/logs/api/introspection/status'))
    expect(await screen.findByText('2026-10-18 00:30')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '手动触发内省' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/logs/api/introspection/run', {}))
    expect(await screen.findByText(/已触发内省分析/)).toBeTruthy()
  })

  it('提供 /logs/dashboard 的外部入口', async () => {
    baseGets()
    render(<LogCenterPanel />)
    const link = screen.getByRole('link', { name: '打开日志仪表盘' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/logs/dashboard')
  })
})
