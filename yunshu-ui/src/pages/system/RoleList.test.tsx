/**
 * RoleList —— 角色列表页测试（M2 核心 + M3 数据范围集成）
 * 覆盖：列表加载渲染、数据范围入口（点「数据」→ 弹窗回显 → 保存 updateRoleDataScope）。
 * 说明：api/role 与 api/menu 均 mock，不依赖 dev server 中间件。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { assignRolePermissions, createRole, deleteRole, getRoleList, updateRole, type RoleItem } from '@/api/role'
import { updateRoleDataScope } from '@/api/menu'
import { toast } from '@/components/Toaster'
import RoleList from './RoleList'

const MOCK_ROLES: RoleItem[] = [
  { id: 1, name: 'admin', label: '管理员', description: '系统超级管理员', permissions: ['dashboard:view'], dataScope: 'all', createdAt: '2026-08-01 10:00:00' },
  { id: 3, name: 'user', label: '普通用户', description: '仅基础功能', permissions: [], dataScope: 'self', createdAt: '2026-08-03 12:00:00' },
]

vi.mock('@/api/role', () => ({
  getRoleList: vi.fn(),
  createUser: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  deleteRole: vi.fn(),
  assignRolePermissions: vi.fn(),
}))

vi.mock('@/api/menu', () => ({
  getMenuTree: vi.fn(),
  getPermissionList: vi.fn(),
  updateRoleDataScope: vi.fn(),
}))

const mockGetRoleList = vi.mocked(getRoleList)
const mockCreateRole = vi.mocked(createRole)
const mockUpdateRole = vi.mocked(updateRole)
const mockDeleteRole = vi.mocked(deleteRole)
const mockAssign = vi.mocked(assignRolePermissions)
const mockDataScope = vi.mocked(updateRoleDataScope)
let toastSpy: ReturnType<typeof vi.spyOn>

function renderList() {
  return render(<RoleList />)
}

beforeEach(() => {
  vi.clearAllMocks()
  toastSpy = vi.spyOn(toast, 'success')
  mockGetRoleList.mockResolvedValue({ list: MOCK_ROLES, total: 2 })
  mockCreateRole.mockResolvedValue(MOCK_ROLES[0])
  mockUpdateRole.mockResolvedValue(MOCK_ROLES[0])
  mockDeleteRole.mockResolvedValue(null)
  mockAssign.mockResolvedValue(MOCK_ROLES[0])
  mockDataScope.mockResolvedValue(MOCK_ROLES[0])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('角色列表页', () => {
  it('列表加载成功：渲染角色名与权限数', async () => {
    renderList()

    expect(await screen.findByText('管理员')).toBeInTheDocument()
    expect(screen.getByText('普通用户')).toBeInTheDocument()
    expect(mockGetRoleList).toHaveBeenCalledWith({ page: 1, pageSize: 10, keyword: '' })
  })

  it('数据范围入口：打开弹窗回显 → 保存调用 updateRoleDataScope + toast', async () => {
    renderList()
    await screen.findByText('管理员')

    // 管理员行点「数据」
    const row = screen.getByText('管理员').closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: '数据' }))
    const dialog = await screen.findByRole('dialog', { name: '数据范围：管理员' })
    // 回显 dataScope='all'
    expect(within(dialog).getByRole('radio', { name: /全部数据/ })).toBeChecked()

    // 改为「本部门数据」并保存
    fireEvent.click(within(dialog).getByRole('radio', { name: /本部门数据/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockDataScope).toHaveBeenCalledWith(1, 'dept'))
    expect(toastSpy).toHaveBeenCalledWith('数据范围已更新')
    // 保存后刷新列表
    await waitFor(() => expect(mockGetRoleList.mock.calls.length).toBeGreaterThanOrEqual(2))
  })
})
