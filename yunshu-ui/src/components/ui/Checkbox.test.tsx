/**
 * ui/Checkbox 单元测试
 *
 * 守三件事：
 * 1. label 与 input 通过 id 关联（getByLabelText 可用）；
 * 2. **勾选色走 accent-color**（不手绘方块，避免又是第 N 种青）；
 * 3. 与 Switch 的语义分工：Checkbox 是「一次性确认」，不承担状态列（口径 Owner）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Checkbox from './Checkbox'

afterEach(cleanup)

describe('ui/Checkbox', () => {
  it('label 与 input 关联，点 label 能勾选', () => {
    const onChange = vi.fn()
    render(<Checkbox label="我确认覆盖现有摘要" onChange={onChange} />)
    const box = screen.getByLabelText('我确认覆盖现有摘要')
    expect(box).toHaveProperty('type', 'checkbox')
    fireEvent.click(box)
    expect(onChange).toHaveBeenCalled()
  })

  it('label 支持 JSX（确认框常带强调）', () => {
    render(<Checkbox label={<span>我确认<b>清空</b></span>} />)
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
    expect(screen.getByText('清空')).toBeInTheDocument()
  })

  it('勾选色来自主色（accent-primary），尺寸两档', () => {
    const { rerender } = render(<Checkbox label="x" />)
    expect(screen.getByRole('checkbox').className).toContain('accent-primary')
    expect(screen.getByRole('checkbox').className).toContain('h-4')
    rerender(<Checkbox label="x" size="sm" />)
    expect(screen.getByRole('checkbox').className).toContain('h-3.5')
  })

  it('disabled：元素禁用 + 外层标出不可点（jsdom 不模拟 disabled 的点击抑制）', () => {
    // 【为什么只断这两条】jsdom 的 fireEvent 不做 disabled 的点击抑制 —— 点了仍会派发事件、
    // 甚至会翻转 checked，所以「点了没反应」在 jsdom 里验不出来（真实浏览器里 disabled 不会触发 click）。
    // 这里守的是**可判定的两条**：控件本身 disabled、外层 label 有 cursor-not-allowed 的视觉契约。
    render(<Checkbox label="x" disabled />)
    const box = screen.getByRole('checkbox') as HTMLInputElement
    expect(box).toBeDisabled()
    expect(box.closest('label')?.className).toContain('cursor-not-allowed')
  })

  it('无 label 时只渲染裸 input（能进 grid 等自定义布局）', () => {
    const { container } = render(<Checkbox aria-label="裸框" />)
    expect(container.querySelector('label')).toBeNull()
    expect(screen.getByLabelText('裸框')).toBeInTheDocument()
  })
})
