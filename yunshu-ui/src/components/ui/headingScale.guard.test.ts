/**
 * 守卫：标题字号必须是**显式 token**，不许由 CSS 类 / 父选择器决定（2026-11-19）
 *
 * 背景：全仓最后 12 处 heading 的字号写在 .pl-* / .kb-* CSS 里
 * （pl-title / pl-col-title / pl-modal-title / kb-detail-title + prompt-lab 预览 8 处），
 * 于是同一个 h2 在页面里可能是 20（kb-detail）/ 16（pl-modal）/ 14（preview）/
 * 13（pl-col）四种。CSS 字号在 JSX 里看不见，每次改阶梯都会漏掉它们。
 *
 * 现在这 12 处全部带 text-* token、CSS 里的 font-size 已删；本守卫防回退：
 * src 下所有 .tsx 里，每个 <h1..h6> 开标签内部必须出现 text-* 字号类。
 *
 * 局限：只认标签内的字面量。className 由变量拼出来的也要求出现 text-
 * （这是刻意的：变量拼字号正是"藏起来"的源头）。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC_ROOT = join(process.cwd(), 'src')

/** 返回缺少 text-* token 的标题开标签（导出以便用合成反例证明守卫会红） */
export function headingsWithoutTextToken(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/<h[1-6]\b[^>]*>/g)) {
    if (!m[0].includes('text-')) out.push(m[0].replace(/\s+/g, ' '))
  }
  return out
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out
}

describe('标题字号守卫', () => {
  it('每个 <h1..h6> 都必须显式声明 text-* 字号', () => {
    const offenders: string[] = []
    for (const file of walk(SRC_ROOT)) {
      if (!file.endsWith('.tsx') || file.includes('.test.')) continue
      const src = readFileSync(file, 'utf8')
      for (const tag of headingsWithoutTextToken(src)) {
        offenders.push(relative(SRC_ROOT, file).replace(/\\/g, '/') + ' :: ' + tag)
      }
    }
    expect(offenders, '这些标题的字号来自 CSS / 变量拼接（改阶梯时看不见）').toEqual([])
  })

  it('合成反例必须判红（证明守卫不是恒绿）', () => {
    expect(headingsWithoutTextToken('<h2 className="pl-col-title">x</h2>')).toHaveLength(1)
    expect(headingsWithoutTextToken('<h2 className="text-ui">x</h2>')).toHaveLength(0)
    // 多行开标签 / className 由表达式拼：仍要求出现 text-
    expect(headingsWithoutTextToken('<h3\n  className={cn(foo)}>x</h3>')).toHaveLength(1)
    expect(headingsWithoutTextToken('<h3\n  className={cn(\'text-body\', foo)}>x</h3>')).toHaveLength(0)
  })
})
