/**
 * 回放中心 —— 5 条端点
 * 锁：列表+统计并发；元数据与数据分两步；清理默认 dry_run，真删需勾确认。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import ReplayCenter from './index'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/replay/list')) {
      return { ok: true, total: 1, records: [{ replay_id: 'r1', url: '/chat', created_at: '2026-10-18 05:00', size_bytes: 2048 }] }
    }
    if (url === '/api/replay/stats') return { ok: true, stats: { window_hours: 24, fully_correlated: 0, with_trace_id: 1 } }
    if (url === '/api/replay/r1') return { replay_id: 'r1', events: 12 }
    if (url === '/api/replay/r1/data') return { replay_id: 'r1', data: '[]' }
    return {}
  })
}

describe('ReplayCenter', () => {
  it('列表与统计并发拉取', async () => {
    gets()
    render(<ReplayCenter />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/replay/list?limit=20'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/replay/stats'))
    expect(await screen.findByText('r1')).toBeTruthy()
    expect(screen.getByText('2 KB')).toBeTruthy()
  })

  it('元数据与数据分两步拉（数据可能很大，不随列表一起取）', async () => {
    gets()
    render(<ReplayCenter />)
    await screen.findByText('r1')

    fireEvent.click(screen.getByRole('button', { name: '回放元数据 r1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/replay/r1'))

    fireEvent.click(screen.getByRole('button', { name: '回放数据 r1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/replay/r1/data'))
  })

  it('清理：预览走 dry_run=true；真删需勾确认并走 false', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, deleted: ['r0'] })
    render(<ReplayCenter />)
    await screen.findByText('r1')

    fireEvent.click(screen.getByRole('button', { name: '预览清理回放' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/replay/cleanup', { dry_run: true }))

    const real = screen.getByRole('button', { name: '清理回放' }) as HTMLButtonElement
    expect(real.disabled, '物理删除排障现场，必须先确认').toBe(true)
    fireEvent.click(screen.getByLabelText('确认清理回放'))
    fireEvent.click(real)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/replay/cleanup', { dry_run: false }))
  })
})
