/**
 * hubNav 参数化推导测试 —— 缺陷 ②（复用组件初始分类/模式由导航 key 推导）
 * - assets/<category>：8 个资产子菜单共用 AssetsPage → 推导 initialCategory
 * - memory/manual | memory/auto：共用 MemoryPage → 推导 mode
 * 单一来源：key 即参数语义，避免在导航配置里重复声明导致漂移。
 */
import { describe, expect, it } from 'vitest'
import { DEFAULT_NAV_KEY, derivePanelParams, findNavItem, flattenNav, HUB_NAV } from './hubNav'

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

describe('hubNav 导航结构（技能中心与提示词实验室同属「会话与配置」桶）', () => {
  it('「技能中心」是「会话与配置」桶内的叶子项，且紧跟在「提示词实验室」之后', () => {
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    const childKeys = (workspace?.children ?? []).map((c) => c.key)
    expect(childKeys).toContain('skills-center')
    expect(childKeys.indexOf('skills-center')).toBe(childKeys.indexOf('prompt-lab') + 1)
    const item = workspace?.children?.find((c) => c.key === 'skills-center')
    expect(item?.children).toBeUndefined()
    expect(item?.component).toBeDefined()
  })

  it('「技能中心」不再挂在「记忆管理」下（原入口已移除）', () => {
    const memory = HUB_NAV.find((i) => i.key === 'memory')
    const memoryChildKeys = (memory?.children ?? []).map((c) => c.key)
    expect(memoryChildKeys).not.toContain('memory/skills-center')
    // 【阶段 1.3】经验库归位到「知识库系统」之后（只归位，不合并组件）
    expect(memoryChildKeys).toEqual([
      'memory/manual',
      'memory/auto',
      'memory/knowledge',
      'memory/experience',
      'memory/search',
    ])
    expect(flattenNav(HUB_NAV).some((i) => i.key.startsWith('memory/skills'))).toBe(false)
    // 顶层不再单列「经验库」
    expect(HUB_NAV.map((i) => i.key)).not.toContain('experience')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'memory/experience')).toBe(true)
  })

  it('技能中心与提示词实验室渲染的是不同页面组件', () => {
    const promptLab = findNavItem('prompt-lab')
    const skills = findNavItem('skills-center')
    expect(skills?.component).not.toBe(promptLab?.component)
  })
})

describe('hubNav 导航结构（工具调用子项收敛为页面内 Tab）', () => {
  it('「工具调用」是叶子项（5 个子项不再各占一条导航项）', () => {
    const tools = findNavItem('tools')
    expect(tools).toBeDefined()
    expect(tools?.children).toBeUndefined()
    expect(tools?.component).toBeDefined()
  })

  it('「工具调用」随「会话与配置」桶整体排在「全景看板」之前（配置类在前）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)
    // 阶段 3：顶层收敛为 8 桶，前四桶固定
    expect(topKeys.slice(0, 4)).toEqual(['workspace', 'panorama', 'governance', 'memory'])
    expect(topKeys).toHaveLength(8)
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    expect((workspace?.children ?? []).map((c) => c.key)).toEqual([
      'session', 'prompt-lab', 'skills-center', 'tools',
    ])
    expect(topKeys.indexOf('workspace')).toBeLessThan(topKeys.indexOf('panorama'))
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

describe('hubNav 导航结构（人格配置并入提示词实验室 · 阶段 1.1）', () => {
  it('persona 分组与其子项已从导航树移除', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    expect(keys).not.toContain('persona')
    expect(keys).not.toContain('persona/personality')
  })

  it('「提示词实验室」仍是叶子项（人格以页内 Tab 承载，不再单列导航）', () => {
    const promptLab = findNavItem('prompt-lab')
    expect(promptLab).toBeDefined()
    expect(promptLab?.children).toBeUndefined()
    expect(promptLab?.component).toBeDefined()
  })
})

describe('hubNav 顶层收敛（阶段 3 · 8 个语义桶）', () => {
  it('顶层恰好 8 个桶，顺序固定', () => {
    expect(HUB_NAV.map((i) => i.key)).toEqual([
      'workspace', 'panorama', 'governance', 'memory',
      'assets', 'workshop', 'components', 'admin',
    ])
  })

  it('桶内子项全部为叶子（不再有嵌套分组，NavPanel 一级展开即可见全部叶子）', () => {
    for (const bucket of HUB_NAV) {
      for (const child of bucket.children ?? []) {
        expect(child.children, `${bucket.key}/${child.key} 不应再有 children`).toBeUndefined()
      }
    }
  })

  it('叶子 key 全部保留（旧深链仍可解析）', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    for (const k of [
      'session', 'prompt-lab', 'skills-center', 'tools',
      'panorama/health', 'panorama/logs', 'engine/heartbeat', 'engine/scheduler',
      'governance/approvals', 'governance/route-coverage',
      'memory/experience', 'assets/interactions',
      'workshop/agents', 'components/plugins', 'network',
      'admin/dashboard', 'admin/logs',
    ]) {
      expect(keys).toContain(k)
    }
  })

  it('「循环工程」组 key 已消失（走 engine → engine/heartbeat 兼容映射）', () => {
    expect(HUB_NAV.map((i) => i.key)).not.toContain('engine')
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'engine')).toBe(false)
  })

  it('DEFAULT_NAV_KEY 指向第一个叶子（分组无 component，不能作为默认选中）', () => {
    // 回归：HUB_NAV[0] 是「会话与配置」分组；默认选中必须是可渲染的叶子「会话任务」
    expect(DEFAULT_NAV_KEY).toBe('session')
    expect(findNavItem(DEFAULT_NAV_KEY)?.component).toBeDefined()
  })
})
