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
    // 已装通道来自 /api/extensions/list 且只取 ext_type=channel（现在按行展示 + 三个动作）
    // 内置模板行与已装行都会出现 email_smtp ⇒ 用动作按钮定位"已装行"
    expect(screen.getByRole('button', { name: '配置通道 email_smtp' })).toBeTruthy()
    expect(screen.getByText('停用')).toBeTruthy()
    expect(screen.getByRole('button', { name: '配置通道 email_smtp' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '切换通道 email_smtp' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '卸载通道 email_smtp' })).toBeTruthy()
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

  // ── 事件出站开关（#1221）：就地切换，写的是开关中心同一个 POST 端点 ──

  it('事件出站：勾选态来自后端登记表；切换即 POST /api/cp/settings/<key> {value}', async () => {
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/cp/settings') {
        return {
          ok: true,
          items: [
            { key: 'CP_CHANNEL_EVENTS', value: true, editable: true },
            { key: 'CP_CHANNEL_EVENT_ALERTS', value: false, editable: true },
            { key: 'CP_CHANNEL_EVENT_TASK_FAILED', value: true, editable: true },
            { key: 'CP_CHANNEL_EVENT_TASK_COMPLETED', value: true, editable: true },
          ],
        }
      }
      if (url === '/api/extensions/discover') return { ok: true, builtin_channels: [] }
      if (url === '/api/extensions/list') return { ok: true, extensions: [] }
      return { ok: true }
    })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<HubChannelsPanel />)

    const alerts = (await screen.findByLabelText('事件出站 CP_CHANNEL_EVENT_ALERTS')) as HTMLInputElement
    // 勾选态必须来自后端值（不是前端默认 true）
    expect(alerts.checked).toBe(false)
    expect((screen.getByLabelText('事件出站 CP_CHANNEL_EVENTS') as HTMLInputElement).checked).toBe(true)

    fireEvent.click(alerts)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/cp/settings/CP_CHANNEL_EVENT_ALERTS', { value: true },
    ))
  })

  it('事件出站：被环境变量锁定的项置灰，并把原因显示出来', async () => {
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/cp/settings') {
        return {
          ok: true,
          items: [
            { key: 'CP_CHANNEL_EVENTS', value: true, editable: false, locked_reason: '被环境变量锁定（CP_CHANNEL_EVENTS）' },
          ],
        }
      }
      if (url === '/api/extensions/discover') return { ok: true, builtin_channels: [] }
      if (url === '/api/extensions/list') return { ok: true, extensions: [] }
      return { ok: true }
    })
    render(<HubChannelsPanel />)

    const master = (await screen.findByLabelText('事件出站 CP_CHANNEL_EVENTS')) as HTMLInputElement
    expect(master.disabled).toBe(true)
    expect(screen.getByText(/被环境变量锁定/)).toBeTruthy()
  })

  // ── 已装通道自管（#1226）：配置 / 启停 / 卸载 从扩展市场搬进本页 ──

  it('已装通道配置：坏 JSON 不发请求；好 JSON 提交 configure', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<HubChannelsPanel />)
    await screen.findByRole('button', { name: '配置通道 email_smtp' })

    fireEvent.click(screen.getByRole('button', { name: '配置通道 email_smtp' }))
    const area = screen.getByLabelText('通道配置 email_smtp') as HTMLTextAreaElement

    fireEvent.change(area, { target: { value: '{not json' } })
    fireEvent.click(screen.getByRole('button', { name: '保存通道配置 email_smtp' }))
    expect(api.hubPost, '坏 JSON 不该提交').not.toHaveBeenCalled()
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()

    fireEvent.change(area, { target: { value: '{"url":"https://example.com/hook"}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存通道配置 email_smtp' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/configure', {
      type: 'channel', id: 'email_smtp', config: { url: 'https://example.com/hook' },
    }))
  })

  it('已装通道启停带下一态；卸载是两步、force=true 只在确认后传', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<HubChannelsPanel />)
    await screen.findByRole('button', { name: '切换通道 email_smtp' })

    fireEvent.click(screen.getByRole('button', { name: '切换通道 email_smtp' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/toggle', {
      type: 'channel', id: 'email_smtp', enabled: true,
    }))

    fireEvent.click(screen.getByRole('button', { name: '卸载通道 email_smtp' }))
    expect(await screen.findByText(/不可逆删除/)).toBeTruthy()
    expect(api.hubPost.mock.calls.some(([u]) => u === '/api/extensions/uninstall')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '确认卸载通道 email_smtp' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/uninstall', {
      type: 'channel', id: 'email_smtp', force: true,
    }))
  })
})
