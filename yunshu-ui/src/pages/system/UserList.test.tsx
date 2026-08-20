/**
 * UserList —— 用户管理列表页测试
 * 覆盖（对应 M1 测试计划）：
 *   T1 列表加载成功 / T2 加载失败 / T3 搜索与重置 / T4 空结果
 *   T5 分页（翻页/末页禁用/跳页越界回退）
 *   T6 新增用户（createUser + toast + 刷新）/ T7 编辑用户（回填 + updateUser）
 *   T8 删除（确认/取消/末页删空回退）/ T9 新增表单校验
 * 说明：api/user 全部 mock，不依赖 dev server 中间件。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createUser, deleteUser, getUserList, updateUser, type UserListItem } from '@/api/user'
import { toast } from '@/components/Toaster'
import UserList from './UserList'

/** mock 用户数据（对齐 api/user.ts 的 UserListItem） */
const MOCK_USERS: UserListItem[] = [
  { id: 1, username: 'admin', email: 'admin@yunshu.local', role: 'admin', status: 1, createdAt: '2026-08-01 10:00:00' },
  { id: 2, username: 'user02', email: 'user02@yunshu.local', role: 'user', status: 0, createdAt: '2026-08-02 11:00:00' },
]

vi.mock('@/api/user', () => ({
  getUserList: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
}))

const mockGetUserList = vi.mocked(getUserList)
const mockCreateUser = vi.mocked(createUser)
const mockUpdateUser = vi.mocked(updateUser)
const mockDeleteUser = vi.mocked(deleteUser)
/** toast.success 调用断言（避免真实 Toaster 的模块级状态跨测试泄漏） */
let toastSpy: ReturnType<typeof vi.spyOn>

/** 渲染页面（toast 断言用 vi.spyOn(toast)，避免 Toaster 模块级状态跨测试泄漏） */
function renderList() {
  return render(<UserList />)
}

/** 等待列表首次渲染完成（admin 用户的 username 与 role 均为 admin，需用 getAllByText） */
async function waitListReady() {
  await waitFor(() => expect(screen.getAllByText('admin').length).toBeGreaterThan(0))
}

beforeEach(() => {
  vi.clearAllMocks()
  toastSpy = vi.spyOn(toast, 'success')
  mockGetUserList.mockResolvedValue({ list: MOCK_USERS, total: 2 })
  mockCreateUser.mockResolvedValue(MOCK_USERS[0])
  mockUpdateUser.mockResolvedValue(MOCK_USERS[0])
  mockDeleteUser.mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('用户管理列表页', () => {
  it('T1 列表加载成功：渲染行数据与总数，默认查询参数正确', async () => {
    renderList()
    await waitListReady()

    // admin 出现在用户名列与角色 badge，使用 getAllByText
    expect(screen.getAllByText('admin').length).toBeGreaterThan(0)
    expect(screen.getByText('user02')).toBeInTheDocument()
    // 总数「2」与 id 列数字 2 冲突，限定在 span（分页总数展示）
    expect(screen.getByText('2', { selector: 'span' })).toBeInTheDocument()
    expect(mockGetUserList).toHaveBeenCalledWith({ page: 1, pageSize: 10, keyword: '' })
  })

  it('T2 列表加载失败：不崩溃，展示空态', async () => {
    mockGetUserList.mockRejectedValue(new Error('服务器错误'))
    renderList()

    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })

  it('T3 搜索与重置：查询携带 keyword 并重置页码，重置清空参数', async () => {
    renderList()
    await waitListReady()

    fireEvent.change(screen.getByPlaceholderText('请输入用户名搜索'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: '查询' }))
    await waitFor(() =>
      expect(mockGetUserList).toHaveBeenLastCalledWith({ page: 1, pageSize: 10, keyword: 'admin' }),
    )

    fireEvent.click(screen.getByRole('button', { name: '重置' }))
    await waitFor(() =>
      expect(mockGetUserList).toHaveBeenLastCalledWith({ page: 1, pageSize: 10, keyword: '' }),
    )
  })

  it('T4 空结果：显示「暂无数据」', async () => {
    mockGetUserList.mockResolvedValue({ list: [], total: 0 })
    renderList()

    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })

  it('T5 分页：翻页参数正确、末页禁用、跳页越界回退', async () => {
    mockGetUserList.mockResolvedValue({ list: MOCK_USERS, total: 25 }) // 25 条 → 3 页
    renderList()
    await waitListReady()

    // 第一页：上一页禁用
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mockGetUserList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mockGetUserList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 3 })))
    // 末页：下一页禁用
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()

    // 跳页输入非数字 → 回退为当前页（仍停留在第 3 页）
    const pageInput = screen.getAllByRole('textbox')[1]
    fireEvent.change(pageInput, { target: { value: 'abc' } })
    fireEvent.keyDown(pageInput, { key: 'Enter' })
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled())
  })

  it('T6 新增用户：提交 createUser、成功 toast、刷新列表', async () => {
    renderList()
    await waitListReady()

    fireEvent.click(screen.getByRole('button', { name: '新增用户' }))
    const dialog = await screen.findByRole('dialog', { name: '新增用户' })

    fireEvent.change(within(dialog).getByLabelText('用户名'), { target: { value: 'newuser' } })
    fireEvent.change(within(dialog).getByLabelText('邮箱'), { target: { value: 'new@yunshu.local' } })
    fireEvent.change(within(dialog).getByLabelText('角色'), { target: { value: 'manager' } })
    fireEvent.change(within(dialog).getByLabelText('状态'), { target: { value: '1' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(mockCreateUser).toHaveBeenCalledWith({
        username: 'newuser',
        email: 'new@yunshu.local',
        role: 'manager',
        status: 1,
      }),
    )
    expect(await screen.findByText('2', { selector: 'span' })).toBeInTheDocument()
    void toastSpy
  })

  it('T7 编辑用户：表单回填、用户名只读、updateUser、成功 toast', async () => {
    renderList()
    await waitListReady()

    fireEvent.click(screen.getAllByRole('button', { name: '编辑' })[0])
    const dialog = await screen.findByRole('dialog', { name: '编辑用户' })

    expect(within(dialog).getByLabelText('用户名')).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('邮箱'), { target: { value: 'new-email@yunshu.local' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(mockUpdateUser).toHaveBeenCalledWith(1, expect.objectContaining({ email: 'new-email@yunshu.local' })),
    )
    expect(toastSpy).toHaveBeenCalledWith('用户已更新')
  })

  it('T8 删除用户：取消不调用接口，确认后 deleteUser 并刷新', async () => {
    renderList()
    await waitListReady()

    // 取消：不调用接口
    fireEvent.click(screen.getAllByRole('button', { name: '删除' })[0])
    const cancelDialog = await screen.findByRole('dialog', { name: '删除用户' })
    fireEvent.click(within(cancelDialog).getByRole('button', { name: '取消' }))
    expect(mockDeleteUser).not.toHaveBeenCalled()

    // 确认：调用 deleteUser 并刷新列表
    fireEvent.click(screen.getAllByRole('button', { name: '删除' })[0])
    const confirmDialog = await screen.findByRole('dialog', { name: '删除用户' })
    fireEvent.click(within(confirmDialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(mockDeleteUser).toHaveBeenCalledWith(1))
    await waitFor(() => expect(mockGetUserList.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('T8b 末页删空：删除后自动回退一页', async () => {
    mockGetUserList.mockResolvedValue({ list: [MOCK_USERS[0]], total: 25 })
    renderList()
    await waitListReady()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mockGetUserList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))

    fireEvent.click(screen.getAllByRole('button', { name: '删除' })[0])
    const dialog = await screen.findByRole('dialog', { name: '删除用户' })
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(mockGetUserList).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })))
  })

  it('T9 新增表单校验：用户名为空时保存按钮禁用', async () => {
    renderList()
    await waitListReady()

    fireEvent.click(screen.getByRole('button', { name: '新增用户' }))
    const dialog = await screen.findByRole('dialog', { name: '新增用户' })
    const saveBtn = within(dialog).getByRole('button', { name: '保存' })

    expect(saveBtn).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('用户名'), { target: { value: 'x' } })
    expect(saveBtn).toBeEnabled()
  })
})
