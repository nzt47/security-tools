/**
 * ui/Radio 单元测试：label 关联、勾选色、尺寸档、受控 checked。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Radio from './Radio'

afterEach(cleanup)

describe('ui/Radio', () => {
  it('label 与 input 关联，点 label 触发 onChange', () => {
    const onChange = vi.fn()
    render(<Radio name="g" label="路径模式" onChange={onChange} />)
    const box = screen.getByLabelText('路径模式')
    expect(box).toHaveProperty('type', 'radio')
    fireEvent.click(box)
    expect(onChange).toHaveBeenCalled()
  })

  it('勾选色走主色（accent-primary），两档尺寸', () => {
    const { rerender } = render(<Radio name="g" aria-label="裸框" />)
    expect(screen.getByRole('radio').className).toContain('accent-primary')
    expect(screen.getByRole('radio').className).toContain('h-4')
    rerender(<Radio name="g" aria-label="裸框" size="sm" />)
    expect(screen.getByRole('radio').className).toContain('h-3.5')
  })

  it('受控 checked=true 反映到 DOM', () => {
    render(<Radio name="g" label="已选" checked readOnly />)
    expect(screen.getByRole('radio')).toBeChecked()
  })

  it('disabled 时元素禁用且外层标出不可点', () => {
    render(<Radio name="g" label="不可选" disabled />)
    const box = screen.getByRole('radio')
    expect(box).toBeDisabled()
    expect(box.closest('label')?.className).toContain('cursor-not-allowed')
  })
})
