/**
 * 守卫：叶子页的标题必须在 Tab 条**上面**（2026-11-19）
 *
 * 【为什么要有这条】PageHeader / Tabs 各自封装了外观，但"谁在前面"没有机制约束：
 * 能力获取曾把 <Tabs> 写在 <PageHeader> 前面（标题落到 Tab 下面），引擎 / 实例与网络
 * 直接把 Tab 条当页头（压根没有标题）。这类问题只在真机截图里看得见，没有用例会红。
 *
 * 判据（源码级）：含 export default 且渲染 <Tabs> 的页面文件，第一个 <Tabs> 之前
 * 必须出现任意一个标题标记（PageTabsHeader / PageHeader / h1 / h2 / SectionTitle）。
 * 局限：只认字面量；子面板（抽屉 / Tab 内嵌组件）用 ALLOW 显式列出理由。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

const PAGES_ROOT = join(process.cwd(), 'src', 'pages')

/** 不是叶子页、由父页提供标题的子面板（名字即理由；新增时必须在 PR 里说明） */
const ALLOW = new Map<string, string>([
  ['hub/memory/skill-detail-drawer.tsx', '抽屉组件：标题由打开它的页面给'],
  ['hub/panorama/log-center-panel.tsx', 'Tab 内嵌面板：标题由父页给'],
  ['hub/panorama/ops-diagnostics.tsx', 'Tab 内嵌面板：标题由父页给'],
])

/** 判据本体（导出以便用合成源码证明"守卫真的会红"，而不是恒绿） */
export function hasTitleBeforeFirstTabs(src: string): boolean {
  if (!src.includes('export default') || !src.includes('<Tabs')) return true
  const tabsAt = src.indexOf('<Tabs')
  const marks = ['<PageTabsHeader', '<PageHeader', '<h1', '<h2', '<SectionTitle']
    .map((t) => src.indexOf(t))
    .filter((i) => i >= 0)
  return marks.some((i) => i < tabsAt)
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

describe('叶子页标题位置守卫', () => {
  it('渲染 <Tabs> 的页面必须先把标题放在 Tab 条上面', () => {
    const offenders: string[] = []
    for (const file of walk(PAGES_ROOT)) {
      if (!file.endsWith('.tsx') || file.includes('.test.')) continue
      const src = readFileSync(file, 'utf8')
      if (!src.includes('export default') || !src.includes('<Tabs')) continue
      const rel = relative(PAGES_ROOT, file).replace(/\\/g, '/')
      if (ALLOW.has(rel)) continue
      if (!hasTitleBeforeFirstTabs(src)) offenders.push(rel)
    }
    expect(offenders, '这些页面把 <Tabs> 放在了任何标题之前（标题会落到 Tab 下面）').toEqual([])
  })

  it('合成反例必须判红（证明守卫不是恒绿）', () => {
    const bad = 'export default function X(){ return <div><Tabs items={[]} value="" onChange={() => {}} /><PageHeader title="t" /></div> }'
    const good = 'export default function X(){ return <div><PageHeader title="t" /><Tabs items={[]} value="" onChange={() => {}} /></div> }'
    expect(hasTitleBeforeFirstTabs(bad)).toBe(false)
    expect(hasTitleBeforeFirstTabs(good)).toBe(true)
  })

  it('allowlist 里没有已不存在的文件（避免守卫悄悄失效）', () => {
    for (const rel of ALLOW.keys()) {
      expect(() => statSync(join(PAGES_ROOT, rel))).not.toThrow()
    }
  })
})
