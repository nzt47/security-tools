/**
 * ui/Badge 单元测试：五种 tone 都来自语义 token、两档尺寸、可选状态点。
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import Badge from './Badge'

afterEach(cleanup)

describe('ui/Badge', () => {
  it('默认 neutral + sm', () => {
    render(<Badge>未知</Badge>)
    const el = screen.getByText('未知')
    expect(el.className).toContain('bg-muted')
    expect(el.className).toContain('text-micro')
  })

  it.each([
    ['primary', 'bg-primary/15'],
    ['success', 'bg-success/15'],
    ['warning', 'bg-warning/15'],
    ['danger', 'bg-danger/15'],
  ] as const)('tone=%s 用语义 token（%s）', (tone, klass) => {
    render(<Badge tone={tone}>x</Badge>)
    expect(screen.getByText('x').className).toContain(klass)
  })

  it('size=md 用 12px 档', () => {
    render(<Badge size="md">x</Badge>)
    expect(screen.getByText('x').className).toContain('text-body')
  })

  it('dot 渲染一个状态点（aria-hidden，不重复朗读）', () => {
    const { container } = render(<Badge tone="success" dot>x</Badge>)
    const dot = container.querySelector('[aria-hidden]')
    expect(dot).not.toBeNull()
    expect(dot?.className).toContain('bg-success')
  })
})
