/**
 * DataScopeDialog —— 角色数据范围配置弹窗测试（M3）
 * 覆盖：打开时回显角色当前数据范围、选择后保存提交正确的 scope 值。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { RoleItem } from '@/api/role'
import DataScopeDialog from './DataScopeDialog'

const ADMIN_ROLE: RoleItem = {
  id: 1,
  name: 'admin',
  label: '管理员',
  permissions: [],
  dataScope: 'dept',
  createdAt: '2026-08-01 10:00:00',
}

function renderDialog(role: RoleItem | null = ADMIN_ROLE, onSubmit = vi.fn(), onCancel = vi.fn()) {
  return render(
    <DataScopeDialog open role={role} saving={false} onSubmit={onSubmit} onCancel={onCancel} />,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('数据范围配置弹窗', () => {
  it('T8a 打开时回显角色当前数据范围', () => {
    renderDialog()

    const dialog = screen.getByRole('dialog', { name: '数据范围：管理员' })
    expect(within(dialog).getByRole('radio', { name: /本部门数据/ })).toBeChecked()
    expect(within(dialog).getByRole('radio', { name: /全部数据/ })).not.toBeChecked()
  })

  it('T8b 选择其他范围并保存：提交对应 scope 值', () => {
    const onSubmit = vi.fn()
    renderDialog(ADMIN_ROLE, onSubmit)

    const dialog = screen.getByRole('dialog', { name: '数据范围：管理员' })
    fireEvent.click(within(dialog).getByRole('radio', { name: /仅本人数据/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(onSubmit).toHaveBeenCalledWith('self')
  })

  it('T8c 默认值：未配置 dataScope 时默认「仅本人数据」', () => {
    renderDialog({ ...ADMIN_ROLE, dataScope: undefined })

    expect(screen.getByRole('radio', { name: /仅本人数据/ })).toBeChecked()
  })
})
