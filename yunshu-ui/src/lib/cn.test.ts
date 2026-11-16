import { describe, expect, it } from 'vitest'
import { cn } from './cn'

/**
 * 字号阶梯 vs 文字颜色的冲突回归（2026-11-16）
 * ----------------------------------------------------------------
 * 自定义字号档位（text-micro / text-body / text-ui …）默认不被 tailwind-merge 认识，
 * 会被当成颜色组里的类 ⇒ 与 text-muted-foreground 这类颜色类"冲突"而被静默丢弃，
 * 结果是"字号档位写了等于没写"（Button 的 text-ui 被 text-primary-foreground 吃掉）。
 */
describe('cn：字号档位与文字颜色互不吞掉', () => {
  it.each([
    ['text-micro', 'text-muted-foreground'],
    ['text-caption', 'text-slate-400'],
    ['text-body', 'text-primary-foreground'],
    ['text-body-lg', 'text-foreground'],
    ['text-ui', 'text-danger'],
    ['text-title', 'text-primary'],
  ])('%s 与 %s 同时保留', (size, color) => {
    expect(cn(size, color)).toBe(`${size} ${color}`)
    // 顺序反过来也一样（颜色在前、字号在后）
    expect(cn(color, size)).toBe(`${color} ${size}`)
  })

  it('同一组内仍然只保留最后一个（字号之间 / 颜色之间）', () => {
    expect(cn('text-body', 'text-ui')).toBe('text-ui')
    expect(cn('text-muted-foreground', 'text-primary')).toBe('text-primary')
  })

  it('普通工具类照旧合并（padding / 尺寸）', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
    expect(cn('h-6', 'h-8')).toBe('h-8')
  })
})
