/**
 * 守卫：满宽 / flex-1 的 Button 必须显式声明对齐方式
 * ------------------------------------------------------------------
 * 【为什么需要】Button 基类是 `inline-flex items-center justify-center`：
 * 一旦它被当成"行"用（w-full / flex-1 把宽度撑满），内容就会**居中**。
 * 而作者往往只写了 `text-left` —— 那管的是行内文本，不管 flex 子项的落位。
 * 2026-11-19 Owner 连续报了三次同类问题：会话行 / 右坞-文档文件名 / 本机可吞噬源；
 * 三次都是同一个根因。所以在这里把它变成**会失败的测试**，而不是靠人记。
 *
 * 规则（扫描 src/**\/*.tsx，排除测试文件）：
 *  1. <Button> 的 className 含 `w-full` 或 `flex-1` ⇒ 必须同时含 `justify-start` 或 `justify-center`；
 *  2. className 含 `text-left` ⇒ 必须含 `justify-start`（text-left 单独出现一定是误用）。
 * 局限：只认字面量 className（cn(...) / 变量拼接看不到）—— 这是有意的：
 * 动态拼出来的对齐无法静态判定，看到就应当拆成字面量。
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

/** src 根目录：从 cwd 往上找（vitest 的 jsdom 环境下 import.meta.url 不是 file: 协议） */
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

function walkTsx(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walkTsx(p, out)
    else if (name.endsWith('.tsx') && !name.endsWith('.test.tsx')) out.push(p)
  }
  return out
}

/** 从 `<Button` 到对应的 `>`。
 * 【2026-11-21 修】原先只跳过字符串 ⇒ `onClick={() => ...}` 里箭头函数的 `>`
 * 会把标签**提前截断**，className 落在截断点之后 ⇒ 守卫对"处理器写在 className 之前"
 * 的 Button 全部失明（分身-主线档案 boost 窗就是这种写法，漏了三个月）。
 * 现在同时跟踪 `{}` 花括号深度（花括号里再按字符串规则跳过）。 */
function skipString(text: string, i: number): number {
  const q = text[i]
  i += 1
  while (i < text.length && text[i] !== q) i += text[i] === '\\' ? 2 : 1
  return i + 1
}

function skipBraces(text: string, i: number): number {
  let depth = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '"' || ch === "'" || ch === '`') {
      i = skipString(text, i)
      continue
    }
    if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth === 0) return i + 1
    }
    i += 1
  }
  return i
}

function tagAt(text: string, start: number): string {
  let i = start
  while (i < text.length) {
    const ch = text[i]
    if (ch === '"' || ch === "'" || ch === '`') {
      i = skipString(text, i)
      continue
    }
    if (ch === '{') {
      i = skipBraces(text, i)
      continue
    }
    if (ch === '>') return text.slice(start, i)
    i += 1
  }
  return text.slice(start)
}

function classNameOf(tag: string): string {
  const m = /className=(?:"([^"]*)"|\{`([^`]*)`\})/.exec(tag)
  return m ? m[1] ?? m[2] ?? '' : ''
}

describe('Button 对齐守卫', () => {
  it('满宽/flex-1 的 Button 必须声明 justify-start 或 justify-center；text-left 必须配 justify-start', () => {
    const offenders: string[] = []
    for (const file of walkTsx(SRC)) {
      const text = readFileSync(file, 'utf-8')
      const rel = file.slice(SRC.length + 1).replace(/\\/g, '/')
      let idx = text.indexOf('<Button')
      while (idx >= 0) {
        const tag = tagAt(text, idx)
        const cls = classNameOf(tag)
        const line = text.slice(0, idx).split('\n').length
        const full = cls.includes('w-full') || cls.includes('flex-1')
        const aligned = cls.includes('justify-start') || cls.includes('justify-center')
        if (full && !aligned) {
          offenders.push(`${rel}:${line} 满宽但未声明对齐：${cls}`)
        } else if (cls.includes('text-left') && !cls.includes('justify-start')
                   // 【2026-11-21】flex-col 按钮的主轴是纵向：justify-* 管的是垂直居中，
                   // 与"文字左对齐"无关（SessionRail 的会话行就是 flex-col + 标题/时间两行）。
                   && !cls.includes('flex-col')) {
          offenders.push(`${rel}:${line} text-left 但缺 justify-start：${cls}`)
        }
        idx = text.indexOf('<Button', idx + 7)
      }
    }
    expect(offenders, '\n' + offenders.join('\n')).toEqual([])
  })
})
