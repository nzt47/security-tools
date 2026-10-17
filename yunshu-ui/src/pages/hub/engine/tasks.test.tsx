/**
 * 任务队列 —— 7 条端点
 * 锁：两族任务并发拉取；详情/结果按族打对应端点；取消需确认且只对可取消状态开放。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import TaskQueue from './tasks'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/background/tasks?')) {
      return {
        ok: true, total: 2, running: 1, active: 1,
        tasks: [
          { task_id: 'bg1', name: '导出', status: 'running', created_at: '2026-10-18 05:00' },
          { task_id: 'bg2', name: '归档', status: 'completed', created_at: '2026-10-18 04:00' },
        ],
      }
    }
    if (url.startsWith('/api/tasks?')) {
      return { ok: true, total: 1, tasks: [{ id: 'at1', name: '蒸馏', status: 'pending' }] }
    }
    if (url === '/api/background/tasks/bg1') return { task_id: 'bg1', status: 'running', steps: [] }
    if (url === '/api/background/tasks/bg1/result') return { task_id: 'bg1', status: 'running', message: '进行中' }
    if (url === '/api/tasks/at1') return { id: 'at1', status: 'pending' }
    return {}
  })
}

describe('TaskQueue', () => {
  it('两族并发拉取；已完成的后台任务不给取消', async () => {
    gets()
    render(<TaskQueue />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/background/tasks?limit=50'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/tasks?limit=50'))
    expect(await screen.findByText('bg1')).toBeTruthy()
    expect((screen.getByRole('button', { name: '取消任务 bg2' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('详情与结果分别打 background tasks/<id> 与 /result', async () => {
    gets()
    render(<TaskQueue />)
    await screen.findByText('bg1')

    fireEvent.click(screen.getByRole('button', { name: '任务详情 bg1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/background/tasks/bg1'))

    fireEvent.click(screen.getByRole('button', { name: '任务结果 bg1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/background/tasks/bg1/result'))
  })

  it('取消是两步：展开确认时不发请求', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<TaskQueue />)
    await screen.findByText('bg1')

    fireEvent.click(screen.getByRole('button', { name: '取消任务 bg1' }))
    expect(await screen.findByText(/可能留下半成品/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认取消 bg1' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/background/tasks/bg1/cancel', {}))
  })

  it('异步任务详情走 /api/tasks/<id>', async () => {
    gets()
    render(<TaskQueue />)
    await screen.findByText('at1')
    fireEvent.click(screen.getByRole('button', { name: '任务详情 at1' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/tasks/at1'))
  })
})
