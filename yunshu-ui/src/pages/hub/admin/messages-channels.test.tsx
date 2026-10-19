/**
 * 消息中心 · 消息通道面板
 * ------------------------------------------------------------------
 * 锁三件事：
 *   1. 接线状态必须**上屏**：告警（触发/恢复）会经已启用通道发送摘要，
 *      而 `/channels/send` 只供手动调试 —— 两句话都不能少；
 *   2. 内置模板来自 /api/extensions/discover，安装走 POST /api/extensions/install
 *      {type:'channel', source:id}；
 *   3. 手动发送的按钮门禁（缺 id/内容不可点），提交 /api/extensions/channels/send。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import HubChannelsPanel from './messages-channels'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function setup() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/extensions/discover') {
      return {
        ok: true,
        builtin_channels: [
          { id: 'webhook', name: 'Webhook 通道', description: '发到外部服务', installed: false },
          { id: 'email_smtp', name: '邮件通道 (SMTP)', description: 'SMTP 发信', installed: true },
        ],
      }
    }
    if (url === '/api/extensions/list') {
      return {
        ok: true,
        extensions: [
          { ext_id: 'email_smtp', ext_type: 'channel', name: '邮件通道', enabled: false },
          { ext_id: 'self_reflection', ext_type: 'skill', name: '自省反思', enabled: true },
        ],
      }
    }
    return { ok: true }
  })
}

describe('HubChannelsPanel', () => {
  it('内置模板上屏；接线状态提示必须可见；已装通道按启用态展示', async () => {
    setup()
    render(<HubChannelsPanel />)

    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/discover'))
    expect(await screen.findByText('Webhook 通道')).toBeTruthy()
    // 接线状态：说清"什么事件会发、发到哪里、send 端点仍只供调试"
    expect(screen.getByText(/已接线/)).toBeTruthy()
    expect(screen.getByText(/监控告警（触发 \/ 恢复）/)).toBeTruthy()
    expect(screen.getByText(/只供本页「手动发送（调试）」/)).toBeTruthy()
    // 已装通道来自 /api/extensions/list 且只取 ext_type=channel
    expect(screen.getByText(/email_smtp\(停用\)/)).toBeTruthy()
    expect(screen.queryByText(/self_reflection/)).toBeNull()
    // 已装的不给安装按钮；未装的给
    expect(screen.queryByRole('button', { name: '安装通道 email_smtp' })).toBeNull()
    expect(screen.getByRole('button', { name: '安装通道 webhook' })).toBeTruthy()
  })

  it('安装内置通道：POST {type:channel, source:id}，装完重拉', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true, message: '已安装' })
    render(<HubChannelsPanel />)
    await screen.findByText('Webhook 通道')

    const before = api.hubGet.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '安装通道 webhook' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/install', {
      type: 'channel', source: 'webhook',
    }))
    await waitFor(() => expect(api.hubGet.mock.calls.length).toBeGreaterThan(before))
    expect(await screen.findByText(/已安装通道 webhook/)).toBeTruthy()
  })

  it('手动发送：缺 id 或内容时按钮不可用；填齐后提交 channel_id + message', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true, message: 'ok' })
    render(<HubChannelsPanel />)
    await screen.findByText('Webhook 通道')

    const btn = screen.getByRole('button', { name: '发送通道消息' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('通道 id'), { target: { value: 'webhook' } })
    expect(btn.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('通道消息内容'), { target: { value: 'hello' } })
    expect(btn.disabled).toBe(false)

    fireEvent.click(btn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/channels/send', {
      channel_id: 'webhook', message: 'hello',
    }))
  })
})
