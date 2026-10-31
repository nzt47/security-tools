/**
 * 导航检索 + 深链 单元测试（2026-09-22）
 * ------------------------------------------------
 * 覆盖三件本次新增的能力，以及一件被修的信息架构缺陷：
 *   1. `filterNav`：侧栏搜索框的唯一实现（含"命中分组整组保留"这条易被改错的规则）
 *   2. `navKeyFromSearch` / `searchForNavKey`：深链的读与写（**必须校验合法性**）
 *   3. 「治理与运营」（2026-10-31 前叫「治理面板」）的层级位置：本次上移是为了"滚不到就找不到"这个实测问题不回归
 *
 * 【为什么这些是纯函数测试而不是渲染测试】判定逻辑一旦塞进组件就只能靠 DOM 断言，
 * 而"未知 ?panel= 不许写进状态"这类边界用渲染测试很难表达清楚（要造出空白页再断言）。
 * 纯函数留在这里；组件层的搜索框另有 NavPanel.search.test.tsx 覆盖交互。
 */
import { describe, expect, it } from 'vitest'
import {
  HUB_NAV,
  filterNav,
  navKeyFromSearch,
  searchForNavKey,
  flattenNav,
  LEGACY_NAV_KEY_MAP,
  resolveNavKey,
  findNavItem,
  type HubNavItem,
} from './hubNav'

describe('filterNav —— 侧栏搜索', () => {
  it('空查询原样返回同一引用（不做无谓的树拷贝）', () => {
    expect(filterNav(HUB_NAV, '')).toBe(HUB_NAV)
    expect(filterNav(HUB_NAV, '   ')).toBe(HUB_NAV)
  })

  it('命中子项 ⇒ 只保留该子项（保留其所在分组作为路径）', () => {
    const tree = filterNav(HUB_NAV, '审批')
    const keys = flattenNav(tree).map((i) => i.key)

    expect(keys).toContain('governance/approvals')
    expect(keys).toContain('governance')          // 分组作为路径保留
    expect(keys).not.toContain('governance/pipeline')
    expect(keys).not.toContain('memory/manual')
  })

  it('命中分组名 ⇒ 整组保留（用户要找的是整个面板，不是猜子项名）', () => {
    const tree = filterNav(HUB_NAV, '治理')
    const keys = flattenNav(tree).map((i) => i.key)

    // 「审计导出 / 开关中心」2026-10-15 已移到「全景看板」桶（运行时运维），
    // 命中「治理」不再包含它们；这里用仍在治理面板的 route-coverage 代替。
    for (const child of ['governance/pipeline', 'governance/approvals', 'governance/route-coverage']) {
      expect(keys).toContain(child)
    }
    expect(keys).not.toContain('memory/manual')
  })

  it('大小写不敏感（对 key 匹配；标签是中文，走 key 才需要这条）', () => {
    const keys = flattenNav(filterNav(HUB_NAV, 'GOVERNANCE')).map((i) => i.key)
    expect(keys).toContain('governance/approvals')
  })

  it('无命中 ⇒ 空数组（组件据此渲染"没有匹配"而不是空白侧栏）', () => {
    expect(filterNav(HUB_NAV, '这个功能不存在xyz')).toEqual([])
  })

  it('**不就地改写** HUB_NAV（否则搜一次之后导航永久变短）', () => {
    const before = HUB_NAV.length
    const beforeKeys = flattenNav(HUB_NAV).map((i) => i.key)

    filterNav(HUB_NAV, '审批')

    expect(HUB_NAV.length).toBe(before)
    expect(flattenNav(HUB_NAV).map((i) => i.key)).toEqual(beforeKeys)
  })
})

describe('深链：navKeyFromSearch（读）', () => {
  it('合法 key ⇒ 原样返回', () => {
    expect(navKeyFromSearch('?panel=governance/approvals')).toBe('governance/approvals')
    expect(navKeyFromSearch('?foo=1&panel=session')).toBe('session')
  })

  it('无参数 / 空值 ⇒ 空串（不覆盖默认选中项）', () => {
    expect(navKeyFromSearch('')).toBe('')
    expect(navKeyFromSearch('?foo=1')).toBe('')
    expect(navKeyFromSearch('?panel=')).toBe('')
  })

  it('**不在导航树里的 key 一律拒绝**（否则手改地址栏会把工作台带到空白页）', () => {
    expect(navKeyFromSearch('?panel=not-a-real-panel')).toBe('')
    expect(navKeyFromSearch('?panel=governance/nope')).toBe('')
  })
})

describe('深链：searchForNavKey（写）', () => {
  it('写入 panel 且保留其它查询参数', () => {
    expect(searchForNavKey('?foo=1', 'session')).toBe('?foo=1&panel=session')
  })

  it('覆盖同名参数而不是追加第二份', () => {
    expect(searchForNavKey('?panel=session', 'governance/approvals'))
      .toBe('?panel=governance%2Fapprovals')
  })

  it('key 为空 ⇒ 删除该参数（清空后不留 ?panel= 尾巴）', () => {
    expect(searchForNavKey('?panel=session', '')).toBe('')
    expect(searchForNavKey('?foo=1&panel=session', '')).toBe('?foo=1')
  })

  it('写进去的 key 能被读回来（读写同源，防两处口径漂移）', () => {
    const search = searchForNavKey('?x=1', 'governance/approvals')
    expect(navKeyFromSearch(search)).toBe('governance/approvals')
  })
})

describe('信息架构：「治理与运营」必须一屏可达', () => {
  it('紧跟在「全景看板」之后（本次上移的正面判据）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)

    expect(topKeys.indexOf('governance')).toBe(topKeys.indexOf('panorama') + 1)
  })

  it('不再排在末位桶之后（侧栏靠底 ⇒ 滚不到 ⇒ 找不到）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)

    // 【第四轮 IA】「系统组件」桶已取消，此刻的"桶底"是「系统管理」。
    // 判据不变：治理面板必须排在最后一个桶之前。
    expect(topKeys.indexOf('governance')).toBeLessThan(topKeys.indexOf('admin'))
    expect(topKeys).toEqual(['workspace', 'panorama', 'governance', 'admin'])
  })

  it('「审批收件箱」仍是治理与运营下的子项（入口没被搬走）', () => {
    const gov = HUB_NAV.find((i) => i.key === 'governance')
    const childKeys = (gov?.children ?? []).map((c) => c.key)

    expect(childKeys).toContain('governance/approvals')
    expect(childKeys[0]).toBe('governance/pipeline')
    expect(childKeys[1]).toBe('governance/approvals')
  })

  it('「路由覆盖」是治理与运营的子项（阶段 2 归位，不再是顶层自动发现项）', () => {
    const gov = HUB_NAV.find((i) => i.key === 'governance')
    const childKeys = (gov?.children ?? []).map((c) => c.key)

    expect(childKeys).toContain('governance/route-coverage')
    expect(HUB_NAV.map((i) => i.key)).not.toContain('route-coverage')
  })

  it('运行时运维子项（审计导出 / 开关中心）挂在「全景看板」桶；路由覆盖仍在治理与运营（2026-10-15 IA）', () => {
    const panoKeys = (HUB_NAV.find((i) => i.key === 'panorama')?.children ?? []).map((c) => c.key)
    expect(panoKeys).toContain('governance/audit')
    expect(panoKeys).toContain('governance/settings')

    const govKeys = (HUB_NAV.find((i) => i.key === 'governance')?.children ?? []).map((c) => c.key)
    expect(govKeys).toContain('governance/route-coverage')
    expect(govKeys).not.toContain('governance/audit')
    expect(govKeys).not.toContain('governance/settings')
  })

  it('【2026-10-31 IA 第二批】「安全与权限」归入「系统管理」桶；key 不变 ⇒ 深链仍解析', () => {
    const adminKeys = (HUB_NAV.find((i) => i.key === 'admin')?.children ?? []).map((c) => c.key)
    expect(adminKeys).toContain('governance/security')

    const govKeys = (HUB_NAV.find((i) => i.key === 'governance')?.children ?? []).map((c) => c.key)
    expect(govKeys).not.toContain('governance/security')

    // 深链与组件参数都按 key 推导，不认桶 ⇒ 入口照旧可达
    expect(navKeyFromSearch('?panel=governance%2Fsecurity')).toBe('governance/security')
    expect(findNavItem('governance/security')?.component).toBeDefined()
  })
})

describe('旧深链兼容层（阶段 0 · IA 整合不断链）', () => {
  it('映射表覆盖方案 §6 三组旧 key + 2026-10-17 引擎三合一', () => {
    expect(LEGACY_NAV_KEY_MAP['persona/personality']).toBe('prompt-lab')
    expect(LEGACY_NAV_KEY_MAP['experience']).toBe('memory')
    // 【2026-10-28 IA · 方案 A】插件并入「工具与接入」⇒ 旧 key 落到 tools
    expect(LEGACY_NAV_KEY_MAP['extensions']).toBe('tools')
    expect(LEGACY_NAV_KEY_MAP['components/plugins']).toBe('tools')
    // 【2026-10-17 IA】心跳/定时任务/执行历史 → 叶子 engine；
    // 旧子项 key 一并落到本页，engine 自身已是真实叶子（无需自映射）
    expect(LEGACY_NAV_KEY_MAP['engine/heartbeat']).toBe('engine')
    expect(LEGACY_NAV_KEY_MAP['engine/scheduler']).toBe('engine')
    expect(LEGACY_NAV_KEY_MAP['engine']).toBeUndefined()
    // 第三轮 IA：装配车间并入会话与配置 ⇒ 组 key 落到其原第一个子项
    expect(LEGACY_NAV_KEY_MAP['workshop']).toBe('workshop/agents')
    // 【第四轮 IA】「记忆与资产」「系统组件」两个桶取消 ⇒ 组 key 也落到其原第一个子项
    expect(LEGACY_NAV_KEY_MAP['memory-assets']).toBe('memory')
    // 【第五轮】模块列表并入系统监控 ⇒ 旧组 key 与旧叶子 key 都落到 panorama/monitor
    expect(LEGACY_NAV_KEY_MAP['components']).toBe('panorama/monitor')
    expect(LEGACY_NAV_KEY_MAP['components/modules']).toBe('panorama/monitor')
    // 第二轮 IA：记忆/资产子项 → 叶子
    expect(LEGACY_NAV_KEY_MAP['memory/experience']).toBe('memory')
    expect(LEGACY_NAV_KEY_MAP['assets/memory']).toBe('assets')
  })

  it('迁移已落地 ⇒ 全部旧 key 落到新 key（深链不断）', () => {
    expect(resolveNavKey('persona/personality')).toBe('prompt-lab')
    expect(resolveNavKey('experience')).toBe('memory')
    expect(resolveNavKey('extensions')).toBe('tools')
    expect(resolveNavKey('engine')).toBe('engine')
    expect(resolveNavKey('engine/heartbeat')).toBe('engine')
    expect(resolveNavKey('engine/scheduler')).toBe('engine')
    expect(resolveNavKey('workshop')).toBe('workshop/agents')
    expect(navKeyFromSearch('?panel=workshop')).toBe('workshop/agents')
    expect(navKeyFromSearch('?panel=experience')).toBe('memory')
    expect(navKeyFromSearch('?panel=persona/personality')).toBe('prompt-lab')
    expect(navKeyFromSearch('?panel=extensions')).toBe('tools')
  })

  it('旧 key 已移除但新 key 在树里 ⇒ 落到新 key（深链不断）', () => {
    const icon = HUB_NAV[0].icon
    const afterMigration: HubNavItem[] = [
      { key: 'prompt-lab', label: '提示词实验室', icon },
      { key: 'memory', label: '记忆管理', icon },
      { key: 'assets', label: '资产管理', icon },
      { key: 'tools', label: '工具与接入', icon },
    ]
    expect(resolveNavKey('persona/personality', afterMigration)).toBe('prompt-lab')
    expect(resolveNavKey('experience', afterMigration)).toBe('memory')
    expect(resolveNavKey('extensions', afterMigration)).toBe('tools')
    // 第二轮 IA：memory/<x> 与 assets/<x> 都落到叶子
    expect(resolveNavKey('memory/experience', afterMigration)).toBe('memory')
    expect(resolveNavKey('assets/interactions', afterMigration)).toBe('assets')
    // 新 key 本身在树里 ⇒ 原样
    expect(resolveNavKey('memory', afterMigration)).toBe('memory')
  })

  it('新旧都不在树里 ⇒ 空串（拒绝非法 key 的语义不变）', () => {
    expect(resolveNavKey('not-a-real-panel')).toBe('')
    // 【第四轮 IA】HUB_NAV[0]（会话与配置）如今**已包含** memory ⇒ 旧 key 'experience'
    // 在其中能解析到 memory，不再是"不在树里"。为守住本用例的判据（映射目标不在
    // 给定树里 ⇒ 空串），改用一棵确实不含 memory 的子树（系统管理桶）。
    const withoutMemory: HubNavItem[] = HUB_NAV.filter((i) => i.key === 'admin')
    expect(withoutMemory.some((b) => (b.children ?? []).some((c) => c.key === 'memory'))).toBe(false)
    expect(resolveNavKey('experience', withoutMemory)).toBe('')
    expect(navKeyFromSearch('?panel=not-a-real-panel')).toBe('')
    expect(navKeyFromSearch('?panel=')).toBe('')
    expect(navKeyFromSearch('?panel=persona/nope')).toBe('')
  })
})
