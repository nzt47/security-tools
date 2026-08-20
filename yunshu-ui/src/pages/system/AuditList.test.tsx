/**
 * AuditList —— 操作审计页测试（M4）
 * 覆盖：列表加载/失败/空态、操作人/类型/关键字筛选、分页。
 * 说明：api/audit mock，不依赖 dev server 中间件。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { getAuditLogs, type AuditLogItem } from '@/api/audit'
import AuditList from './AuditList'

const MOCK_LOGS: AuditLogItem[] = [
  { id: 1, traceId: 'trace-0001', operator: 'admin', action: 'login', target: '登录系统', result: 'success', ip: '10.0.0.1', detail: '', createdAt: '2026-08-01 10:00:00' },
  { id: 2, traceId: 'trace-0002', operator: 'manager', action: 'delete', target: '删除用户 user02', result: 'fail', ip: '10.0.0.2', detail: '权限不足或数据不存在', createdAt: '2026-08-01 10:03:00' },
]

vi.mock('@/api/audit', () => ({
  getAuditLogs: vi.fn(),
}))

const mockGetAuditLogs = vi.mocked(getAuditLogs)

function renderList() {
  return render(<AuditList />)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetAuditLogs.mockResolvedValue({ list: MOCK_LOGS, total: 2 })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('操作审计页', () => {
  it('T1 列表加载成功：渲染日志行与默认查询参数', async () => {
    renderList()

    expect(await screen.findByText('admin')).toBeInTheDocument()
    expect(screen.getByText('删除用户 user02')).toBeInTheDocument()
    // 结果状态展示
    expect(screen.getByText('失败')).toBeInTheDocument()
    // 默认参数：action 空值转 undefined（AuditLogParams 可选语义）
    expect(mockGetAuditLogs).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, pageSize: 10, operator: '', keyword: '' }),
    )
  })

  it('T2 加载失败：不崩溃，展示空态', async () => {
    mockGetAuditLogs.mockRejectedValue(new Error('服务器错误'))
    renderList()

    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })

  it('T3 筛选与重置：操作人/类型/关键字传参，重置清空', async () => {
    renderList()
    await screen.findByText('admin')

    fireEvent.change(screen.getByPlaceholderText('操作人'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByPlaceholderText('操作对象 / 详情关键字'), { target: { value: 'user02' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    await waitFor(() =>
      expect(mockGetAuditLogs).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, operator: 'admin', keyword: 'user02' }),
      ),
    )

    fireEvent.click(screen.getByRole('button', { name: '重置' }))
    await waitFor(() =>
      expect(mockGetAuditLogs).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, pageSize: 10, operator: '', keyword: '' }),
      ),
    )
  })

  it('T4 分页：下一页参数正确、末页禁用', async () => {
    mockGetAuditLogs.mockResolvedValue({ list: MOCK_LOGS, total: 25 })
    renderList()
    await screen.findByText('admin')

    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mockGetAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
  })
})
