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
    // 类型徽章在每行渲染一次即可（2026-11-13 起下拉只保留"安全告警"，不再有同名选项）
    expect(screen.getByText('系统公告')).toBeInTheDocument()
    expect(screen.getByText('审计提醒')).toBeInTheDocument()
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

  it('T7 类型下拉：默认显示「全部类型」且**可再次选回全部**（占位项不禁用）', async () => {
    renderList()
    await screen.findByText('系统例行维护公告')
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('')
    const all = screen.getByRole('option', { name: '全部类型' }) as HTMLOptionElement
    expect(all.disabled).toBe(false) // 选过一次类型后必须能回到全部
    expect(screen.getByRole('option', { name: '安全告警' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: '系统公告' })).toBeNull()
    expect(screen.getByText(/类型目前只有/)).toBeInTheDocument()
  })

  it('T8 带筛选且无结果：空态说明当前筛选，而不是干巴巴「暂无数据」', async () => {
    mockGetNotifications.mockResolvedValue({ list: [], total: 0 })
    renderList()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'alert' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    expect(await screen.findByText(/没有匹配的消息/)).toBeInTheDocument()
    expect(screen.getByText(/换个条件或点「重置」/)).toBeInTheDocument()
  })

  it('T9 全部已读后当前列表行也变已读（红点/按钮不再残留）', async () => {
    mockMarkAllRead.mockResolvedValue(null)
    renderList()
    await screen.findByText('系统例行维护公告')
    expect(screen.getAllByRole('button', { name: '标记已读' }).length).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: '全部已读' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '标记已读' })).toBeNull())
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

describe('消息中心 · 告警状态语义（2026-11-13）', () => {
  it('resolved 显示「已自动恢复」，并区分「已确认」', async () => {
    const alarmRows: NotificationItem[] = [
      { id: 11, type: 'alert', severity: 'critical', title: 'L3 LLM 降级',
        content: 'error_rate=20%', read: false, createdAt: '2026-11-13 10:00:00',
        state: 'firing' },
      { id: 12, type: 'alert', severity: 'warning', title: '技能异常',
        content: 'unit.skills warn', read: true, createdAt: '2026-11-13 09:00:00',
        state: 'resolved', acked: true, acked_by: 'owner',
        acked_at: '2026-11-13T09:30:00+08:00' },
    ]
    mockGetNotifications.mockResolvedValue({ list: alarmRows, total: 2 })

    renderList()

    expect(await screen.findByText('已自动恢复')).toBeInTheDocument()
    expect(screen.getByText('已确认')).toBeInTheDocument()
    // 旧文案「已恢复」会被读成"人已处理"，不许再出现
    expect(screen.queryByText('已恢复')).toBeNull()
  })
})
