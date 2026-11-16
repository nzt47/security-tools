/**
 * ui/Input 单元测试
 *
 * 【为什么单立这一份】2026-11-17 给 Input 补了 leading/trailing 附加槽 —— 会话栏搜索框
 * 之所以一直留原生 <input>，就是因为原语没有槽位、图标会压住正文。槽位是"正文让位"
 * 的契约（有槽就有对应内边距），所以这里直接量 class，而不是只断言"渲染出来了"。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Input from './Input'

afterEach(cleanup)

describe('ui/Input', () => {
  it('label 关联 input（htmlFor/id 自动生成）', () => {
    render(<Input label="用户名" />)
    expect(screen.getByLabelText('用户名')).toBeInTheDocument()
  })

  it('error：aria-invalid + 红色描边 + 文案', () => {
    render(<Input label="邮箱" error="邮箱格式不对" />)
    const el = screen.getByLabelText('邮箱')
    expect(el).toHaveAttribute('aria-invalid', 'true')
    expect(el.className).toContain('border-danger')
    expect(screen.getByText('邮箱格式不对')).toBeInTheDocument()
  })

  it('loading：禁用输入 + 显示 spinner', () => {
    const { container } = render(<Input label="校验中" loading />)
    expect(screen.getByLabelText('校验中')).toBeDisabled()
    expect(container.querySelector('[data-input-trailing] svg')).not.toBeNull()
  })

  it('leading 槽：图标进槽位、正文左内边距让位（不压住文字）', () => {
    const { container } = render(<Input label="搜索" leading={<span data-testid="ico">🔍</span>} />)
    const input = screen.getByLabelText('搜索')
    // 图标在槽里，不在 input 里
    expect(container.querySelector('[data-input-leading]')?.contains(screen.getByTestId('ico'))).toBe(true)
    // 让位：md 档 pl-9；反向证据是**不能**只有 px-3
    expect(input.className).toContain('pl-9')
    // 装饰性槽位不吃指针事件
    expect(container.querySelector('[data-input-leading]')?.className).toContain('pointer-events-none')
  })

  it('trailing 槽：保留指针事件（清空按钮要能点）+ 右侧让位', () => {
    const onClear = vi.fn()
    const { container } = render(
      <Input
        label="搜索"
        size="xs"
        trailing={
          <button type="button" onClick={onClear} aria-label="清空搜索">
            ×
          </button>
        }
      />,
    )
    const input = screen.getByLabelText('搜索')
    expect(input.className).toContain('pr-6')
    const trailing = container.querySelector('[data-input-trailing]')
    expect(trailing?.className).not.toContain('pointer-events-none')
    fireEvent.click(screen.getByLabelText('清空搜索'))
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('无槽时不给让位内边距（不留白）', () => {
    render(<Input label="名称" />)
    const input = screen.getByLabelText('名称')
    expect(input.className).not.toContain('pl-9')
    expect(input.className).not.toContain('pr-9')
  })

  it('inputClassName 只作用在内层 input，className 作用在外层容器', () => {
    const { container } = render(<Input label="宽度" className="w-40" inputClassName="font-mono" />)
    expect(screen.getByLabelText('宽度').className).toContain('font-mono')
    // 容器基类带 w-full，className 的 w-40 会被 cn()/twMerge 判为同一组的冲突并**替换**它
    // （这也是"外层宽度由调用方定"的口径：传 w-40 就是 w-40，不会残留 w-full）
    expect(container.firstElementChild?.className).toContain('w-40')
    expect(container.firstElementChild?.className).not.toContain('w-full')
  })
})
