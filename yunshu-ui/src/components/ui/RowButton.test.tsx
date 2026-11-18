/**
 * ui/RowButton 单元测试：整行可点语义、选中态、左右槽、禁用。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import RowButton from './RowButton'

afterEach(cleanup)

describe('ui/RowButton', () => {
  it('整行可点，点击触发 onClick', () => {
    const onClick = vi.fn()
    render(<RowButton onClick={onClick}>会话 A</RowButton>)
    fireEvent.click(screen.getByRole('button', { name: '会话 A' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('选中态走主色淡底，并给 aria-current', () => {
    render(<RowButton selected>会话 A</RowButton>)
    const el = screen.getByRole('button')
    expect(el.className).toContain('bg-primary/15')
    expect(el).toHaveAttribute('aria-current', 'true')
  })

  it('leading / trailing 都渲染在外层，正文可截断', () => {
    const { container } = render(
      <RowButton leading={<i>L</i>} trailing={<i>T</i>}>
        标题
      </RowButton>,
    )
    expect(container.textContent).toContain('L')
    expect(container.textContent).toContain('T')
    // 正文容器可伸缩（min-w-0 flex-1），截断交给调用方
    expect(container.querySelector('.min-w-0.flex-1')).not.toBeNull()
  })

  it('禁用时不可点', () => {
    const onClick = vi.fn()
    render(<RowButton disabled onClick={onClick}>会话 A</RowButton>)
    expect(screen.getByRole('button')).toBeDisabled()
  })

  it('bare：不设 min-height（行内可点区域用）', () => {
    render(<RowButton bare>标题</RowButton>)
    const cls = screen.getByRole('button').className
    expect(cls).not.toContain('min-h')
    expect(cls).toContain('py-0.5')
  })

  it('两档行高', () => {
    const { rerender } = render(<RowButton>行</RowButton>)
    expect(screen.getByRole('button').className).toContain('min-h-10')
    rerender(<RowButton size="sm">行</RowButton>)
    expect(screen.getByRole('button').className).toContain('min-h-8')
  })
})
