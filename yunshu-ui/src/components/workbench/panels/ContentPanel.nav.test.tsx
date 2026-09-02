/**
 * ContentPanel 导航渲染回归测试 —— 缺陷 ②
 * ------------------------------------------------
 * 背景：assets 组 8 个菜单共用 AssetsPage、memory/manual|auto 共用 MemoryPage。
 * 若渲染不携带 key={activeKey} 且不传参数，点不同子菜单会因组件实例复用而渲染成
 * 相同内容（内部 state 停留在上次分类/模式）。
 * 本测试用"挂载时读取 props 存为 state"的探针组件验证：
 *   1. 跨导航切换（同组件）会强制重挂载（key），state 按新 initialCategory/mode 初始化；
 *   2. derivePanelParams 推导的参数被透传给复用组件。
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

/** 资产页探针：initialCategory 只在挂载时读入 state —— 未重挂载则切换后残留旧分类 */
function ProbeAsset({ initialCategory = '(none)' }: { initialCategory?: string }) {
  const [cat] = useState(initialCategory)
  return <div data-testid="probe-asset">{cat}</div>
}

/** 记忆页探针：mode 只在挂载时读入 state —— 未重挂载则切换后残留旧模式 */
function ProbeMemory({ mode }: { mode: 'manual' | 'auto' }) {
  const [m] = useState(mode)
  return <div data-testid="probe-memory">{m}</div>
}

beforeEach(() => {
  registry.clear()
  registry.set('assets/memory', { component: ProbeAsset })
  registry.set('assets/prompts', { component: ProbeAsset })
  registry.set('memory/manual', { component: ProbeMemory })
  registry.set('memory/auto', { component: ProbeMemory })
  // 未注册的 key → findNavItem 返回空 → ContentPanel 渲染空态提示
  useWorkbenchNav.setState({ activeKey: 'session' })
})

afterEach(() => {
  useWorkbenchNav.setState({ activeKey: 'session' })
})

describe('ContentPanel 复用组件参数化（缺陷②）', () => {
  it('assets 子菜单切换：跨导航重挂载且按 key 传入 initialCategory', async () => {
    act(() => useWorkbenchNav.getState().setActiveKey('assets/memory'))
    render(<ContentPanel />)
    expect(await screen.findByTestId('probe-asset')).toHaveTextContent('memory')

    // 切到 assets/prompts：同组件类型，靠 key={activeKey} 重挂载 + initialCategory='prompts'
    act(() => useWorkbenchNav.getState().setActiveKey('assets/prompts'))
    // 缺陷 ② 复现点：若未重挂载，此处会残留 'memory'
    expect(screen.getByTestId('probe-asset')).toHaveTextContent('prompts')

    // 再切回 assets/memory：state 须重新初始化为 'memory'
    act(() => useWorkbenchNav.getState().setActiveKey('assets/memory'))
    expect(screen.getByTestId('probe-asset')).toHaveTextContent('memory')
  })

  it('记忆子菜单切换：mode 随导航 key 变化并重挂载', async () => {
    act(() => useWorkbenchNav.getState().setActiveKey('memory/manual'))
    render(<ContentPanel />)
    expect(await screen.findByTestId('probe-memory')).toHaveTextContent('manual')

    act(() => useWorkbenchNav.getState().setActiveKey('memory/auto'))
    // 缺陷 ② 复现点：若未重挂载，此处会残留 'manual'
    expect(screen.getByTestId('probe-memory')).toHaveTextContent('auto')
  })

  it('未注册导航 key：渲染空态提示（不白屏）', () => {
    render(<ContentPanel />)
    expect(screen.getByText('请从左侧导航选择功能')).toBeInTheDocument()
  })
})
