/**
 * 模块节点详情 / 干预 —— 2 条
 * 锁：详情 GET；动作清单来自后端；执行动作必须两步确认且带 reason 与空的 params。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import ModuleDetail from './ModuleDetail'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function detail() {
  api.hubGet.mockResolvedValue({
    module_id: 'service.app',
    name: '应用服务',
    status: 'healthy',
    metrics: { latency_ms: 12 },
    actions: [
      { action: 'restart', label: '重启', danger: true, description: '重启应用服务' },
      { action: 'probe', label: '探活' },
    ],
    recent_actions: [],
  })
}

describe('ModuleDetail', () => {
  it('详情 GET /api/modules/<id>/detail，动作清单来自后端', async () => {
    detail()
    render(<ModuleDetail moduleId="service.app" onClose={() => {}} />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/modules/service.app/detail'))
    expect(await screen.findByRole('button', { name: '执行动作 restart' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '执行动作 probe' })).toBeTruthy()
  })

  it('执行动作两步：展开确认时不发请求；确认时带 action/reason/params', async () => {
    detail()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ModuleDetail moduleId="service.app" onClose={() => {}} />)
    await screen.findByRole('button', { name: '执行动作 restart' })

    fireEvent.click(screen.getByRole('button', { name: '执行动作 restart' }))
    expect(await screen.findByText(/会写入审计/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('动作原因'), { target: { value: '内存回落' } })
    fireEvent.click(screen.getByRole('button', { name: '确认执行动作 restart' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/modules/service.app/actions', {
      action: 'restart', reason: '内存回落', params: {},
    }))
  })

  it('节点无可执行动作时如实说明', async () => {
    api.hubGet.mockResolvedValue({ module_id: 'x', name: 'X', actions: [] })
    render(<ModuleDetail moduleId="x" onClose={() => {}} />)
    expect(await screen.findByText(/该节点未声明可执行动作/)).toBeTruthy()
  })
})
