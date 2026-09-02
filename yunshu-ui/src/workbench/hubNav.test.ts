/**
 * hubNav 参数化推导测试 —— 缺陷 ②（复用组件初始分类/模式由导航 key 推导）
 * - assets/<category>：8 个资产子菜单共用 AssetsPage → 推导 initialCategory
 * - memory/manual | memory/auto：共用 MemoryPage → 推导 mode
 * 单一来源：key 即参数语义，避免在导航配置里重复声明导致漂移。
 */
import { describe, expect, it } from 'vitest'
import { derivePanelParams, flattenNav, HUB_NAV } from './hubNav'

describe('hubNav derivePanelParams（缺陷② · assets/记忆菜单参数化）', () => {
  it('assets/<category> → initialCategory（与资产类别 key 一致）', () => {
    expect(derivePanelParams('assets/memory')).toEqual({ initialCategory: 'memory' })
    expect(derivePanelParams('assets/prompts')).toEqual({ initialCategory: 'prompts' })
    expect(derivePanelParams('assets/tools')).toEqual({ initialCategory: 'tools' })
    expect(derivePanelParams('assets/interactions')).toEqual({ initialCategory: 'interactions' })
  })

  it('memory/manual 与 memory/auto → mode', () => {
    expect(derivePanelParams('memory/manual')).toEqual({ mode: 'manual' })
    expect(derivePanelParams('memory/auto')).toEqual({ mode: 'auto' })
  })

  it('其它导航项无需参数（组件自带默认视图）', () => {
    expect(derivePanelParams('session')).toEqual({})
    expect(derivePanelParams('memory/skills')).toEqual({})
    expect(derivePanelParams('memory/knowledge')).toEqual({})
    expect(derivePanelParams('admin/users')).toEqual({})
    expect(derivePanelParams('')).toEqual({})
  })

  it('导航树 assets 组全部 8 项、memory manual/auto 项均可被推导覆盖', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    const assets = keys.filter((k) => k.startsWith('assets/'))
    expect(assets).toHaveLength(8)
    for (const k of assets) {
      expect(derivePanelParams(k).initialCategory).toBe(k.slice('assets/'.length))
    }
    expect(derivePanelParams('memory/manual').mode).toBe('manual')
    expect(derivePanelParams('memory/auto').mode).toBe('auto')
    // assets 8 子菜单确认都映射到 AssetsPage（同一组件）
    const assetsComponents = flattenNav(HUB_NAV)
      .filter((i) => i.key.startsWith('assets/'))
      .map((i) => i.component)
    expect(new Set(assetsComponents).size).toBe(1)
    expect(assetsComponents[0]).toBeDefined()
  })
})
