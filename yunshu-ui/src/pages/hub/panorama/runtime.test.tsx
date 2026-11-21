/**
 * 运行监控（全景感知 + 系统拓扑）容器契约测试（2026-11-21 导航重组）
 * 真机抽查时这一页曾出现一次空白（其余合并页都正常），原因未定；
 * 这条用例把"容器本身能渲染页头 + 两个页签"钉住，避免回归。
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('@/pages/hub/panorama/sensors', () => ({
  default: ({ embedded }: { embedded?: boolean }) => <div data-testid="sensors">sensors-{String(embedded)}</div>,
}))
vi.mock('@/pages/hub/panorama/monitor', () => ({
  default: ({ embedded }: { embedded?: boolean }) => <div data-testid="monitor">monitor-{String(embedded)}</div>,
}))

import RuntimeMonitor from './runtime'

describe('运行监控容器', () => {
  it('渲染页头与两个页签，默认落在「全景感知」', () => {
    render(<RuntimeMonitor />)
    expect(screen.getByRole('heading', { name: '运行监控' })).toBeTruthy()
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['全景感知', '系统拓扑'])
    expect(tabs[0].getAttribute('aria-selected')).toBe('true')
  })

  it('嵌入的子页收到 embedded=true（由容器提供页头，不重复渲染）', () => {
    render(<RuntimeMonitor />)
    expect(screen.getByTestId('sensors').textContent).toBe('sensors-true')
  })
})
