/**
 * PanelErrorBoundary 单测 —— 「渲染失败不再等于空白」
 * 2026-10-15：与「系统管理·仪表盘空白页」同批（根因是渲染期 TypeError 无边界兜底）。
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PanelErrorBoundary from './PanelErrorBoundary'

function Boom(): never {
  throw new Error('boom 渲染失败')
}

describe('PanelErrorBoundary', () => {
  it('子组件抛错时给出可读错误卡，而不是空白', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <PanelErrorBoundary resetKey="a">
        <Boom />
      </PanelErrorBoundary>,
    )
    expect(screen.getByText(/该面板渲染失败/)).toBeInTheDocument()
    expect(screen.getByText(/boom 渲染失败/)).toBeInTheDocument()
    spy.mockRestore()
  })

  it('正常子组件原样渲染', () => {
    render(
      <PanelErrorBoundary resetKey="a">
        <div>正常内容</div>
      </PanelErrorBoundary>,
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
  })

  it('切到别的 resetKey 时自动复位（不再停在错误态）', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <PanelErrorBoundary resetKey="a">
        <Boom />
      </PanelErrorBoundary>,
    )
    expect(screen.getByText(/该面板渲染失败/)).toBeInTheDocument()
    rerender(
      <PanelErrorBoundary resetKey="b">
        <div>切走了</div>
      </PanelErrorBoundary>,
    )
    expect(screen.getByText('切走了')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('点重试后重新渲染子树', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    let shouldThrow = true
    function Flaky() {
      if (shouldThrow) throw new Error('第一次失败')
      return <div>恢复了</div>
    }
    render(
      <PanelErrorBoundary resetKey="a">
        <Flaky />
      </PanelErrorBoundary>,
    )
    expect(screen.getByText(/第一次失败/)).toBeInTheDocument()
    shouldThrow = false
    fireEvent.click(screen.getByText('重试'))
    expect(screen.getByText('恢复了')).toBeInTheDocument()
    spy.mockRestore()
  })
})
