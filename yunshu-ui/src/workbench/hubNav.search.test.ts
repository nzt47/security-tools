/**
 * 导航检索 + 深链 单元测试（2026-09-22）
 * ------------------------------------------------
 * 覆盖三件本次新增的能力，以及一件被修的信息架构缺陷：
 *   1. `filterNav`：侧栏搜索框的唯一实现（含"命中分组整组保留"这条易被改错的规则）
 *   2. `navKeyFromSearch` / `searchForNavKey`：深链的读与写（**必须校验合法性**）
 *   3. 「治理面板」的层级位置：本次上移是为了"滚不到就找不到"这个实测问题不回归
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

    for (const child of ['governance/pipeline', 'governance/approvals', 'governance/settings']) {
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

describe('信息架构：「治理面板」必须一屏可达', () => {
  it('紧跟在「全景看板」之后（本次上移的正面判据）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)

    expect(topKeys.indexOf('governance')).toBe(topKeys.indexOf('panorama') + 1)
  })

  it('不再排在「系统组件」等之后（侧栏靠底 ⇒ 滚不到 ⇒ 找不到）', () => {
    const topKeys = HUB_NAV.map((i) => i.key)

    expect(topKeys.indexOf('governance')).toBeLessThan(topKeys.indexOf('components'))
    // 顶层前四项目前为：会话任务 → 提示词实验室 → 技能中心 → 工具调用（既有口径，未动）
    expect(topKeys.slice(0, 4)).toEqual(['session', 'prompt-lab', 'skills-center', 'tools'])
  })

  it('「审批收件箱」仍是治理面板下的子项（入口没被搬走）', () => {
    const gov = HUB_NAV.find((i) => i.key === 'governance')
    const childKeys = (gov?.children ?? []).map((c) => c.key)

    expect(childKeys).toContain('governance/approvals')
    expect(childKeys[0]).toBe('governance/pipeline')
    expect(childKeys[1]).toBe('governance/approvals')
  })
})
