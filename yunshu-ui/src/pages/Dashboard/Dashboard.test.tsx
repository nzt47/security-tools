/**
 * Dashboard 单元测试 —— 数据加载链路（成功 / 失败 / 加载中 / 结构不符）
 * ------------------------------------------------
 * 【2026-10-15】随「系统管理·仪表盘打开是空白页」的修复同步更新：
 *   后端 /api/dashboard/summary 返回的是**扁平**指标，页面不再按 {stats,trend,roles} 取数；
 *   并补一条「结构不符也不空白」的回归 —— 这正是原空白页的根因（data.stats 为 undefined
 *   导致渲染期 TypeError）。用 importOriginal 保留真实 validateDashboardSummary。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { DashboardSummaryData } from '@/api/dashboard'

const { mockGetDashboardSummary } = vi.hoisted(() => ({ mockGetDashboardSummary: vi.fn() }))
vi.mock('@/api/dashboard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/dashboard')>()
  return { ...actual, getDashboardSummary: mockGetDashboardSummary }
})

import Dashboard from './index'

/** 与后端契约一致的正常返回（plugins/admin_api.py::admin_dashboard_summary 实测载荷） */
const normalData: DashboardSummaryData = {
  totalUsers: 12480,
  activeUsers: 8153,
  totalRoles: 4,
  totalNotifications: 96,
  unreadNotifications: 7,
  todayAuditCount: 12,
  sensorCount: 18,
  systemStatus: 'healthy',
}

describe('Dashboard 数据加载链路', () => {
  beforeEach(() => {
    mockGetDashboardSummary.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('加载成功：渲染真实指标卡片与系统状态', async () => {
    mockGetDashboardSummary.mockResolvedValue(normalData)
    render(<Dashboard />)

    await waitFor(() => expect(screen.getByText('12,480')).toBeInTheDocument())
    expect(screen.getByText('用户总数')).toBeInTheDocument()
    expect(screen.getByText('8,153')).toBeInTheDocument()
    expect(screen.getByText('活跃用户')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('角色数')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('今日审计')).toBeInTheDocument()
    expect(screen.getByText('18')).toBeInTheDocument()
    expect(screen.getByText('传感器')).toBeInTheDocument()
    expect(screen.getByText('96')).toBeInTheDocument()
    expect(screen.getByText('通知总数')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('未读通知')).toBeInTheDocument()
    expect(screen.getByText('healthy')).toBeInTheDocument()
  })

  it('请求失败：显示错误空态，且不渲染卡片', async () => {
    mockGetDashboardSummary.mockRejectedValue(new Error('mock network error'))
    render(<Dashboard />)

    await waitFor(() => expect(screen.getByText('数据加载失败，请稍后重试')).toBeInTheDocument())
    expect(screen.queryByText('用户总数')).not.toBeInTheDocument()
  })

  it('加载中：请求挂起时显示 spinner，resolve 后切换为数据视图', async () => {
    let resolveFn!: (v: DashboardSummaryData) => void
    mockGetDashboardSummary.mockReturnValue(
      new Promise((r) => {
        resolveFn = r
      }),
    )
    const { container } = render(<Dashboard />)

    expect(container.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByText('用户总数')).not.toBeInTheDocument()

    resolveFn(normalData)
    await waitFor(() => expect(screen.getByText('12,480')).toBeInTheDocument())
    expect(container.querySelector('.animate-spin')).toBeNull()
  })

  it('【回归】结构不符时明确报因，绝不空白（原「空白页」根因）', async () => {
    // 旧契约载荷（stats/trend/roles）—— 正是修复前会让页面抛 TypeError 的那一份
    mockGetDashboardSummary.mockResolvedValue({
      stats: { totalUsers: 1 },
      trend: [],
      roles: [],
    } as unknown as DashboardSummaryData)
    render(<Dashboard />)

    await waitFor(() => expect(screen.getByText(/接口返回结构与页面不匹配/)).toBeInTheDocument())
    expect(screen.getByText(/totalUsers 缺失或不是 number/)).toBeInTheDocument()
    // 关键：不空白 —— 有可见的说明文本
    expect(screen.getByText(/admin_dashboard_summary/)).toBeInTheDocument()
  })
})
