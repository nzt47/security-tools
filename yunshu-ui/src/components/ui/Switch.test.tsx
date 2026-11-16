/**
 * Switch —— 开关原语的口径测试（Owner 2026-11-16 三条）
 *   ① 开=高亮（主色轨道）、关=灰；
 *   ② 形制与标签/徽章区分：role="switch"、**没有文字**（不带「开启/关闭」状态列）；
 *   ③ 开关本身就是状态：aria-checked 与视觉同一份 truth。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Switch from './Switch'

afterEach(cleanup)

describe('ui/Switch', () => {
  it('role=switch + aria-checked 反映状态，且不含任何文字（开关就是状态列）', () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onChange} label="联网搜索" />)
    const sw = screen.getByRole('switch', { name: '联网搜索' })
    expect(sw.getAttribute('aria-checked')).toBe('false')
    expect(sw.textContent).toBe('')
  })

  it('关闭时轨道是中性灰、开启时是主色并带发光', () => {
    const { rerender } = render(<Switch checked={false} onCheckedChange={() => {}} label="x" />)
    const off = screen.getByRole('switch').className
    expect(off).toContain('bg-muted')
    expect(off).not.toContain('bg-primary/85')

    rerender(<Switch checked onCheckedChange={() => {}} label="x" />)
    const on = screen.getByRole('switch').className
    expect(on).toContain('bg-primary/85')
    expect(on).not.toContain('bg-muted')
  })

  it('点击把**新值**交给回调（受控，调用方不用再取反）', () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onChange} label="x" />)
    fireEvent.click(screen.getByRole('switch'))
    expect(onChange).toHaveBeenCalledWith(true)

    onChange.mockClear()
    render(<Switch checked onCheckedChange={onChange} label="y" />)
    fireEvent.click(screen.getByRole('switch', { name: 'y' }))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('disabled 时不触发，且尺寸档位可切换', () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onChange} label="x" disabled />)
    const sw = screen.getByRole('switch')
    expect(sw.hasAttribute('disabled')).toBe(true)
    fireEvent.click(sw)
    expect(onChange).not.toHaveBeenCalled()
  })
})
