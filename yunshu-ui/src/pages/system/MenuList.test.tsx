/**
 * MenuList —— 菜单管理页测试（M3）
 * 覆盖：菜单树加载/失败/空态、新增顶级菜单、新增子菜单（parentId 传递）、编辑回填、
 *       删除（确认/取消）、表单校验。
 * 说明：api/menu 全部 mock，不依赖 dev server 中间件。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMenu, deleteMenu, getMenuTree, updateMenu, type MenuItem } from '@/api/menu'
import { toast } from '@/components/Toaster'
import MenuList from './MenuList'

/** mock 菜单树（含二级子菜单） */
const MOCK_TREE: MenuItem[] = [
  { id: 1, parentId: 0, title: '仪表盘', path: '/', icon: 'LayoutDashboard', authority: '', order: 1, hideInMenu: false, children: [] },
  {
    id: 3,
    parentId: 0,
    title: '系统管理',
    path: '/system',
    icon: 'Settings',
    authority: 'system:view',
    order: 10,
    hideInMenu: false,
    children: [
      { id: 4, parentId: 3, title: '用户列表', path: '/system/user', icon: 'Users', authority: 'system:user:view', order: 1, hideInMenu: false, children: [] },
    ],
  },
]

vi.mock('@/api/menu', () => ({
  getMenuTree: vi.fn(),
  createMenu: vi.fn(),
  updateMenu: vi.fn(),
  deleteMenu: vi.fn(),
}))

const mockGetMenuTree = vi.mocked(getMenuTree)
const mockCreateMenu = vi.mocked(createMenu)
const mockUpdateMenu = vi.mocked(updateMenu)
const mockDeleteMenu = vi.mocked(deleteMenu)
let toastSpy: ReturnType<typeof vi.spyOn>

function renderList() {
  return render(<MenuList />)
}

beforeEach(() => {
  vi.clearAllMocks()
  toastSpy = vi.spyOn(toast, 'success')
  mockGetMenuTree.mockResolvedValue(MOCK_TREE)
  mockCreateMenu.mockResolvedValue(MOCK_TREE[0])
  mockUpdateMenu.mockResolvedValue(MOCK_TREE[0])
  mockDeleteMenu.mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('菜单管理页', () => {
  it('T1 菜单树加载：渲染顶级与子菜单', async () => {
    renderList()

    expect(await screen.findByText('仪表盘')).toBeInTheDocument()
    expect(screen.getByText('系统管理')).toBeInTheDocument()
    expect(screen.getByText('用户列表')).toBeInTheDocument()
    expect(mockGetMenuTree).toHaveBeenCalledTimes(1)
  })

  it('T2 加载失败：不崩溃，展示空态', async () => {
    mockGetMenuTree.mockRejectedValue(new Error('服务器错误'))
    renderList()

    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })

  it('T3 新增顶级菜单：createMenu parentId=0 + toast + 刷新', async () => {
    renderList()
    await screen.findByText('仪表盘')

    fireEvent.click(screen.getByRole('button', { name: '新增菜单' }))
    const dialog = await screen.findByRole('dialog', { name: '新增菜单' })
    fireEvent.change(within(dialog).getByLabelText('菜单名'), { target: { value: '数据导出' } })
    fireEvent.change(within(dialog).getByLabelText('路由路径'), { target: { value: '/export' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(mockCreateMenu).toHaveBeenCalledWith(expect.objectContaining({ parentId: 0, title: '数据导出', path: '/export' })),
    )
    expect(toastSpy).toHaveBeenCalledWith('菜单已创建')
    await waitFor(() => expect(mockGetMenuTree.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('T4 新增子菜单：parentId 取父菜单 id', async () => {
    renderList()
    await screen.findByText('系统管理')

    // 在「系统管理」行点「子菜单」
    const sysRow = screen.getByText('系统管理').closest('tr')!
    fireEvent.click(within(sysRow).getByRole('button', { name: '子菜单' }))
    const dialog = await screen.findByRole('dialog', { name: '新增菜单' })
    expect(within(dialog).getByText(/作为「系统管理」的子菜单/)).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('菜单名'), { target: { value: '角色权限' } })
    fireEvent.change(within(dialog).getByLabelText('路由路径'), { target: { value: '/system/role' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(mockCreateMenu).toHaveBeenCalledWith(expect.objectContaining({ parentId: 3, title: '角色权限' })),
    )
  })

  it('T5 编辑菜单：回填 + updateMenu + toast', async () => {
    renderList()
    await screen.findByText('仪表盘')

    const row = screen.getByText('仪表盘').closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: '编辑' }))
    const dialog = await screen.findByRole('dialog', { name: '编辑菜单' })

    // 回填：原 title/path 已填充
    expect(within(dialog).getByLabelText('菜单名')).toHaveValue('仪表盘')
    fireEvent.change(within(dialog).getByLabelText('路由路径'), { target: { value: '/home' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(mockUpdateMenu).toHaveBeenCalledWith(1, expect.objectContaining({ path: '/home', title: '仪表盘' })),
    )
    expect(toastSpy).toHaveBeenCalledWith('菜单已更新')
  })

  it('T6 删除菜单：取消不调用，确认后 deleteMenu + toast', async () => {
    renderList()
    await screen.findByText('仪表盘')

    const row = screen.getByText('仪表盘').closest('tr')!
    fireEvent.click(within(row).getByRole('button', { name: '删除' }))
    const dialog = await screen.findByRole('dialog', { name: '删除菜单' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(mockDeleteMenu).not.toHaveBeenCalled()

    fireEvent.click(within(row).getByRole('button', { name: '删除' }))
    const dialog2 = await screen.findByRole('dialog', { name: '删除菜单' })
    fireEvent.click(within(dialog2).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(mockDeleteMenu).toHaveBeenCalledWith(1))
    expect(toastSpy).toHaveBeenCalledWith('菜单已删除')
  })

  it('T7 表单校验：菜单名或路径为空时保存禁用', async () => {
    renderList()
    await screen.findByText('仪表盘')

    fireEvent.click(screen.getByRole('button', { name: '新增菜单' }))
    const dialog = await screen.findByRole('dialog', { name: '新增菜单' })
    const saveBtn = within(dialog).getByRole('button', { name: '保存' })

    expect(saveBtn).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('菜单名'), { target: { value: 'x' } })
    expect(saveBtn).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('路由路径'), { target: { value: '/x' } })
    expect(saveBtn).toBeEnabled()
  })
})
