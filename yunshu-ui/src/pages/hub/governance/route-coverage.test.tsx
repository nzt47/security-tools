/**
 * 路由覆盖面板 ·「该进 UI / 不该进」意图标注（方案 §7）前端测试
 * ------------------------------------------------------------------
 * 锁死：产物里的 intent / ui_pending / pending_ui 真的被上屏，
 * 且「该进却未进」可一键过滤 —— 删掉展示或过滤即红。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const requestMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/apiClient', () => ({ request: requestMock }))

import RouteCoveragePanel from './route-coverage'

const PAYLOAD = {
  generated_at: '2026-10-11T00:00:00',
  totals: {
    routes: 3, live: 3, dead_copy: 0,
    by_intent: { should_be_in_ui: 1, should_not_be_in_ui: 1, unclassified: 1 },
  },
  frontend_endpoints: 1,
  frontend_unmatched: [],
  pending_ui: [{ norm: '/api/x', path: '/api/x' }],
  entries: [
    { path: '/api/x', norm: '/api/x', methods: ['GET'], category: 'unreferenced', live: true, ui_refs: [], non_ui_refs: [], where: [], intent: 'should_be_in_ui', ui_pending: true },
    { path: '/api/y', norm: '/api/y', methods: ['GET'], category: 'ui', live: true, ui_refs: ['a.ts'], non_ui_refs: [], where: [], intent: 'unclassified', ui_pending: false },
    { path: '/api/z', norm: '/api/z', methods: ['POST'], category: 'unreferenced', live: true, ui_refs: [], non_ui_refs: [], where: [], intent: 'should_not_be_in_ui', ui_pending: false },
  ],
}

afterEach(() => {
  cleanup()
  requestMock.mockReset()
})

describe('RouteCoveragePanel · 意图标注', () => {
  it('上屏 pending 计数 + 意图列（含「待办」标记）', async () => {
    requestMock.mockResolvedValue(PAYLOAD)
    render(<RouteCoveragePanel />)

    const filter = await screen.findByTestId('route-pending-filter')
    expect(filter.textContent).toContain('该进却未进')
    expect(filter.textContent).toContain('1')

    expect(screen.getByTestId('route-intent-/api/x').textContent).toContain('该进 UI')
    expect(screen.getByTestId('route-intent-/api/x').textContent).toContain('待办')
    expect(screen.getByTestId('route-intent-/api/y').textContent).toContain('未标注')
    expect(screen.getByTestId('route-intent-/api/z').textContent).toContain('不该进')
  })

  it('「该进却未进」过滤只留 ui_pending 行', async () => {
    requestMock.mockResolvedValue(PAYLOAD)
    render(<RouteCoveragePanel />)
    await screen.findByTestId('route-pending-filter')

    fireEvent.click(screen.getByTestId('route-pending-filter'))

    expect(screen.getByText('/api/x')).toBeTruthy()
    expect(screen.queryByText('/api/y')).toBeNull()
    expect(screen.queryByText('/api/z')).toBeNull()
  })
})
