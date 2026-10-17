/**
 * 健康体检 / 诊断+可观测 —— 19 条端点
 * ------------------------------------------------------------------
 * 锁：只读面打开即拉；快速体检是 GET 且不重复请求其它面；
 * 权重 PUT 需勾确认且坏 JSON 拦在本地；告警规则 PUT/DELETE 都带规则名。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPut: vi.fn(), hubDelete: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPut: api.hubPut,
  hubDelete: api.hubDelete,
}))

import HealthVitals from './health-vitals'
import OpsDiagnostics from './ops-diagnostics'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPut.mockReset()
  api.hubDelete.mockReset()
})

function healthGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/health/score') {
      return { overall_score: 91.5, level: 'good', dimensions: { availability: { score: 96.25, weight: 0.2, indicators: { uptime: 100 } } } }
    }
    if (url === '/api/health/summary') return { overall_score: 91.5, level: 'good' }
    if (url.startsWith('/api/health/trend')) return { data_points: 1, trend: { trend: 'stable', change: 0 } }
    if (url.startsWith('/api/health/history')) return { history: [{ overall_score: 90 }] }
    if (url === '/api/health/alerts') return { alerts: [{ name: 'YunshuHealthScoreCritical', condition: 'health.overall_score < 30', severity: 'critical' }] }
    if (url === '/api/health/weights') return { dimensions: ['stability', 'quality'], weights: { stability: 0.2, quality: 0.2 } }
    if (url === '/api/health/quick-check') return { ok: true, level: 'good', score: 85, elapsed_ms: 103, metrics: { cpu_percent: 8 }, issues: [] }
    return { ok: true }
  })
}

describe('HealthVitals', () => {
  it('打开即拉 6 个只读面并渲染维度表', async () => {
    healthGets()
    render(<HealthVitals />)
    for (const u of ['/api/health/score', '/api/health/summary', '/api/health/alerts', '/api/health/weights']) {
      await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith(u))
    }
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/health/trend?days=7'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/health/history?limit=5'))
    expect(await screen.findByText('availability')).toBeTruthy()
    expect(screen.getByText(/YunshuHealthScoreCritical/)).toBeTruthy()
  })

  it('快速体检走 GET quick-check 并显示耗时', async () => {
    healthGets()
    render(<HealthVitals />)
    await screen.findByText('availability')
    fireEvent.click(screen.getByRole('button', { name: '快速体检' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/health/quick-check'))
    // 「103ms」会同时出现在完成提示与快速体检卡片里 —— 按"至少一处"断言
    await waitFor(() => expect(screen.getAllByText(/103ms/).length).toBeGreaterThan(0))
  })

  it('权重：未确认时禁用；坏 JSON 拦在本地；确认后 PUT', async () => {
    healthGets()
    api.hubPut.mockResolvedValue({ ok: true })
    render(<HealthVitals />)
    await screen.findByText('availability')

    const save = screen.getByRole('button', { name: '保存权重' }) as HTMLButtonElement
    expect(save.disabled, '改权重会影响所有人看到的评分，必须显式确认').toBe(true)

    fireEvent.click(screen.getByLabelText('确认修改权重'))
    fireEvent.change(screen.getByLabelText('健康维度权重 JSON'), { target: { value: '{oops' } })
    fireEvent.click(save)
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPut).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('健康维度权重 JSON'), { target: { value: '{"quality":0.3}' } })
    fireEvent.click(save)
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith('/api/health/weights', { weights: { quality: 0.3 } }))
  })
})

function opsGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/diagnostics/config') return { error: "Config.get() missing 1 required positional argument: 'self'" }
    if (url === '/api/diagnostics/health') return { dimensions: { error_rate: 1 } }
    if (url.startsWith('/api/diagnostics/logs')) return { logs: [{ msg: 'line-1' }] }
    if (url === '/api/diagnostics/error_correlation') return { ok: true, correlation: {} }
    if (url === '/api/diagnostics/tools') return { tools: [{ name: 'distill', enabled: true }], categories: { general: 1 } }
    if (url === '/api/observability/access_stats') return { total_accesses: 8, error_rate: 0.125, avg_response_time_ms: 170, unique_endpoints: 8 }
    if (url.startsWith('/api/observability/access_logs')) return { access_logs: [{ datetime: '2026-07-12T02:13:15', method: 'GET', endpoint: '/api/health', status_code: 200, response_time_ms: 12 }] }
    if (url.startsWith('/api/observability/traces')) return { traces: [] }
    if (url === '/api/observability/state') return { ok: true }
    return { ok: true }
  })
}

describe('OpsDiagnostics', () => {
  it('诊断页把后端错误如实上屏（不藏起来）', async () => {
    opsGets()
    render(<OpsDiagnostics />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/diagnostics/config'))
    expect(await screen.findByText(/Config.get\(\) missing/)).toBeTruthy()
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/diagnostics/tools'))
    expect(screen.getByText('distill')).toBeTruthy()
  })

  it('可观测页：访问统计与日志表', async () => {
    opsGets()
    render(<OpsDiagnostics />)
    fireEvent.click(screen.getByRole('button', { name: '可观测' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/observability/access_stats'))
    expect(await screen.findByText('12.5%')).toBeTruthy()
    expect(screen.getByText('/api/health')).toBeTruthy()
  })

  it('告警规则：保存走 PUT(name)，删除需确认走 DELETE(name)', async () => {
    opsGets()
    api.hubPut.mockResolvedValue({ ok: true })
    api.hubDelete.mockResolvedValue({ ok: true })
    render(<OpsDiagnostics />)
    fireEvent.click(screen.getByRole('button', { name: '可观测' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/observability/access_stats'))

    fireEvent.change(screen.getByLabelText('告警规则名'), { target: { value: 'YunshuHealthScoreCritical' } })
    fireEvent.change(screen.getByLabelText('告警规则 JSON'), { target: { value: '{"severity":"critical"}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存告警规则' }))
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith(
      '/api/observability/alerts/YunshuHealthScoreCritical', { severity: 'critical' },
    ))

    const del = screen.getByRole('button', { name: '删除告警规则' }) as HTMLButtonElement
    expect(del.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('确认删除告警规则'))
    fireEvent.click(del)
    await waitFor(() => expect(api.hubDelete).toHaveBeenCalledWith('/api/observability/alerts/YunshuHealthScoreCritical'))
  })
})
