/**
 * CodeBlock 单测（2026-11-18 新增原语）
 * 锁：① 默认 maxH=224 / text-micro / token 颜色；② maxH 与 size 生效；
 * ③ className 透传（mt-2 / 覆盖底色）；④ 内容原样渲染（不转义、不裁剪）。
 */
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import CodeBlock from './CodeBlock'

describe('ui/CodeBlock', () => {
  it('默认：maxHeight 224、text-micro、token 描边/底色/字色', () => {
    const { container } = render(<CodeBlock>{'{"a":1}'}</CodeBlock>)
    const pre = container.querySelector('pre')!
    expect(pre.style.maxHeight).toBe('224px')
    expect(pre.className).toContain('text-micro')
    expect(pre.className).toContain('border-border')
    expect(pre.className).toContain('bg-background')
    expect(pre.className).toContain('text-muted-foreground')
    expect(pre.className).toContain('whitespace-pre-wrap')
    expect(pre.textContent).toBe('{"a":1}')
  })

  it('maxH 与 size=caption 生效（长文预览档）', () => {
    const { container } = render(<CodeBlock maxH={288} size="caption">{'x'}</CodeBlock>)
    const pre = container.querySelector('pre')!
    expect(pre.style.maxHeight).toBe('288px')
    expect(pre.className).toContain('text-caption')
    expect(pre.className).toContain('leading-relaxed')
  })

  it('className 透传：mt-2 / 覆盖字色都能落到 <pre> 上', () => {
    const { container } = render(<CodeBlock className="mt-2 text-amber-300">{'x'}</CodeBlock>)
    const pre = container.querySelector('pre')!
    expect(pre.className).toContain('mt-2')
    expect(pre.className).toContain('text-amber-300')
  })

  it('多行内容原样保留（不折叠空白）', () => {
    const { container } = render(<CodeBlock>{'line1\n  line2'}</CodeBlock>)
    expect(container.querySelector('pre')!.textContent).toBe('line1\n  line2')
  })
})
