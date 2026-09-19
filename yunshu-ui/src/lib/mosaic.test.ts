/**
 * mosaic 布局定义与下线面板迁移测试
 * ------------------------------------------------------------------
 * 背景：右侧「思考过程」面板（panelId = think）已下线 —— 思考过程与工具调用步骤
 * 改为在每条回复内联显示。这里锁死两件事：
 *   1. 默认布局只含 nav / chat / code（不再出现 think）；
 *   2. 历史 localStorage 布局里若含 think，迁移时**只剔除该面板**，其余布局意图
 *      （拆方向、比例、tabs）保留 —— 而不是整份被判脏后回退默认。
 */
import { describe, expect, it } from 'vitest'
import type { MosaicNode } from 'react-mosaic-component'
import {
  DEFAULT_LAYOUT,
  PANEL,
  PANEL_TITLES,
  RETIRED_PANEL_IDS,
  sanitizeLayout,
  stripRetiredPanels,
} from './mosaic'

/** 下线前的默认布局（历史数据形态）：nav | (chat | (think / code)) */
const LEGACY_LAYOUT = {
  type: 'split',
  direction: 'row',
  children: [
    'nav',
    {
      type: 'split',
      direction: 'row',
      children: [
        'chat',
        {
          type: 'split',
          direction: 'column',
          children: ['think', 'code'],
          splitPercentages: [55, 45],
        },
      ],
      splitPercentages: [72, 28],
    },
  ],
  splitPercentages: [16, 84],
}

/** 收集布局树里的全部叶子面板 ID */
function leaves(node: MosaicNode<string> | null | unknown): string[] {
  if (typeof node === 'string') return [node]
  if (node && typeof node === 'object') {
    const n = node as { type?: string; children?: unknown[]; tabs?: string[] }
    if (n.type === 'split' && Array.isArray(n.children)) return n.children.flatMap(leaves)
    if (n.type === 'tabs' && Array.isArray(n.tabs)) return [...n.tabs]
  }
  return []
}

describe('默认布局（思考面板下线后）', () => {
  it('面板集合为 nav / chat / code，不含 think', () => {
    expect(Object.values(PANEL)).toEqual(['nav', 'chat', 'code'])
    expect('think' in PANEL_TITLES).toBe(false)
    expect(RETIRED_PANEL_IDS).toContain('think')
    expect(leaves(DEFAULT_LAYOUT)).toEqual(['nav', 'chat', 'code'])
  })

  it('默认布局可被 sanitizeLayout 接受（比例与 children 数量对齐）', () => {
    expect(sanitizeLayout(DEFAULT_LAYOUT)).not.toBeNull()
  })

  it('主内容区吃掉原思考面板的宽度，代码编辑器宽度基本不变', () => {
    const root = DEFAULT_LAYOUT as { children: unknown[]; splitPercentages: number[] }
    expect(root.splitPercentages).toEqual([16, 84])
    const middle = root.children[1] as { children: string[]; splitPercentages: number[] }
    expect(middle.children).toEqual(['chat', 'code'])
    // chat ≈ 84*0.87 ≈ 73%，code ≈ 84*0.13 ≈ 11%（下线前 code ≈ 10.6%）
    expect(middle.splitPercentages[0]).toBeGreaterThan(80)
    expect(84 * (middle.splitPercentages[1] / 100)).toBeCloseTo(10.9, 0)
  })
})

describe('历史布局迁移（stripRetiredPanels）', () => {
  it('剔除 think 叶子，并折叠只剩一个子节点的 split', () => {
    const migrated = stripRetiredPanels(LEGACY_LAYOUT) as Record<string, unknown>
    expect(leaves(migrated)).toEqual(['nav', 'chat', 'code'])
    // 迁移结果仍然是合法布局（不会整份被判脏 → 用户的比例/拆分得以保留）
    expect(sanitizeLayout(migrated)).not.toBeNull()
  })

  it('保留与 children 数量对齐的 splitPercentages（外层 16/84 不丢）', () => {
    const migrated = stripRetiredPanels(LEGACY_LAYOUT) as Record<string, unknown>
    expect(migrated.splitPercentages).toEqual([16, 84])
    const middle = (migrated.children as unknown[])[1] as Record<string, unknown>
    expect(middle.splitPercentages).toEqual([72, 28])
  })

  it('tabs 里的 think 被剔除；剩一个则折叠为叶子', () => {
    const tabs = { type: 'tabs', tabs: ['think', 'code'], activeTabIndex: 0 }
    expect(stripRetiredPanels(tabs)).toBe('code')
    const twoLeft = { type: 'tabs', tabs: ['think', 'code', 'chat'], activeTabIndex: 2 }
    const out = stripRetiredPanels(twoLeft) as { tabs: string[] }
    expect(out.tabs).toEqual(['code', 'chat'])
  })

  it('只含 think 的布局 → 全部移除（返回 null，交由默认布局兜底）', () => {
    expect(stripRetiredPanels('think')).toBeNull()
    expect(stripRetiredPanels({ type: 'split', direction: 'row', children: ['think', 'think'] })).toBeNull()
  })

  it('未含下线面板的布局原样通过（幂等）', () => {
    const out = stripRetiredPanels({ type: 'split', direction: 'row', children: ['nav', 'chat'], splitPercentages: [30, 70] })
    expect(out).toEqual({ type: 'split', direction: 'row', children: ['nav', 'chat'], splitPercentages: [30, 70] })
  })

  it('非法结构返回 null（不抛异常，持久化脏数据不白屏）', () => {
    expect(stripRetiredPanels(undefined)).toBeNull()
    expect(stripRetiredPanels(42)).toBeNull()
    expect(stripRetiredPanels({ type: 'unknown' })).toBeNull()
  })
})
