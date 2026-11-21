/**
 * NavPanel 搜索框交互测试（2026-09-22）
 * ------------------------------------------------
 * 为什么要有这一层：`filterNav` 的纯函数测试只能证明"过滤逻辑对"，
 * 证不了"搜索框真的接上了、清了、点得动"。而本次要修的正是一个**界面可达性**
 * 问题（操作者滚不到「治理面板」⇒ 找不到「审批收件箱」⇒ 审批单据无人裁决），
 * 所以必须有一条"输入关键词 → 目标项出现在屏幕上"的端到端断言。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { NavPanel } from './NavPanel'
import { useWorkbenchNav } from '../../../workbench/navStore'
import { DEFAULT_NAV_KEY } from '../../../workbench/hubNav'

beforeEach(() => {
  // 导航选中项是全局 store；不重置会让用例互相污染（上一条点了别的项）
  useWorkbenchNav.setState({ activeKey: DEFAULT_NAV_KEY })
})

afterEach(() => {
  cleanup()
})

function searchBox(): HTMLInputElement {
  return screen.getByLabelText('搜索功能') as HTMLInputElement
}

describe('NavPanel 搜索框', () => {
  it('默认渲染搜索框，且「操作（可改）/ 审批收件箱」都在首屏侧栏里', () => {
    render(<NavPanel />)

    expect(searchBox()).toBeTruthy()
    // 【2026-11-21 重组】桶名改为能力轴：治理与运营 → 操作（可改）
    expect(screen.getByText('操作（可改）')).toBeTruthy()
    // 顶层分组默认展开（depth <= 1）⇒ 子项本来就在 DOM 里；
    // 真正的障碍是分组原先排在靠底、要滚动才看得见（见 hubNav.search.test.ts 的层级断言）
    expect(screen.getByText('审批收件箱')).toBeTruthy()
  })

  it('输入「审批」⇒ 直达「审批收件箱」（本次要修的正是这条路径）', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '审批' } })

    expect(screen.getByText('审批收件箱')).toBeTruthy()
    // 搜索态强制展开分组 ⇒ 命中项一定在屏幕上，而不是藏在折叠里
    expect(screen.queryByText('记忆管理')).toBeNull()
  })

  it('输入小节名「治理」⇒ 整块展开（不必先知道子项叫什么）', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '治理' } })

    // 【2026-11-21 重组】「治理」既是观测桶下的小节名，也是「治理看板」叶子的名字
    expect(screen.getByText('治理看板')).toBeTruthy()
    expect(screen.getByText('业务看板')).toBeTruthy()
    // 操作桶里的东西不属于"治理"这一块（审批收件箱在操作桶）
    expect(screen.queryByText('审批收件箱')).toBeNull()
  })

  it('输入「观测」⇒ 整桶展开，且首屏就能看到合并后的「运行监控 / 日志与审计」', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '观测' } })

    expect(screen.getByText('运行监控')).toBeTruthy()
    expect(screen.getByText('日志与审计')).toBeTruthy()
    expect(screen.getByText('系统总览')).toBeTruthy()
  })

  it('旧的页内 Tab 名（审计导出 / 开关中心）不再作为叶子出现 —— 它们已是页内 Tab', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '审计' } })

    // 只剩合并后的叶子「日志与审计」；点进去在页内切到「审计链」Tab
    expect(screen.getByText('日志与审计')).toBeTruthy()
    expect(screen.queryByText('审计导出')).toBeNull()
    expect(screen.queryByText('开关中心')).toBeNull()
  })

  it('无命中 ⇒ 显示"没有匹配"提示（而不是留一片空白侧栏）', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '不存在的能力zzz' } })

    expect(screen.getByText(/没有匹配/)).toBeTruthy()
  })

  it('清空按钮恢复完整导航树', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '审批' } })
    expect(screen.queryByText('记忆管理')).toBeNull()

    fireEvent.click(screen.getByLabelText('清空搜索'))

    expect(searchBox().value).toBe('')
    expect(screen.getByText('记忆管理')).toBeTruthy()
  })

  it('Esc 也能清空（键盘可达）', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '审批' } })
    fireEvent.keyDown(searchBox(), { key: 'Escape' })

    expect(searchBox().value).toBe('')
  })

  it('点击搜索出来的「审批收件箱」⇒ 真的切换到该导航项', () => {
    render(<NavPanel />)

    fireEvent.change(searchBox(), { target: { value: '审批' } })
    fireEvent.click(screen.getByText('审批收件箱'))

    expect(useWorkbenchNav.getState().activeKey).toBe('governance/approvals')
  })
})
