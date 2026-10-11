/**
 * ContentPanel 导航渲染回归测试 —— 缺陷 ②
 * ------------------------------------------------
 * 背景：多个菜单共用同一页面组件时，若渲染不携带 key={activeKey} 且不传参数，
 * 点不同子菜单会因组件实例复用而渲染成相同内容（内部 state 停留在上次参数）。
 * 本测试用"挂载时读取 props 存为 state"的探针组件验证：
 *   1. 跨导航切换（同组件）会强制重挂载（key），state 按新参数初始化；
 *   2. derivePanelParams 推导的参数被透传给复用组件。
 *
 * 【第二轮 IA 后的覆盖点】assets 与 memory 已收敛为叶子（子项在页内 Tab / 分类切换），
 * 不再由导航 key 推导 initialCategory / mode；仍由导航 key 参数化的是 governance/<panel>。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { act, render, screen } from '@testing-library/react'
import { ContentPanel } from './ContentPanel'
import { useWorkbenchNav } from '../../../workbench/navStore'

// 导航项注册表：替换真实 hubNav 的 findNavItem（其余导出如 derivePanelParams /
// DEFAULT_NAV_KEY 等保持真实），避免引入真实页面（懒加载 + 网络请求）。
const registry = vi.hoisted(() => new Map<string, { component: unknown }>())

vi.mock('../../../workbench/hubNav', async (importActual) => {
  const actual = await importActual<typeof import('../../../workbench/hubNav')>()
  return {
    ...actual,
    findNavItem: (key: string) =>
      registry.get(key) as ReturnType<typeof actual.findNavItem> | undefined,
  }
})

/** 治理面板探针：panel 只在挂载时读入 state —— 未重挂载则切换后残留旧面板 */
function ProbeGovernance({ panel = '(none)' }: { panel?: string }) {
  const [p] = useState(panel)
  return <div data-testid="probe-gov">{p}</div>
}

/** 资产页探针：叶子项不再传 initialCategory，默认值即证据 */
function ProbeAsset({ initialCategory = '(none)' }: { initialCategory?: string }) {
  const [cat] = useState(initialCategory)
  return <div data-testid="probe-asset">{cat}</div>
}

/** 记忆页探针：叶子项不再传 mode */
function ProbeMemory({ mode = '(none)' }: { mode?: string }) {
  const [m] = useState(mode)
  return <div data-testid="probe-memory">{m}</div>
}

beforeEach(() => {
  registry.clear()
  registry.set('governance/approvals', { component: ProbeGovernance })
  registry.set('governance/settings', { component: ProbeGovernance })
  registry.set('assets', { component: ProbeAsset })
  registry.set('memory', { component: ProbeMemory })
  // 未注册的 key → findNavItem 返回空 → ContentPanel 渲染空态提示
  useWorkbenchNav.setState({ activeKey: 'session' })
})

afterEach(() => {
  useWorkbenchNav.setState({ activeKey: 'session' })
})

describe('ContentPanel 复用组件参数化（缺陷②）', () => {
  it('governance 子面板切换：跨导航重挂载且按 key 传入 panel', async () => {
    act(() => useWorkbenchNav.getState().setActiveKey('governance/approvals'))
    render(<ContentPanel />)
    expect(await screen.findByTestId('probe-gov')).toHaveTextContent('approvals')

    act(() => useWorkbenchNav.getState().setActiveKey('governance/settings'))
    // 缺陷 ② 复现点：若未重挂载，此处会残留 'approvals'
    expect(screen.getByTestId('probe-gov')).toHaveTextContent('settings')

    act(() => useWorkbenchNav.getState().setActiveKey('governance/approvals'))
    expect(screen.getByTestId('probe-gov')).toHaveTextContent('approvals')
  })

  it('assets / memory 已是叶子项：不再透传 initialCategory / mode', async () => {
    act(() => useWorkbenchNav.getState().setActiveKey('assets'))
    render(<ContentPanel />)
    expect(await screen.findByTestId('probe-asset')).toHaveTextContent('(none)')

    act(() => useWorkbenchNav.getState().setActiveKey('memory'))
    expect(screen.getByTestId('probe-memory')).toHaveTextContent('(none)')
  })

  it('未注册导航 key：渲染空态提示（不白屏）', () => {
    render(<ContentPanel />)
    expect(screen.getByText('请从左侧导航选择功能')).toBeInTheDocument()
  })
})
