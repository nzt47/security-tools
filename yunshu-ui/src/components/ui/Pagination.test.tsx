/**
 * Pagination 单元测试
 * - 总条数 / 页码展示；上一页/下一页禁用边界；onChange 触发
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Pagination from './Pagination'

afterEach(cleanup)

function prevBtn(): HTMLButtonElement {
  return screen.getByRole('button', { name: '上一页' })
}

function nextBtn(): HTMLButtonElement {
  return screen.getByRole('button', { name: '下一页' })
}

describe('Pagination', () => {
  it('展示总条数与当前/总页码', () => {
    render(<Pagination page={2} pageSize={10} total={45} onChange={vi.fn()} />)
    expect(screen.getByText('共 45 条')).toBeInTheDocument()
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
  })

  it('第一页时上一页禁用，下一页可用', () => {
    render(<Pagination page={1} pageSize={10} total={45} onChange={vi.fn()} />)
    expect(prevBtn()).toBeDisabled()
    expect(nextBtn()).not.toBeDisabled()
  })

  it('最后一页时下一页禁用', () => {
    render(<Pagination page={5} pageSize={10} total={45} onChange={vi.fn()} />)
    expect(prevBtn()).not.toBeDisabled()
    expect(nextBtn()).toBeDisabled()
  })

  it('下一页 / 上一页触发 onChange 且传正确的页码', () => {
    const onChange = vi.fn()
    render(<Pagination page={2} pageSize={10} total={45} onChange={onChange} />)
    fireEvent.click(nextBtn())
    expect(onChange).toHaveBeenCalledWith(3)
    fireEvent.click(prevBtn())
    expect(onChange).toHaveBeenCalledWith(1)
  })
})
