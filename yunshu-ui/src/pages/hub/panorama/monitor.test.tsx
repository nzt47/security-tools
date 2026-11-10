/**
 * 系统监控 · 模块自动发现（2026-11-11）
 * ================================================================
 * 本页是 /api/modules/topology 的唯一消费方。这一版新增两件事，都要能被看见：
 *   ① 包自声明后被**自动发现**的节点（origin=discovered）—— 标「自动发现」，与手写节点同权；
 *   ② 声明了模块元数据、却没声明状态源的包 —— 「未登记」单列披露，**不生成节点、不参与健康分**。
 *
 * 【为什么打桩 fetch】两条数据源都走 getEnvelope（要求 X-Envelope: v2 + {code,data,message}），
 * 打桩 hubGet/getEnvelope 会把信封判断绕过去；这里沿用 health.test.tsx 的同一套做法。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'

import PanoramaMonitor from './monitor'

const fetchMock = vi.fn()

function fakeResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: { get: (h: string) => (h === 'X-Envelope' ? 'v2' : null) },
    json: async () => ({ code: 200, data: body, message: '' }),
  }
}

const TOPOLOGY = {
  generated_at: '2026-11-11T00:20:00',
  overall_health: 0.92,
  domains: [
    {
      domain_id: 'cognitive', domain_name: '认知层', icon: '🧠',
      nodes: [
        {
          module_id: 'planning.engine', name: '规划引擎', type: 'service',
          status: 'healthy', status_detail: '运行中', origin: 'declared',
        },
        {
          // 包自己声明（agent/workflow_learning/__init__.py 的 MODULE_STATUS_SOURCE）
          module_id: 'discovered.workflow_learning', name: '学习工作流', type: 'service',
          status: 'healthy', status_detail: '运行中', origin: 'discovered',
        },
      ],
    },
  ],
  autodiscovery: { enabled: true, scanned_packages: 51, discovered: 1, unregistered: 1 },
  unregistered: [{
    package: 'agent/example_probe/',
    name: 'example_probe',
    reason: '声明了模块元数据但未声明 MODULE_STATUS_SOURCE（无状态源 ⇒ 不生成节点、不参与健康分）',
  }],
}

const HEALTH = {
  dimensions: { l1_process: 1 },
  layers: { l1_process: { score: 1, available: true, detail: 'psutil 采样正常' } },
  issues: [],
  overall_health: 1,
}

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input: unknown) => {
    const url = String(input)
    if (url.includes('/api/modules/topology')) return fakeResponse(TOPOLOGY)
    if (url.includes('/api/health/dashboard')) return fakeResponse(HEALTH)
    throw new Error('unexpected url: ' + url)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('系统监控 · 模块自动发现与「未登记」', () => {
  it('自动发现的节点标「自动发现」，手写节点不标，两者同权计入模块总数', async () => {
    render(<PanoramaMonitor />)
    const badge = await screen.findByTestId('origin-badge-discovered.workflow_learning')
    expect(badge.textContent).toBe('自动发现')
    expect(screen.queryByTestId('origin-badge-planning.engine')).toBeNull()
    // 模块总数 = 手写 1 + 自动发现 1（未登记的包不算）；页头说清来源
    expect(screen.getByText(/1 个域 \/ 2 个模块，其中 1 个自动发现；1 个未登记/)).toBeTruthy()
  })

  it('「未登记」单列披露：包名 + 原因，且不并进模块明细（不参与健康分）', async () => {
    render(<PanoramaMonitor />)
    const card = await screen.findByTestId('module-unregistered')
    expect(within(card).getByText('未登记模块（1）')).toBeTruthy()
    expect(within(card).getByText('agent/example_probe/')).toBeTruthy()
    // 「不参与健康分」在标题说明与原因里各出现一次（两处都在披露，不是重复渲染）
    expect(within(card).getAllByText(/不参与健康分/).length).toBeGreaterThanOrEqual(1)
    // 该包只出现在「未登记」里，没有变成节点（模块明细里查不到它的 module_id）
    await waitFor(() => expect(screen.getAllByText('agent/example_probe/').length).toBe(1))
    expect(screen.queryByText('discovered.example_probe')).toBeNull()
  })

  it('开关关闭时明确说「自动发现已关闭」，不静默退回旧图', async () => {
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input)
      if (url.includes('/api/modules/topology')) {
        return fakeResponse({ ...TOPOLOGY, autodiscovery: { enabled: false }, unregistered: [] })
      }
      if (url.includes('/api/health/dashboard')) return fakeResponse(HEALTH)
      throw new Error('unexpected url: ' + url)
    })
    render(<PanoramaMonitor />)
    expect(await screen.findByTestId('autodiscovery-off')).toBeTruthy()
    expect(screen.queryByTestId('module-unregistered')).toBeNull()
  })
})
