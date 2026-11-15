/**
 * useLayoutStore · 持久化布局迁移（下线面板 think + 搬到抽屉的右栏 code）
 * ------------------------------------------------------------------
 * 用户浏览器里可能存着**含右侧「思考过程」面板**的历史布局（已下线），
 * 2026-11-15 B 步起右栏（code）也搬到了覆盖式抽屉、不再是布局树里的常驻格子。
 * 两者若不迁移，sanitizeLayout 会把整份布局判为脏数据 → 回退默认，用户的拖拽比例全丢。
 * 本测试用「预置 localStorage + 重新加载 store」验证真实水合路径：
 * 迁移后 think / code 消失，nav / chat 与拆方向/比例保留。
 */
import { describe, expect, it, vi } from 'vitest'

/** 下线前的布局（含对 think 的列拆分） */
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
        { type: 'split', direction: 'column', children: ['think', 'code'], splitPercentages: [55, 45] },
      ],
      splitPercentages: [72, 28],
    },
  ],
  splitPercentages: [16, 84],
}

vi.stubGlobal('localStorage', {
  getItem: vi.fn((key: string) =>
    key === 'yunshu:mosaic:layout:v1'
      ? // rightDockOpen=true：同时验证"抽屉开合状态"这条新持久化字段能水合回来
        JSON.stringify({ state: { layout: LEGACY_LAYOUT, rightDockOpen: true }, version: 1 })
      : null,
  ),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
  key: vi.fn(() => null),
  length: 1,
})

const { useLayoutStore } = await import('./useLayoutStore')

function leaves(node: unknown): string[] {
  if (typeof node === 'string') return [node]
  if (node && typeof node === 'object') {
    const n = node as { type?: string; children?: unknown[]; tabs?: string[] }
    if (n.type === 'split' && Array.isArray(n.children)) return n.children.flatMap(leaves)
    if (n.type === 'tabs' && Array.isArray(n.tabs)) return [...n.tabs]
  }
  return []
}

describe('布局水合：下线面板 think / 搬到抽屉的 code 自动迁移', () => {
  it('历史布局里的 think 与 code 被剔除，nav / chat 保留', () => {
    const layout = useLayoutStore.getState().layout
    expect(layout).not.toBeNull()
    expect(leaves(layout)).toEqual(['nav', 'chat'])
  })

  it('右栏开合状态（rightDockOpen）随布局一起水合', () => {
    expect(useLayoutStore.getState().rightDockOpen).toBe(true)
  })

  it('保留拆方向与外层比例（不因迁移而重置为默认布局）', () => {
    const layout = useLayoutStore.getState().layout as unknown as {
      direction: string
      splitPercentages: number[]
    }
    expect(layout.direction).toBe('row')
    expect(layout.splitPercentages).toEqual([16, 84])
  })

  it('重置布局 = 默认树（nav | chat）+ 右栏收起', () => {
    useLayoutStore.getState().resetLayout()
    expect(leaves(useLayoutStore.getState().layout)).toEqual(['nav', 'chat'])
    expect(useLayoutStore.getState().rightDockOpen).toBe(false)
  })
})
