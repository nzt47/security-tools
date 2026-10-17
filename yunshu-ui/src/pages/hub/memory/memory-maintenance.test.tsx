/**
 * 记忆运维 —— 审查/压缩/长期摘要/窗口监控/向量维护（14 条端点）
 * ------------------------------------------------------------------
 * 重点锁「改写前必须确认」这一层：压缩会改来源数据，清空摘要/窗口事件/向量库
 * 都是不可撤销的整体动作，长期摘要还是**盲写**（后端没有读接口）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), hubPut: vi.fn(), hubDelete: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
  hubPut: api.hubPut,
  hubDelete: api.hubDelete,
}))

import MemoryMaintenance from './memory-maintenance'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.hubPut.mockReset()
  api.hubDelete.mockReset()
})

function baseGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/memory/review') {
      return { last_review: null, stats: { total_entries: 4, verified_entries: 3, unverified_entries: 1, sensitive_entries: 0, high_importance_entries: 1, embedding_entries: 4, vec_table: 'ltm_vec_index' } }
    }
    if (url === '/api/memory/windows/current') return { process: 'Code.exe', title: 'vscode', elapsed_sec: 12, is_idle: false }
    if (url === '/api/memory/windows/stats') return { apps: [{ app: 'Code.exe', duration_sec: 30 }], total_switches: 5, total_duration_sec: 40 }
    if (url.startsWith('/api/memory/windows/events')) return { events: [{ ts: 1, to_app: 'Code.exe' }] }
    if (url === '/api/memory/windows/config') return { enabled: true, idle_timeout_sec: 300, max_events: 500, poll_interval_sec: 0.5, ignore_processes: ['explorer.exe'] }
    if (url.startsWith('/api/vector/recent')) return { items: [{ id: 'mem_1', content: '一条记忆', metadata: { type: 'conversation' } }] }
    if (url.startsWith('/api/vector/item/')) return { id: 'mem_9', content: '详情' }
    return { ok: true }
  })
}

describe('MemoryMaintenance', () => {
  it('打开即拉 审查 / 窗口 / 最近向量', async () => {
    baseGets()
    render(<MemoryMaintenance />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/memory/review'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/memory/windows/current'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/vector/recent?limit=20'))
    expect(await screen.findByText('mem_1')).toBeTruthy()
    expect(screen.getByText(/vscode/)).toBeTruthy()
  })

  it('执行审查与触发压缩各自打 POST', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    fireEvent.click(screen.getByRole('button', { name: '执行记忆审查' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/memory/review', {}))

    fireEvent.click(screen.getByRole('button', { name: '触发记忆压缩' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/memory/compress', {}))
  })

  it('长期摘要是盲写：未确认时禁用，确认后才 PUT', async () => {
    baseGets()
    api.hubPut.mockResolvedValue({ ok: true })
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    fireEvent.change(screen.getByLabelText('长期摘要内容'), { target: { value: '新的摘要' } })
    const save = screen.getByRole('button', { name: '保存长期摘要' }) as HTMLButtonElement
    expect(save.disabled, '没有确认就允许覆盖现有摘要，是不可恢复的误操作').toBe(true)

    fireEvent.click(screen.getByLabelText('确认覆盖摘要'))
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await waitFor(() => expect(api.hubPut).toHaveBeenCalledWith('/api/memory/summary', { summary: '新的摘要' }))
  })

  it('清空摘要需勾确认', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    const clear = screen.getByRole('button', { name: '清空长期摘要' }) as HTMLButtonElement
    expect(clear.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('确认清空摘要'))
    fireEvent.click(clear)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/memory/clear-summary', {}))
  })

  it('窗口配置：忽略进程按逗号拆成数组后提交', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    fireEvent.change(screen.getByLabelText('忽略进程列表'), { target: { value: 'explorer.exe, chrome.exe' } })
    fireEvent.click(screen.getByRole('button', { name: '保存窗口配置' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/memory/windows/config', {
      enabled: true,
      idle_timeout_sec: 300,
      max_events: 500,
      poll_interval_sec: 0.5,
      ignore_processes: ['explorer.exe', 'chrome.exe'],
    }))
  })

  it('向量批量添加：坏 JSON 不发请求', async () => {
    baseGets()
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    fireEvent.change(screen.getByLabelText('批量向量记忆 JSON'), { target: { value: '{oops' } })
    fireEvent.click(screen.getByRole('button', { name: '批量添加向量记忆' }))
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('清空向量库走 DELETE，且需勾确认', async () => {
    baseGets()
    api.hubDelete.mockResolvedValue({ ok: true })
    render(<MemoryMaintenance />)
    await screen.findByText('mem_1')

    const clear = screen.getByRole('button', { name: '清空向量库' }) as HTMLButtonElement
    expect(clear.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('确认清空向量库'))
    fireEvent.click(clear)
    await waitFor(() => expect(api.hubDelete).toHaveBeenCalledWith('/api/vector/clear'))
  })
})
