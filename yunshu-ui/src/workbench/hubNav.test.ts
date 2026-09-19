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

describe('hubNav 导航结构（技能中心上提为顶层项）', () => {
  it('「技能中心」是顶层叶子项，且紧跟在「提示词实验室」之后', () => {
    const topKeys = HUB_NAV.map((i) => i.key)
    expect(topKeys).toContain('skills-center')
    expect(topKeys.indexOf('skills-center')).toBe(topKeys.indexOf('prompt-lab') + 1)
    const item = HUB_NAV.find((i) => i.key === 'skills-center')
    expect(item?.children).toBeUndefined()
    expect(item?.component).toBeDefined()
  })

  it('「技能中心」不再挂在「记忆管理」下（原入口已移除）', () => {
    const memory = HUB_NAV.find((i) => i.key === 'memory')
    const memoryChildKeys = (memory?.children ?? []).map((c) => c.key)
    expect(memoryChildKeys).not.toContain('memory/skills-center')
    expect(memoryChildKeys).toEqual([
      'memory/manual',
      'memory/auto',
      'memory/knowledge',
      'memory/search',
    ])
    expect(flattenNav(HUB_NAV).some((i) => i.key.startsWith('memory/skills'))).toBe(false)
  })

  it('技能中心与提示词实验室渲染的是不同页面组件', () => {
    const promptLab = HUB_NAV.find((i) => i.key === 'prompt-lab')
    const skills = HUB_NAV.find((i) => i.key === 'skills-center')
    expect(skills?.component).not.toBe(promptLab?.component)
  })
})

describe('hubNav 导航结构（工具调用子项收敛为页面内 Tab）', () => {
  it('「工具调用」是顶层叶子项（5 个子项不再各占一条导航项）', () => {
    const tools = HUB_NAV.find((i) => i.key === 'tools')
    expect(tools).toBeDefined()
    expect(tools?.children).toBeUndefined()
    expect(tools?.component).toBeDefined()
  })

  it('「工具调用」位于「全景看板」之前（配置类模块在前）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)
    expect(topKeys.indexOf('tools')).toBeGreaterThanOrEqual(0)
    expect(topKeys.indexOf('tools')).toBe(topKeys.indexOf('panorama') - 1)
    // 顶层前四项目前为：会话任务 → 提示词实验室 → 技能中心 → 工具调用
    expect(topKeys.slice(0, 4)).toEqual(['session', 'prompt-lab', 'skills-center', 'tools'])
  })

  it('导航树里已不存在 tools/<子项> 键（避免与页面内 Tab 双份入口）', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    expect(keys.filter((k) => k.startsWith('tools/'))).toEqual([])
    for (const legacy of ['tools/toolset', 'tools/cli', 'tools/mcp', 'tools/computer-use', 'tools/lines']) {
      expect(keys).not.toContain(legacy)
    }
  })

  it('工具调用无需由导航 key 推导参数（Tab 自带 localStorage 记忆）', () => {
    expect(derivePanelParams('tools')).toEqual({})
  })
})
