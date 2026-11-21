/**
 * 守卫：外观里的「气泡样式 / 字体大小」必须真的作用到消息上（2026-11-21）
 * ------------------------------------------------------------------
 * 【为什么需要】Owner 报「外观里改气泡样式和字体大小没效果」。实测根因两条：
 *   1. `.wb-markdown` 把字号写死为 var(--text-body-lg) ⇒ 改 --chat-fs 只影响预览
 *      （预览用内联 font-size），真实回复不变；
 *   2. 终端格式 `.wb-bubble` 把 border-radius 写死 4px ⇒ 终端下「气泡样式」三项全失效。
 * 规则（读 src/styles/workbench.css）：
 *   - .wb-markdown 的 font-size 必须引用 --chat-fs；
 *   - .wb-chat-format-terminal .wb-bubble 与 .wb-chat-format-compact .wb-bubble 的
 *     border-radius 必须引用 --chat-bubble-radius。
 * 反例：把两处写死回字面值 ⇒ 本守卫红（不做"永远是绿的装饰"）。
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

function findSrc(): string {
  let dir = process.cwd()
  for (let i = 0; i < 4; i += 1) {
    const candidate = join(dir, 'src')
    if (existsSync(join(candidate, 'components', 'ui'))) return candidate
    dir = join(dir, '..')
  }
  throw new Error('找不到 src 目录（守卫需要在 yunshu-ui 下运行）')
}

function blockOf(css: string, selector: string): string | null {
  const idx = css.indexOf(selector + ' {')
  if (idx < 0) return null
  const start = css.indexOf('{', idx)
  let depth = 0
  for (let i = start; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return css.slice(start + 1, i)
    }
  }
  return null
}

/** 返回缺失项：decl 必须命中 pattern */
export function missingDecl(css: string, checks: Array<[string, string, RegExp]>): string[] {
  const missing: string[] = []
  for (const [sel, decl, pattern] of checks) {
    const body = blockOf(css, sel)
    if (body === null) {
      missing.push(sel + ' 缺少规则块')
      continue
    }
    const line = body.split('\n').find((l) => l.trim().startsWith(decl + ':'))
    if (!line) missing.push(sel + ' 缺少 ' + decl)
    else if (!pattern.test(line)) missing.push(sel + ' 的 ' + decl + ' 未引用样式变量：' + line.trim())
  }
  return missing
}

const CHECKS: Array<[string, string, RegExp]> = [
  ['.wb-markdown', 'font-size', /--chat-fs/],
  ['.wb-chat-format-terminal .wb-bubble', 'border-radius', /--chat-bubble-radius/],
  ['.wb-chat-format-compact .wb-bubble', 'border-radius', /--chat-bubble-radius/],
]

describe('外观样式生效守卫', () => {
  it('字体大小与气泡样式都通过 CSS 变量作用到真实消息', () => {
    const css = readFileSync(join(findSrc(), 'styles', 'workbench.css'), 'utf-8')
    const missing = missingDecl(css, CHECKS)
    expect(missing, '\n' + missing.join('\n')).toEqual([])
  })

  it('合成反例：写死字号/圆角会被判违规（守卫不能永远绿）', () => {
    const bad = [
      '.wb-markdown { font-size: var(--text-body-lg); }',
      '.wb-chat-format-terminal .wb-bubble { border-radius: 4px; }',
      '.wb-chat-format-compact .wb-bubble { border-radius: 6px; }',
    ].join('\n')
    const missing = missingDecl(bad, CHECKS)
    expect(missing.length).toBe(CHECKS.length)
  })
})
