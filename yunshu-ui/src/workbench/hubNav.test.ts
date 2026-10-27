/**
 * hubNav 参数化推导测试 —— 缺陷 ②（复用组件初始分类/模式由导航 key 推导）
 * - assets/<category>：8 个资产子菜单共用 AssetsPage → 推导 initialCategory
 * - memory/manual | memory/auto：共用 MemoryPage → 推导 mode
 * 单一来源：key 即参数语义，避免在导航配置里重复声明导致漂移。
 */
import { describe, expect, it } from 'vitest'
import { DEFAULT_NAV_KEY, derivePanelParams, findNavItem, flattenNav, HUB_NAV, resolveNavKey } from './hubNav'

describe('hubNav derivePanelParams（第二轮 IA：assets/memory 收敛为叶子）', () => {
  it('assets 与 memory 已是叶子项，不再推导 initialCategory / mode', () => {
    expect(derivePanelParams('assets')).toEqual({})
    expect(derivePanelParams('memory')).toEqual({})
    // 旧子项 key 不再由导航 key 推导（经 LEGACY_NAV_KEY_MAP 归一为叶子 key）
    expect(derivePanelParams('assets/memory')).toEqual({})
    expect(derivePanelParams('memory/manual')).toEqual({})
  })

  it('governance/<panel> 仍按 panel 推导', () => {
    expect(derivePanelParams('governance/approvals')).toEqual({ panel: 'approvals' })
    expect(derivePanelParams('governance/settings')).toEqual({ panel: 'settings' })
  })

  it('其它导航项无需参数（组件自带默认视图）', () => {
    expect(derivePanelParams('session')).toEqual({})
    expect(derivePanelParams('admin/users')).toEqual({})
    expect(derivePanelParams('')).toEqual({})
  })

  it('导航树里 assets/memory 已无子项 key（子项在页内切换）', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    expect(keys.filter((k) => k.startsWith('assets/'))).toEqual([])
    expect(keys.filter((k) => k.startsWith('memory/'))).toEqual([])
    expect(keys).toContain('assets')
    expect(keys).toContain('memory')
  })
})

describe('hubNav 导航结构（技能中心与提示词实验室同属「会话与配置」桶）', () => {
  it('「技能中心」是「会话与配置」桶内的叶子项，且紧跟在「吞噬」之后（获取在前、管理在后）', () => {
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    const childKeys = (workspace?.children ?? []).map((c) => c.key)
    expect(childKeys).toContain('skills-center')
    expect(childKeys).toContain('ingestion')
    expect(childKeys.indexOf('skills-center')).toBe(childKeys.indexOf('ingestion') + 1)
    expect(childKeys.indexOf('ingestion')).toBeLessThan(childKeys.indexOf('skills-center'))
    const item = workspace?.children?.find((c) => c.key === 'skills-center')
    expect(item?.children).toBeUndefined()
    expect(item?.component).toBeDefined()
  })

  it('「记忆管理」已是叶子项（5 个子项收敛为页内 Tab）', () => {
    const memory = findNavItem('memory')
    expect(memory).toBeDefined()
    expect(memory?.children).toBeUndefined()
    expect(memory?.component).toBeDefined()
    // 原 memory/<x> 子项 key 全部从导航树移除（经 LEGACY_NAV_KEY_MAP 落到 memory）
    expect(flattenNav(HUB_NAV).some((i) => i.key.startsWith('memory/'))).toBe(false)
    // 顶层不再单列「经验库」
    expect(HUB_NAV.map((i) => i.key)).not.toContain('experience')
  })

  it('技能中心与提示词实验室渲染的是不同页面组件', () => {
    const promptLab = findNavItem('prompt-lab')
    const skills = findNavItem('skills-center')
    expect(skills?.component).not.toBe(promptLab?.component)
  })
})

describe('hubNav 导航结构（2026-10-24：工作流从技能中心拆成独立叶子）', () => {
  it('「工作流」是「会话与配置」桶内的叶子，且紧跟在「技能中心」之后', () => {
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    const keys = (workspace?.children ?? []).map((c) => c.key)
    expect(keys).toContain('workflow')
    expect(keys.indexOf('workflow')).toBe(keys.indexOf('skills-center') + 1)
    const item = findNavItem('workflow')
    expect(item?.children).toBeUndefined()
    expect(item?.component).toBeDefined()
  })

  it('旧 key 经 LEGACY_NAV_KEY_MAP 落到 workflow（深链不 404 回默认页）', () => {
    expect(resolveNavKey('workflow-skills')).toBe('workflow')
    expect(resolveNavKey('workflow/visual')).toBe('workflow')
    expect(resolveNavKey('visual-workflows')).toBe('workflow')
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
    // 第三轮 IA 并为 6 桶；【第四轮 IA · 2026-10-14】再并为 4 桶
    expect(topKeys).toEqual(['workspace', 'panorama', 'governance', 'admin'])
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    // 【2026-10-17 IA】分身创建与组装紧跟「会话任务」（先对话、再配分身）
    expect((workspace?.children ?? []).map((c) => c.key)).toEqual([
      'session', 'workshop/agents', 'prompt-lab', 'ingestion', 'skills-center', 'workflow',
      'tools', 'memory', 'assets', 'components/plugins', 'local', 'replay',
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

describe('hubNav 顶层收敛（阶段 3 → 第三轮 IA 6 桶 → 第四轮 IA 4 桶）', () => {
  it('顶层恰好 4 个桶，顺序固定', () => {
    expect(HUB_NAV.map((i) => i.key)).toEqual([
      'workspace', 'panorama', 'governance', 'admin',
    ])
  })

  it('【第四轮+第五轮 IA】记忆/资产/插件/网络 并入「会话与配置」；模块列表并入「系统监控」；「系统组件」桶取消', () => {
    const workspace = HUB_NAV.find((i) => i.key === 'workspace')
    const childKeys = (workspace?.children ?? []).map((c) => c.key)
    for (const k of ['memory', 'assets', 'components/plugins']) {
      expect(childKeys).toContain(k)
    }
    const topKeys = HUB_NAV.map((i) => i.key)
    expect(topKeys).not.toContain('memory-assets')
    expect(topKeys).not.toContain('components')
    // 【第五轮 · 2026-10-14】模块列表不再是独立叶子：它与系统监控同读
    //   /api/modules/topology（双入口）⇒ 合并为系统监控页内的「模块明细」Tab。
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'components/modules')).toBe(false)
    expect(findNavItem('panorama/monitor')?.component).toBeDefined()
    // 叶子 key 不变 ⇒ 旧深链 ?panel=memory / ?panel=assets 仍解析
    expect(findNavItem('memory')?.component).toBeDefined()
    expect(findNavItem('assets')?.component).toBeDefined()
    // 2026-10-27 IA：网络配置并入「系统管理 · 实例与网络」第三 Tab
    expect(findNavItem('network')).toBeUndefined()
    expect(resolveNavKey('network')).toBe('admin/instances')
    expect(findNavItem('admin/instances')?.component).toBeDefined()
  })

  it('「记忆技能库」并入「记忆管理」：导航叶子消失、旧深链落到 memory（禁止双入口）', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    // 2026-10-18 IA：治理面板的 memory 面板只剩"四层记忆只读清单 + U5 优先级契约"，
    // 早就不含技能（技能中心已上提顶层）⇒ 并入记忆管理的「记忆结构」Tab。
    expect(keys).not.toContain('governance/memory')
    expect(resolveNavKey('governance/memory')).toBe('memory')
    // 只读治理视图仍可达：记忆管理叶子存在（旧深链因此不会落到空白页）
    expect(findNavItem('memory')?.component).toBeDefined()
    // 治理面板其余叶子不受影响
    for (const k of ['governance/pipeline', 'governance/approvals', 'governance/incidents', 'governance/route-coverage']) {
      expect(keys).toContain(k)
    }
  })

  it('「装配车间」并入「会话与配置」；「系统自我复制」已作页内视图（旧深链仍归一）', () => {
    const keys = flattenNav(HUB_NAV).map((i) => i.key)
    expect(keys).toContain('workshop/agents')
    // 2026-10-15：系统自我复制并入「分身创建与组装」第 3 视图（带走 / 自复制），
    // 导航树不再有独立叶子；旧的 ?panel=workshop/replicate 由兼容层落到本页。
    expect(keys).not.toContain('workshop/replicate')
    expect(HUB_NAV.map((i) => i.key)).not.toContain('workshop')
    expect(resolveNavKey('workshop/replicate')).toBe('workshop/agents')
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
      'session', 'prompt-lab', 'skills-center', 'workflow', 'tools',
      'panorama/health', 'panorama/logs', 'engine',
      'governance/approvals', 'governance/route-coverage',
      'memory', 'assets',
      'workshop/agents', 'components/plugins',
      'admin/dashboard', 'admin/logs',
    ]) {
      expect(keys).toContain(k)
    }
  })

  it('引擎三合一：单一叶子 engine，旧子项 key 落到它', () => {
    // 2026-10-17 IA：心跳监测 + 定时任务 + 执行历史 → 一页三 Tab
    expect(findNavItem('engine')?.component).toBeDefined()
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'engine/heartbeat')).toBe(false)
    expect(flattenNav(HUB_NAV).some((i) => i.key === 'engine/scheduler')).toBe(false)
    // 旧深链仍可解析（否则收藏/文档里的链接会落回默认页）
    expect(resolveNavKey('engine/heartbeat')).toBe('engine')
    expect(resolveNavKey('engine/scheduler')).toBe('engine')
  })

  it('DEFAULT_NAV_KEY 指向第一个叶子（分组无 component，不能作为默认选中）', () => {
    // 回归：HUB_NAV[0] 是「会话与配置」分组；默认选中必须是可渲染的叶子「会话任务」
    expect(DEFAULT_NAV_KEY).toBe('session')
    expect(findNavItem(DEFAULT_NAV_KEY)?.component).toBeDefined()
  })
})
