/**
 * 安全与权限 —— 11 条端点
 * ------------------------------------------------------------------
 * 锁：紧急控制四动作各打对应 POST（停止需二次确认）；开关切换后回读 status；
 * 文本校验 POST {text}；关键词添加；窗口授权需勾选。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SecurityPanel from './security'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/permission/status') {
      return {
        current_action: null,
        emergency: { paused: false, stopped: false, network_blocked: false },
        stats: { tools: 91, perm_checks: 0, blocked: 0, total_alerts: 0 },
        toggles: { dangerous_ops: false, file_write: true, network_access: true, sensor: true, window_monitor: false },
      }
    }
    if (url === '/api/permission/stats') return { guard_stats: { blocked: 0 }, perm_stats: { total_checks: 0 } }
    if (url.startsWith('/api/permission/log')) return { action_logs: [], perm_logs: [] }
    if (url.startsWith('/api/permission/access-log')) return { access_logs: [] }
    if (url === '/api/safety/alerts') return { alerts: [], stats: { total_alerts: 0 } }
    if (url === '/api/safety/keywords') return { keywords: { critical: [{ pattern: 'rm -rf /' }], warning: [] } }
    if (url === '/api/privacy/info') return { categories: [{ name: '硬件状态', items: ['CPU'] }] }
    return {}
  })
}

describe('SecurityPanel', () => {
  it('三面并发拉取，开关状态上屏', async () => {
    gets()
    render(<SecurityPanel />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/permission/status'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/safety/keywords'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/privacy/info'))
    expect(await screen.findByText('权限开关（切换后回读，界面只说后端真实状态）')).toBeTruthy()
  })

  it('切换开关：POST {key, enabled} 且写完回读 status', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SecurityPanel />)
    await screen.findByText('权限开关（切换后回读，界面只说后端真实状态）')
    const before = api.hubGet.mock.calls.filter((c) => c[0] === '/api/permission/status').length

    fireEvent.click(screen.getByRole('button', { name: '切换开关 file_write' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/permission/toggle', { key: 'file_write', enabled: false }))
    await waitFor(() => {
      const after = api.hubGet.mock.calls.filter((c) => c[0] === '/api/permission/status').length
      expect(after, '写完必须回读，否则界面在报告我以为的状态').toBeGreaterThan(before)
    })
  })

  it('暂停/封网/取消各打对应 action；紧急停止需二次确认', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, message: 'ok' })
    render(<SecurityPanel />)
    await screen.findByText('权限开关（切换后回读，界面只说后端真实状态）')

    fireEvent.click(screen.getByRole('button', { name: '暂停智能体' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/permission/emergency', { action: 'pause' }))

    fireEvent.click(screen.getByRole('button', { name: '封锁网络' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/permission/emergency', { action: 'network_block' }))

    fireEvent.click(screen.getByRole('button', { name: '取消当前操作' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/permission/emergency', { action: 'cancel' }))

    fireEvent.click(screen.getByRole('button', { name: '紧急停止' }))
    expect(await screen.findByText(/紧急停止会中止正在进行的操作/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalledWith('/api/permission/emergency', { action: 'stop' })
    fireEvent.click(screen.getByRole('button', { name: '确认紧急停止' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/permission/emergency', { action: 'stop' }))
  })

  it('关键词 Tab：文本校验与添加关键词', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, level: 'critical' })
    render(<SecurityPanel />)
    await screen.findByText('权限开关（切换后回读，界面只说后端真实状态）')
    fireEvent.click(screen.getByRole('button', { name: '危险关键词' }))

    fireEvent.change(screen.getByLabelText('待校验文本'), { target: { value: 'rm -rf /' } })
    fireEvent.click(screen.getByRole('button', { name: '校验危险文本' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/safety/check', { text: 'rm -rf /' }))

    fireEvent.change(screen.getByLabelText('关键词正则'), { target: { value: 'shutdown' } })
    fireEvent.change(screen.getByLabelText('关键词说明'), { target: { value: '关机' } })
    fireEvent.click(screen.getByRole('button', { name: '添加危险关键词' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/safety/keywords', {
      pattern: 'shutdown', description: '关机', level: 'warning', category: '自定义',
    }))
  })

  it('隐私 Tab：授权需勾选才可提交', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, consent: true })
    render(<SecurityPanel />)
    await screen.findByText('权限开关（切换后回读，界面只说后端真实状态）')
    fireEvent.click(screen.getByRole('button', { name: '隐私' }))

    const btn = screen.getByRole('button', { name: '授权窗口活动' }) as HTMLButtonElement
    expect(btn.disabled, '窗口活动属于需明确同意的数据，未勾选不该能提交').toBe(true)
    fireEvent.click(screen.getByLabelText('确认窗口授权'))
    fireEvent.click(btn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/window/consent', { consent: true }))
  })
})
