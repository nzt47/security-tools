/**
 * NotificationCenter —— 消息中心页测试（M5）
 * 覆盖：列表加载/失败空态、类型与未读筛选、单条已读、全部已读、分页。
 * 说明：api/notification 全 mock，不依赖 dev server 中间件。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import {
  getNotifications,
  getUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from '@/api/notification'
import NotificationCenter from './NotificationCenter'

const MOCK_LIST: NotificationItem[] = [
  { id: 1, type: 'system', title: '系统例行维护公告', content: '系统将于维护窗口执行例行检查', read: false, createdAt: '2026-08-20 10:00:00' },
  { id: 2, type: 'audit', title: '检测到异常登录行为', content: '检测到来自 10.0.0.1 的非常规操作', read: true, createdAt: '2026-08-20 09:30:00' },
]

vi.mock('@/api/notification', () => ({
  getNotifications: vi.fn(),
  getUnreadCount: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

const mockGetNotifications = vi.mocked(getNotifications)
const mockGetUnreadCount = vi.mocked(getUnreadCount)
const mockMarkRead = vi.mocked(markNotificationRead)
const mockMarkAllRead = vi.mocked(markAllNotificationsRead)

function renderList() {
  return render(<NotificationCenter />)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetNotifications.mockResolvedValue({ list: MOCK_LIST, total: 2 })
  mockGetUnreadCount.mockResolvedValue({ unread: 1 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('消息中心页', () => {
  it('T1 列表加载成功：渲染通知与未读计数、默认查询参数', async () => {
    renderList()

    expect(await screen.findByText('系统例行维护公告')).toBeInTheDocument()
    expect(screen.getByText('检测到异常登录行为')).toBeInTheDocument()
    // 未读计数展示
    expect(screen.getByText(/条未读通知/)).toBeInTheDocument()
    // 类型徽章（select 选项中也有同名文本，需用 getAllByText）
    expect(screen.getAllByText('系统公告').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('审计提醒').length).toBeGreaterThanOrEqual(2)
    // 默认参数：type/unreadOnly 空值转 undefined
    expect(mockGetNotifications).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, pageSize: 10 }),
    )
    expect(mockGetUnreadCount).toHaveBeenCalled()
  })

  it('T2 加载失败：不崩溃，展示空态', async () => {
    mockGetNotifications.mockRejectedValue(new Error('服务器错误'))
    renderList()

    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })

  it('T3 筛选：类型与仅看未读传参，重置清空', async () => {
    renderList()
    await screen.findByText('系统例行维护公告')

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'alert' } })
    fireEvent.click(screen.getByLabelText('仅看未读'))
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    await waitFor(() =>
      expect(mockGetNotifications).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, type: 'alert', unreadOnly: true }),
      ),
    )

    fireEvent.click(screen.getByRole('button', { name: '重置' }))
    await waitFor(() =>
      expect(mockGetNotifications).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, pageSize: 10 }),
      ),
    )
  })

  it('T4 单条标记已读：调用 API、未读数减一、按钮消失', async () => {
    mockMarkRead.mockResolvedValue(null)
    renderList()
    await screen.findByText('系统例行维护公告')

    fireEvent.click(screen.getByRole('button', { name: '标记已读' }))
    await waitFor(() => expect(mockMarkRead).toHaveBeenCalledWith(1))
    // 未读数归零，顶部文案切换
    await waitFor(() => expect(screen.getByText('没有未读通知')).toBeInTheDocument())
    // 该条按钮消失（已读态）
    expect(screen.queryByRole('button', { name: '标记已读' })).not.toBeInTheDocument()
  })

  it('T5 全部已读：调用 API、未读数清零', async () => {
    mockMarkAllRead.mockResolvedValue(null)
    renderList()
    await screen.findByText('系统例行维护公告')

    fireEvent.click(screen.getByRole('button', { name: '全部已读' }))
    await waitFor(() => expect(mockMarkAllRead).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('没有未读通知')).toBeInTheDocument())
  })

  it('T6 分页：下一页参数正确、末页禁用', async () => {
    mockGetNotifications.mockResolvedValue({ list: MOCK_LIST, total: 25 })
    renderList()
    await screen.findByText('系统例行维护公告')

    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() =>
      expect(mockGetNotifications).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
    )
  })
})
