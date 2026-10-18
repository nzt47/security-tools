/**
 * 分身行操作 —— 详情 / 热更新 / 离线包（3 条）
 * 锁：详情 GET 到 /api/subagent/<name>；热更新需确认后才 POST；离线包是下载链接。
 * 另：**不提供"执行"按钮**（后端是占位骨架，真执行走 delegate）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SubagentRowActions from './SubagentRowActions'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

describe('SubagentRowActions', () => {
  it('详情 GET /api/subagent/<name> 并上屏', async () => {
    api.hubGet.mockResolvedValue({ name: 'alice', status: 'running' })
    render(<SubagentRowActions name="alice" />)
    fireEvent.click(screen.getByRole('button', { name: '分身详情 alice' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/subagent/alice'))
    expect(await screen.findByText(/"name": "alice"/)).toBeTruthy()
  })

  it('热更新是两步：展开确认时不发请求', async () => {
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SubagentRowActions name="alice" />)
    fireEvent.click(screen.getByRole('button', { name: '热更新 alice' }))
    expect(await screen.findByText(/热更新会重载该分身的配置/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认热更新 alice' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/subagent/alice/reload', {}))
  })

  it('离线包是下载链接，不走 JSON 请求', () => {
    render(<SubagentRowActions name="alice" />)
    const link = screen.getByRole('link', { name: '下载离线包 alice' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/subagent/alice/offline_pack')
    expect(api.hubGet).not.toHaveBeenCalled()
  })

  it('没有"执行"按钮（后端是占位骨架，真执行走 delegate）', () => {
    render(<SubagentRowActions name="alice" />)
    expect(screen.queryByRole('button', { name: /执行/ })).toBeNull()
  })
})
