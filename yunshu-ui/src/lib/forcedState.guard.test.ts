/**
 * 守卫：不允许"用户改不回来"的强制状态
 * ------------------------------------------------------------------
 * 【为什么需要】2026-11-22 Owner 实报「开关中心选 A/B/C 后分类收不起来」。
 * 根因只有一行：`const expanded = forceOpen || open` —— 外部条件为真时，
 * 用户对标题的点击**被静默忽略**（内部 state 翻转了、界面不动）。
 * 同一形态在导航栏（搜索态）也有一份。修完后把它变成会失败的测试，而不是靠人记。
 *
 * 规则（扫描 src 下所有 .tsx / .ts，排除测试文件；**先剥注释**，注释里的反例不算违规）：
 *   1. `force*` 形式的强制 prop（forceOpen / forceExpand / forceCollapse / forceVisible…）；
 *   2. 恒真条件 `|| true`（等于把某个 UI 状态写死）；
 *   3. `open|expanded|checked|selected|active|pressed|collapsed = … || <同类状态|true>`
 *      —— 即"强制与用户状态取或"，用户改不回来。
 *
 * 局限：只做静态字面量扫描，动态拼出来的写法看不到（看到就应当拆成可控状态 + reset token）。
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
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

const SRC = findSrc()

/** 去掉注释但**保留行号**（注释里写着"旧写法 expanded = forceOpen || open"是合法文档） */
export function stripComments(text: string): string {
  type St = 'code' | 'line' | 'block' | 'single' | 'double' | 'template'
  let state: St = 'code'
  let out = ''
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    const next = text[i + 1]
    if (state === 'code') {
      if (ch === '/' && next === '/') { state = 'line'; out += '  '; i += 2; continue }
      if (ch === '/' && next === '*') { state = 'block'; out += '  '; i += 2; continue }
      if (ch === "'") state = 'single'
      else if (ch === '"') state = 'double'
      else if (ch === '`') state = 'template'
      out += ch; i += 1; continue
    }
    if (state === 'line') {
      if (ch === '\n') { state = 'code'; out += ch } else out += ' '
      i += 1; continue
    }
    if (state === 'block') {
      if (ch === '*' && next === '/') { state = 'code'; out += '  '; i += 2; continue }
      out += ch === '\n' ? '\n' : ' '
      i += 1; continue
    }
    if (ch === '\\') { out += ch + (next ?? ''); i += 2; continue }
    if ((state === 'single' && ch === "'") || (state === 'double' && ch === '"')
        || (state === 'template' && ch === '`')) state = 'code'
    out += ch; i += 1
  }
  return out
}

const RULES: Array<[string, RegExp]> = [
  ['强制 prop（force*）', /\bforce(Open|Expand|Collapse|Visible|Checked|Active|Show|Hide)\b/],
  ['恒真条件（|| true）', /\|\|\s*true\b/],
  ['强制或（… || 用户状态）', /\b(open|expanded|checked|selected|active|pressed|collapsed)\s*=\s*[^;\n]*\|\|\s*(true|open|expanded|checked|selected|active|pressed|collapsed)\b/],
]

/** 返回违规行（`行号: 规则 —— 原文`）；空数组 = 干净 */
export function scanForcedState(text: string): string[] {
  const code = stripComments(text)
  const out: string[] = []
  code.split('\n').forEach((line, idx) => {
    for (const [name, re] of RULES) {
      if (re.test(line)) out.push(`${idx + 1}: ${name} —— ${line.trim().slice(0, 100)}`)
    }
  })
  return out
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if ((name.endsWith('.tsx') || name.endsWith('.ts')) && !name.includes('.test.')) out.push(p)
  }
  return out
}

describe('强制状态守卫（用户必须改得回来）', () => {
  it('自测：旧写法必被抓到（守卫自己能红）', () => {
    expect(scanForcedState('const expanded = forceOpen || open')).not.toEqual([])
    expect(scanForcedState('const expanded = open || true')).not.toEqual([])
    expect(scanForcedState('<Collapsible forceOpen={searching} />')).not.toEqual([])
  })

  it('自测：注释里的反例与正常写法不算违规', () => {
    expect(scanForcedState('// 旧写法 expanded = forceOpen || open')).toEqual([])
    expect(scanForcedState('/* <Collapsible forceOpen={x} /> */')).toEqual([])
    expect(scanForcedState('const expanded = open')).toEqual([])
    expect(scanForcedState("const label = (data.name as string) || ''")).toEqual([])
    expect(scanForcedState('const url = "http://x//y"')).toEqual([])
  })

  it('src 里不存在"强制覆盖用户状态"的写法', () => {
    const offenders: string[] = []
    for (const file of walk(SRC)) {
      const rel = file.slice(SRC.length + 1).replace(/\\/g, '/')
      for (const hit of scanForcedState(readFileSync(file, 'utf-8'))) {
        offenders.push(`${rel}:${hit}`)
      }
    }
    expect(offenders, '\n' + offenders.join('\n')).toEqual([])
  })
})
