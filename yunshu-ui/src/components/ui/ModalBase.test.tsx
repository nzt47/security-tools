/**
 * ModalBase 单元测试
 * - open=false 不渲染；open=true 渲染 dialog/标题/内容
 * - Esc / 遮罩点击触发 onClose；closeOnMask=false 时遮罩不关闭
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import ModalBase from './ModalBase'

afterEach(cleanup)

describe('ModalBase', () => {
  it('open=false 时不渲染任何内容', () => {
    render(
      <ModalBase open={false} onClose={vi.fn()}>
        不应出现
      </ModalBase>,
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByText('不应出现')).toBeNull()
  })

  it('open=true 渲染标题与内容', () => {
    render(
      <ModalBase open onClose={vi.fn()} title="弹窗标题">
        正文内容
      </ModalBase>,
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('弹窗标题')).toBeInTheDocument()
    expect(screen.getByText('正文内容')).toBeInTheDocument()
  })

  it('Esc 按键触发 onClose', () => {
    const onClose = vi.fn()
    render(
      <ModalBase open onClose={onClose}>
        x
      </ModalBase>,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('遮罩点击触发 onClose', () => {
    const onClose = vi.fn()
    render(
      <ModalBase open onClose={onClose}>
        x
      </ModalBase>,
    )
    // ModalBase 经 createPortal 渲染到 document.body，须从 body 定位遮罩
    const mask = document.body.querySelector('[aria-hidden="true"]')
    expect(mask).not.toBeNull()
    fireEvent.click(mask!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closeOnMask=false 时遮罩点击不触发 onClose', () => {
    const onClose = vi.fn()
    render(
      <ModalBase open onClose={onClose} closeOnMask={false}>
        x
      </ModalBase>,
    )
    const mask = document.body.querySelector('[aria-hidden="true"]')
    expect(mask).not.toBeNull()
    fireEvent.click(mask!)
    expect(onClose).not.toHaveBeenCalled()
  })
})
