/**
 * ui/Button 单元测试
 *
 * 守三件事（都是踩过坑的）：
 * 1. 变体语义落成**特征类**（primary=实底主色 / accent=主色描边 / success-outline=成功描边 /
 *    danger-outline=危险描边 / default=中性描边 / ghost=无底）—— 页面断言用同一套特征类；
 * 2. 固定高度下标签不折行（whitespace-nowrap；§11.135 实测「前往组装台」被挤到 61px 宽折两行）；
 * 3. loading 时禁用 + 出 spinner（防重复提交）。
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import Button from './Button'

afterEach(cleanup)

describe('ui/Button', () => {
  it('默认是 default 变体 + md 尺寸', () => {
    render(<Button>确定</Button>)
    const b = screen.getByRole('button', { name: '确定' })
    expect(b.className).toContain('border-border')
    expect(b.className).toContain('h-9')
  })

  it.each([
    ['primary', 'bg-primary'],
    ['accent', 'text-primary'],
    ['success-outline', 'text-success'],
    ['danger', 'bg-danger'],
    ['danger-outline', 'text-danger'],
    ['ghost', 'hover:bg-muted'],
  ] as const)('variant=%s 落成特征类 %s', (variant, klass) => {
    render(<Button variant={variant}>动作</Button>)
    expect(screen.getByRole('button', { name: '动作' }).className).toContain(klass)
  })

  it('标签不折行（固定高度下折行会溢出到相邻元素）', () => {
    render(<Button>前往组装台</Button>)
    expect(screen.getByRole('button', { name: '前往组装台' }).className).toContain('whitespace-nowrap')
  })

  it('loading：禁用 + 出 spinner；点击不触发 onClick', () => {
    const onClick = vi.fn()
    const { container } = render(<Button loading onClick={onClick}>提交</Button>)
    const b = screen.getByRole('button', { name: '提交' })
    expect(b).toBeDisabled()
    expect(container.querySelector('svg')).not.toBeNull()
    fireEvent.click(b)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('size=xs 是 24px 档（表格行内 / 工具条用）', () => {
    render(<Button size="xs">紧凑</Button>)
    expect(screen.getByRole('button', { name: '紧凑' }).className).toContain('h-6')
  })
})
