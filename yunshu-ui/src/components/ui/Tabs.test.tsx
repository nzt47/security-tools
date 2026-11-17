/**
 * ui/Tabs 单元测试：三种视觉家族 + role=tab 语义 + 选中态走主色。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Tabs from './Tabs'

afterEach(cleanup)

const ITEMS = [
  { value: 'a', label: '甲', count: 3 },
  { value: 'b', label: '乙' },
  { value: 'c', label: '丙', disabled: true },
]

describe('ui/Tabs', () => {
  it('渲染 tablist + role=tab，选中项 aria-selected=true', () => {
    render(<Tabs items={ITEMS} value="a" onChange={vi.fn()} aria-label="示例" />)
    expect(screen.getByRole('tablist', { name: '示例' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(screen.getByRole('tab', { name: /甲/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /乙/ })).toHaveAttribute('aria-selected', 'false')
  })

  it('点击切换回调值；禁用项不触发', () => {
    const onChange = vi.fn()
    render(<Tabs items={ITEMS} value="a" onChange={onChange} />)
    fireEvent.click(screen.getByRole('tab', { name: /乙/ }))
    expect(onChange).toHaveBeenCalledWith('b')
    fireEvent.click(screen.getByRole('tab', { name: /丙/ }))
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('三种家族各给出不同外壳（pill 无边框底 / segmented 主色淡底 / folder 上圆角）', () => {
    const { rerender } = render(<Tabs items={ITEMS} value="a" onChange={vi.fn()} variant="segmented" />)
    expect(screen.getByRole('tab', { name: /甲/ }).className).toContain('bg-primary/15')
    rerender(<Tabs items={ITEMS} value="a" onChange={vi.fn()} variant="folder" />)
    expect(screen.getByRole('tablist').className).toContain('border-b')
    expect(screen.getByRole('tab', { name: /甲/ }).className).toContain('rounded-t-panel')
    rerender(<Tabs items={ITEMS} value="a" onChange={vi.fn()} variant="pill" />)
    expect(screen.getByRole('tab', { name: /甲/ }).className).toContain('border-primary/60')
  })

  it('计数徽标跟着选中态换色', () => {
    render(<Tabs items={ITEMS} value="a" onChange={vi.fn()} />)
    expect(screen.getByText('3').className).toContain('bg-primary/20')
  })
})
