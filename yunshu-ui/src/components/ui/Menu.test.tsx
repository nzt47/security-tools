/**
 * Menu 单测（2026-11-18 新增原语）
 * 锁：① 面板 portal 到 body（不在容器内）；② 外点关闭；③ Esc 关闭且挂捕获阶段
 * （内层 stopPropagation 也能关）；④ role=menu + aria-label；⑤ 触发器点击不误关（交给调用方 toggle）。
 */
import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { useMenu, MenuPanel } from './Menu'

afterEach(cleanup)

function Harness({ align = 'right' as const }) {
  const menu = useMenu()
  return (
    <div>
      <button ref={menu.anchorRef as never} onClick={menu.toggle}>打开</button>
      {menu.open && (
        <MenuPanel anchorRef={menu.anchorRef} onClose={menu.close} align={align} label="测试菜单">
          <button role="menuitem">项一</button>
        </MenuPanel>
      )}
    </div>
  )
}

describe('ui/Menu', () => {
  it('打开后 portal 到 body 且 role=menu', () => {
    const { container } = render(<Harness />)
    fireEvent.click(screen.getByText('打开'))
    const menu = screen.getByRole('menu', { name: '测试菜单' })
    expect(container.contains(menu)).toBe(false)
    expect(document.body.contains(menu)).toBe(true)
  })

  it('外点关闭', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('打开'))
    expect(screen.queryByRole('menu')).not.toBeNull()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('Esc 关闭；内层 stopPropagation 时也关（捕获阶段）', () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('打开'))
    const item = screen.getByRole('menuitem')
    item.addEventListener('keydown', (e) => e.stopPropagation())
    fireEvent.keyDown(item, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('点触发器本身不触发"外点关闭"（由调用方 toggle）', () => {
    const onClose = vi.fn()
    function T() {
      const menu = useMenu()
      return (
        <div>
          <button ref={menu.anchorRef as never}>触发器</button>
          {menu.open && <MenuPanel anchorRef={menu.anchorRef} onClose={onClose} label="m"><span>正文</span></MenuPanel>}
          <button onClick={() => menu.setOpen(true)}>force-open</button>
        </div>
      )
    }
    render(<T />)
    fireEvent.click(screen.getByText('force-open'))
    fireEvent.mouseDown(screen.getByText('触发器'))
    expect(onClose).not.toHaveBeenCalled()
  })
})
