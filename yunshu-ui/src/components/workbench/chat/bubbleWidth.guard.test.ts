/**
 * 守卫：对话气泡 / 代码块必须限宽（2026-11-21）
 * ------------------------------------------------------------------
 * 【为什么需要】消息列是 flex column + items-start ⇒ 气泡按**内容 max-content**
 * 收缩。一条长代码行（JSON / 日志 / 表格）就能把气泡撑到几千 px：真机实测
 * 气泡 5406px vs 列宽 456px，右边被面板裁掉、比思考/工具块宽，而且**不随面板
 * 变宽而重排**。修复靠 .wb-bubble / .wb-markdown / .wb-codeblock 的 max-width:100%
 * + min-width:0（代码块自己横向滚动）。
 *
 * 规则（读 src/styles/workbench.css）：
 *   1. .wb-bubble 必须 max-width: 100% 且 min-width: 0；
 *   2. .wb-markdown 必须 max-width: 100% 且 min-width: 0；
 *   3. .wb-codeblock 必须 max-width: 100% 且 min-width: 0。
 * 局限：只认字面声明；写成 var()/计算值不算命中（这条守卫要的就是"显式"）。
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

/** 取 selector { ... } 的第一个规则块（按花括号配对；找不到返回 null） */
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

/** 该块是否同时声明了 max-width:100% 与 min-width:0（返回缺失项列表） */
export function missingClamp(css: string, selectors: string[]): string[] {
  const missing: string[] = []
  for (const sel of selectors) {
    const body = blockOf(css, sel)
    if (body === null) {
      missing.push(sel + ' 缺少规则块')
      continue
    }
    if (!/max-width\s*:\s*100%/.test(body)) missing.push(sel + ' 缺 max-width: 100%')
    if (!/min-width\s*:\s*0/.test(body)) missing.push(sel + ' 缺 min-width: 0')
  }
  return missing
}

const SELECTORS = ['.wb-bubble', '.wb-markdown', '.wb-codeblock']

describe('对话气泡限宽守卫', () => {
  it('workbench.css 里气泡/正文/代码块都限宽（否则长代码行会把气泡撑破）', () => {
    const cssPath = join(findSrc(), 'styles', 'workbench.css')
    const css = readFileSync(cssPath, 'utf-8')
    const missing = missingClamp(css, SELECTORS)
    expect(missing, '\n' + missing.join('\n')).toEqual([])
  })

  it('合成反例：缺 max-width/min-width 会被判定为违规（守卫本身不能永远绿）', () => {
    const bad = '.wb-bubble {\n  padding: 10px 14px;\n}\n.wb-markdown { max-width: 100%; }\n'
    const missing = missingClamp(bad, SELECTORS)
    expect(missing.length).toBeGreaterThan(0)
    expect(missing.some((m) => m.includes('.wb-bubble') && m.includes('max-width'))).toBe(true)
    expect(missing.some((m) => m.includes('.wb-codeblock'))).toBe(true)
  })
})
