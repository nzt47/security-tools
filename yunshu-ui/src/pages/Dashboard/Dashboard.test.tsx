/**
 * Dashboard 单元测试 —— 数据加载链路（成功 / 失败 / 加载中）
 * ------------------------------------------------
 * 通过 mock @/api/dashboard 的 getDashboardSummary 隔离真实网络：
 *   - 成功：渲染 4 张统计卡片（格式化数值）与图表标题
 *   - 失败（reject）：显示「数据加载失败，请稍后重试」空态
 *   - 加载中：请求挂起时显示 spinner，resolve 后切换为数据视图
 * jsdom 无 ResizeObserver（ChartContainer 的尺寸观测依赖它），测试前打桩。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { DashboardSummaryData } from '@/api/dashboard'

// ── mock 数据源：隔离真实网络请求 ──
const { mockGetDashboardSummary } = vi.hoisted(() => ({ mockGetDashboardSummary: vi.fn() }))
vi.mock('@/api/dashboard', () => ({
  getDashboardSummary: mockGetDashboardSummary,
  DASHBOARD_MOCK_ERROR_KEY: 'dashboard_mock_error',
}))

// ── jsdom 无 ResizeObserver，ChartContainer 的尺寸观测需要它 ──
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

import Dashboard from './index'

/** 与后端契约一致的正常返回数据 */
const normalData: DashboardSummaryData = {
  stats: { totalUsers: 12480, totalOrders: 3926, conversionRate: 3.42, activeUsers: 8153 },
  trend: [
    { day: '08-15', visits: 1860 },
    { day: '08-16', visits: 2130 },
    { day: '08-17', visits: 1980 },
    { day: '08-18', visits: 2650 },
    { day: '08-19', visits: 2420 },
    { day: '08-20', visits: 2890 },
    { day: '08-21', visits: 3120 },
  ],
  roles: [
    { name: '普通用户', value: 10640 },
    { name: '编辑', value: 1560 },
    { name: '管理员', value: 280 },
  ],
}

describe('Dashboard 数据加载链路', () => {
  beforeEach(() => {
    mockGetDashboardSummary.mockReset()
    vi.stubGlobal('ResizeObserver', MockResizeObserver)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('加载成功：渲染 4 张统计卡片（格式化数值）与图表标题', async () => {
    mockGetDashboardSummary.mockResolvedValue(normalData)
    render(<Dashboard />)

    // 数据就绪后卡片数值出现（千分位 / 百分比格式化）
    await waitFor(() => expect(screen.getByText('12,480')).toBeInTheDocument())
    expect(screen.getByText('总用户数')).toBeInTheDocument()
    expect(screen.getByText('3,926')).toBeInTheDocument()
    expect(screen.getByText('总订单数')).toBeInTheDocument()
    expect(screen.getByText('3.42%')).toBeInTheDocument()
    expect(screen.getByText('转化率')).toBeInTheDocument()
    expect(screen.getByText('8,153')).toBeInTheDocument()
    expect(screen.getByText('活跃用户')).toBeInTheDocument()
    // 图表标题
    expect(screen.getByText('访问趋势')).toBeInTheDocument()
    expect(screen.getByText('用户角色分布')).toBeInTheDocument()
  })

  it('请求失败：显示错误空态，且不渲染卡片与图表', async () => {
    mockGetDashboardSummary.mockRejectedValue(new Error('mock network error'))
    render(<Dashboard />)

    await waitFor(() => expect(screen.getByText('数据加载失败，请稍后重试')).toBeInTheDocument())
    expect(screen.queryByText('总用户数')).not.toBeInTheDocument()
    expect(screen.queryByText('访问趋势')).not.toBeInTheDocument()
  })

  it('加载中：请求挂起时显示 spinner，resolve 后切换为数据视图', async () => {
    let resolveFn!: (v: DashboardSummaryData) => void
    mockGetDashboardSummary.mockReturnValue(
      new Promise((r) => {
        resolveFn = r
      }),
    )
    const { container } = render(<Dashboard />)

    // 挂起阶段：spinner 可见，卡片未渲染
    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByText('总用户数')).not.toBeInTheDocument()

    // 数据到达后：spinner 消失，卡片出现
    resolveFn(normalData)
    await waitFor(() => expect(screen.getByText('12,480')).toBeInTheDocument())
    expect(container.querySelector('.animate-spin')).toBeNull()
  })
})
