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
  DOCK_DEFAULT_PERCENT,
  DEFAULT_LAYOUT,
  DOCK_PANEL_IDS,
  NAV_DEFAULT_PERCENT,
  PANEL,
  PANEL_TITLES,
  RETIRED_PANEL_IDS,
  addPanelToLayout,
  layoutHasPanel,
  removePanelFromLayout,
  sanitizeLayout,
  stripPanels,
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

describe('默认布局（思考面板下线 + 右栏搬去抽屉之后）', () => {
  it('面板 ID 集合仍是 nav / chat / code，不含 think', () => {
    expect(Object.values(PANEL)).toEqual(['nav', 'chat', 'code'])
    expect('think' in PANEL_TITLES).toBe(false)
    expect(RETIRED_PANEL_IDS).toContain('think')
  })

  it('默认布局 = nav | chat | 右坞（2026-11-19 起右坞默认打开，占 25%）', () => {
    expect(leaves(DEFAULT_LAYOUT)).toEqual(['nav', 'chat', 'code'])
    expect(DOCK_PANEL_IDS).toEqual(['code'])
    const root = DEFAULT_LAYOUT as { children: unknown[]; splitPercentages: number[] }
    expect(root.splitPercentages).toEqual([
      NAV_DEFAULT_PERCENT,
      100 - NAV_DEFAULT_PERCENT - DOCK_DEFAULT_PERCENT,
      DOCK_DEFAULT_PERCENT,
    ])
  })

  it('默认布局可被 sanitizeLayout 接受（比例与 children 数量对齐）', () => {
    expect(sanitizeLayout(DEFAULT_LAYOUT)).not.toBeNull()
  })
})

describe('面板显隐算子（顶栏开关用）', () => {
  it('layoutHasPanel 认叶子与 tabs', () => {
    expect(layoutHasPanel(DEFAULT_LAYOUT, 'nav')).toBe(true)
    expect(layoutHasPanel(DEFAULT_LAYOUT, 'code')).toBe(true)
    expect(layoutHasPanel({ type: 'tabs', tabs: ['chat', 'code'], activeTabIndex: 0 }, 'code')).toBe(true)
    expect(layoutHasPanel(null, 'nav')).toBe(false)
  })

  it('removePanelFromLayout：剩多个子节点时按原占比等比放大（不偷偷变成均分）', () => {
    const three = {
      type: 'split' as const,
      direction: 'row' as const,
      children: ['nav', 'chat', 'code'] as ('nav' | 'chat' | 'code')[],
      splitPercentages: [20, 50, 30],
    }
    const out = removePanelFromLayout(three, 'code') as {
      children: string[]
      splitPercentages: number[]
    }
    expect(out.children).toEqual(['nav', 'chat'])
    expect(out.splitPercentages[0]).toBeCloseTo((20 / 70) * 100, 5)
    expect(out.splitPercentages[1]).toBeCloseTo((50 / 70) * 100, 5)
  })

  it('addPanelToLayout(右坞)：插到最右，主内容让出宽度、导航不动', () => {
    // 默认树里已有右坞 ⇒ 幂等（2026-11-19 起右坞默认打开）
    expect(addPanelToLayout(DEFAULT_LAYOUT, 'code', 22)).toBe(DEFAULT_LAYOUT)

    // 两栏布局（nav | chat）插右坞：导航宽度不变、主内容让出 22%
    const two = {
      type: 'split' as const,
      direction: 'row' as const,
      children: ['nav', 'chat'] as ('nav' | 'chat')[],
      splitPercentages: [11, 89],
    }
    const withDockRaw = addPanelToLayout(two, 'code', 22)
    const withDock = withDockRaw as {
      children: string[]
      splitPercentages: number[]
    }
    expect(withDock.children).toEqual(['nav', 'chat', 'code'])
    expect(withDock.splitPercentages[0]).toBe(11) // 导航宽度不变
    expect(withDock.splitPercentages[2]).toBe(22) // 右坞按传入占比
    expect(withDock.splitPercentages[1]).toBeCloseTo(67, 5) // 主内容让出 22%
    // 已在树里 ⇒ 幂等
    expect(addPanelToLayout(withDockRaw, 'code', 22)).toBe(withDockRaw)

    // 只有一个面板时也能插（包一层横向 split）
    const onlyChat = addPanelToLayout('chat', 'code', 22) as {
      children: string[]
      splitPercentages: number[]
    }
    expect(onlyChat.children).toEqual(['chat', 'code'])
    expect(onlyChat.splitPercentages).toEqual([78, 22])
  })

  it('addPanelToLayout：导航插回第 0 位、主内容插回第 1 位（11/89）', () => {
    const two = {
      type: 'split' as const,
      direction: 'row' as const,
      children: ['nav', 'chat'] as ('nav' | 'chat')[],
      splitPercentages: [11, 89],
    }
    const withoutNav = addPanelToLayout(two, 'nav') // 已在树里 → 原样返回
    expect(withoutNav).toBe(two)

    const onlyChat = removePanelFromLayout(two, 'nav')
    expect(onlyChat).toBe('chat')
    const back = addPanelToLayout(onlyChat, 'nav') as {
      children: string[]
      splitPercentages: number[]
    }
    expect(back.children).toEqual(['nav', 'chat'])
    expect(back.splitPercentages).toEqual([11, 89])

    const onlyNav = removePanelFromLayout(two, 'chat')
    expect(onlyNav).toBe('nav')
    const chatBack = addPanelToLayout(onlyNav, 'chat') as {
      children: string[]
      splitPercentages: number[]
    }
    expect(chatBack.children).toEqual(['nav', 'chat'])
    expect(chatBack.splitPercentages).toEqual([11, 89])
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

  it('右坞开着的历史布局：收起右坞≠导航被拉宽（16/84 仍是 16/84，不是 50/50）', () => {
    // 2026-11-19 修：此前剔除子节点后 splitPercentages 被丢掉 ⇒ mosaic 均分 ⇒ 导航变 50%
    const withDock = {
      type: 'split',
      direction: 'row',
      children: ['nav', 'chat', 'code'],
      splitPercentages: [16, 84, 22],
    }
    const out = stripPanels(withDock, ['code']) as { children: string[]; splitPercentages: number[] }
    expect(out.children).toEqual(['nav', 'chat'])
    expect(out.splitPercentages).toEqual([16, 84])
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
