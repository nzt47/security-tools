/**
 * Notice 单测（2026-11-18 新增原语）
 * 锁三件事：① tone → 语义 token 类；② 危险态 role=alert、其余 role=status；
 * ③ icon=null 不要图标 / 不传 icon 用默认图标。
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Notice from './Notice'

describe('ui/Notice', () => {
  it('默认 info：role=status，颜色走 primary token', () => {
    const { container } = render(<Notice>提示</Notice>)
    const box = screen.getByRole('status')
    expect(box.className).toContain('border-primary/40')
    expect(box.textContent).toContain('提示')
    // 默认带图标
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('danger：role=alert + danger token', () => {
    render(<Notice tone="danger">出错了</Notice>)
    const box = screen.getByRole('alert')
    expect(box.className).toContain('border-danger/40')
    expect(box.className).toContain('text-danger')
  })

  it('success / warning 各走自己的 token', () => {
    const { unmount } = render(<Notice tone="success">成功</Notice>)
    expect(screen.getByRole('status').className).toContain('border-success/40')
    unmount()
    render(<Notice tone="warning">当心</Notice>)
    expect(screen.getByRole('status').className).toContain('border-warning/40')
  })

  it('icon={null} 不渲染图标；size=sm 用紧凑档', () => {
    const { container } = render(<Notice tone="info" icon={null} size="sm">无图</Notice>)
    expect(container.querySelector('svg')).toBeNull()
    expect(screen.getByRole('status').className).toContain('text-caption')
  })

  it('title 与正文分层，className 可覆盖（调用方定密度）', () => {
    const { container } = render(
      <Notice tone="danger" size="sm" className="px-4 py-3 text-ui" title="保存失败">
        原因见服务日志
      </Notice>,
    )
    const box = screen.getByRole('alert')
    expect(box.className).toContain('px-4')
    expect(box.className).toContain('text-ui')
    expect(box.className).not.toContain('text-caption')
    expect(container.textContent).toContain('保存失败')
    expect(container.textContent).toContain('原因见服务日志')
  })
})
