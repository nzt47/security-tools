/**
 * Select / FormField 单元测试
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Select from './Select'
import FormField from './FormField'

afterEach(cleanup)

const options = [
  { label: '管理员', value: 'admin' },
  { label: '普通用户', value: 'user' },
]

describe('Select', () => {
  it('渲染 label / placeholder / 选项', () => {
    render(<Select label="角色" options={options} value="" onChange={vi.fn()} />)
    expect(screen.getByLabelText('角色')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '请选择' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '管理员' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '普通用户' })).toBeInTheDocument()
  })

  it('选择触发 onChange', () => {
    const onChange = vi.fn()
    render(<Select options={options} value="" onChange={onChange} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'admin' } })
    expect(onChange).toHaveBeenCalledWith('admin')
  })

  // 【2026-11-17】必选项下拉：没有"空值"这个合法状态 ⇒ 不能多渲染占位项
  it('hidePlaceholder：不渲染「请选择」占位项（必选项）', () => {
    render(<Select options={options} value="admin" onChange={vi.fn()} hidePlaceholder aria-label="角色" />)
    expect(screen.queryByRole('option', { name: '请选择' })).toBeNull()
    expect(screen.getAllByRole('option')).toHaveLength(2)
    // 默认仍然渲染占位项（有"全部/未选择"语义的筛选下拉要它）
    cleanup()
    render(<Select options={options} value="" onChange={vi.fn()} />)
    expect(screen.getByRole('option', { name: '请选择' })).toBeInTheDocument()
  })

  it('error 文案展示', () => {
    render(<Select options={options} value="" onChange={vi.fn()} error="请选择角色" />)
    expect(screen.getByText('请选择角色')).toBeInTheDocument()
  })
})

describe('FormField', () => {
  it('渲染 label 与必填标记', () => {
    render(
      <FormField label="用户名" required>
        <input />
      </FormField>,
    )
    expect(screen.getByText('用户名')).toBeInTheDocument()
    expect(screen.getByText('*')).toBeInTheDocument()
  })

  it('渲染 error 提示', () => {
    render(
      <FormField label="用户名" error="用户名不能为空">
        <input />
      </FormField>,
    )
    expect(screen.getByText('用户名不能为空')).toBeInTheDocument()
  })
})
