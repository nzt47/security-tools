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
