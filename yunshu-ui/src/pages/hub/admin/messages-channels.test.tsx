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
    // 【2026-11-13 合并】内置模板与已装通道合成一列：同一个 id 只出现一行，
    // 未装的给「安装」、装了的给「配置/启停/卸载」；状态徽标只有一处（不再两张卡片各说一遍）。
    expect(screen.queryByText(/内置通道模板/)).toBeNull()
    expect(screen.queryByText(/已装通道（/)).toBeNull()
    expect(screen.getByText(/通道（2 个 · 已装 1 · 启用 0）/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '配置通道 email_smtp' })).toBeTruthy()
    expect(screen.getByText('已装（未启用）')).toBeTruthy()
    const toggle = screen.getByRole('button', { name: '切换通道 email_smtp' })
    expect(toggle.textContent).toContain('启用')
    // 停用态下「启用」= 下一步该点的动作 ⇒ 必须高亮（Owner 2026-11-13）
    expect(toggle.className).toContain('bg-emerald-500/10')
    expect(toggle.className).toContain('text-emerald-300')
    expect(screen.getByRole('button', { name: '安装通道 webhook' }).className)
      .toContain('bg-emerald-500/10')
    expect(screen.getByRole('button', { name: '卸载通道 email_smtp' }).className)
      .not.toContain('bg-emerald-500/10')
    expect(screen.getByText('未安装')).toBeTruthy()
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

  it('测试发送挂在通道那一行：启用才可输入，缺内容时按钮不可用；提交的 channel_id 就是行 id', async () => {
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/extensions/discover') return { ok: true, builtin_channels: [] }
      if (url === '/api/extensions/list') {
        return { ok: true, extensions: [{ ext_id: 'email_smtp', ext_type: 'channel', name: '邮件通道', enabled: true }] }
      }
      return { ok: true }
    })
    api.hubPost.mockResolvedValue({ ok: true, message: '邮件已发送' })
    render(<HubChannelsPanel />)

    const input = (await screen.findByLabelText('测试消息 email_smtp')) as HTMLInputElement
    expect(input.disabled).toBe(false) // 启用的通道可以直接输
    const btn = screen.getByRole('button', { name: '发送测试消息 email_smtp' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true) // 没有内容时不能发

    fireEvent.change(input, { target: { value: 'hello' } })
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/channels/send', {
      channel_id: 'email_smtp', message: 'hello',
    }))
    expect(await screen.findByText(/已发送/)).toBeTruthy()
  })

  it('停用的通道：输入框禁用 + 提示「先启用」，按钮不可用', async () => {
    setup()
    render(<HubChannelsPanel />)
    await screen.findByText('Webhook 通道')
    const input = (await screen.findByLabelText('测试消息 email_smtp')) as HTMLInputElement
    expect(input.disabled).toBe(true)
    expect(input.placeholder).toContain('启用后可测试发送')
    const btn = screen.getByRole('button', { name: '发送测试消息 email_smtp' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.getByText('先「启用」再测试发送')).toBeTruthy()
  })

  it('未安装的模板行：没有测试发送框（先装再发）', async () => {
    setup()
    render(<HubChannelsPanel />)
    await screen.findByText('Webhook 通道')
    expect(screen.queryByLabelText('测试消息 webhook')).toBeNull()
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

  // ── 报警通道写 .env（2026-11-14，alarm-channel-env-sync）──
  //
  // 后端 /api/extensions/discover 的 resolved_channels 是**唯一真相**：
  // 字段→环境键、值来自哪一层、空态范例与说明都在那里；本页只渲染。

  function viewOf(overrides: Record<string, unknown> = {}) {
    return {
      channel_id: 'email_smtp',
      channel_type: 'smtp',
      primary: true,
      env_keys: ['CP_ALERT_SMTP_HOST', 'CP_ALERT_SMTP_PASSWORD'],
      fields: [
        {
          field: 'smtp_server', env_key: 'CP_ALERT_SMTP_HOST', secret: false,
          local: 'smtp.example.com', local_configured: true,
          effective: 'smtp.example.com', source: 'channel',
          source_label: '通道本地配置', configured: true,
        },
        {
          field: 'smtp_password', env_key: 'CP_ALERT_SMTP_PASSWORD', secret: true,
          local: '***', local_configured: true, effective: '***',
          source: 'channel', source_label: '通道本地配置', configured: true,
        },
      ],
      extra: {},
      example: { channel_type: 'smtp', smtp_server: 'smtp.example.com' },
      notes: ['端口：465 = 隐式 TLS（常见于 139/163），587 = STARTTLS。'],
      ...overrides,
    }
  }

  function setupWithView(view: Record<string, unknown>, config: Record<string, unknown> = {}) {
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/extensions/discover') {
        return {
          ok: true,
          builtin_channels: [{ id: 'email_smtp', name: '邮件通道 (SMTP)', installed: true }],
          resolved_channels: [view],
        }
      }
      if (url === '/api/extensions/list') {
        return {
          ok: true,
          extensions: [{ ext_id: 'email_smtp', ext_type: 'channel', name: '邮件通道', enabled: true, config }],
        }
      }
      return { ok: true }
    })
  }

  it('空通道：端点没配但端口已预置，仍要出范例（不然内置模板永远不显示空态）', async () => {
    setupWithView(viewOf({
      // 后端算出的 empty 与"所有字段都空"不同：端口 587 是有值的，端点为空
      empty: true,
      fields: [
        {
          field: 'smtp_port', env_key: 'CP_ALERT_SMTP_PORT', secret: false,
          local: '587', local_configured: true, effective: '587',
          source: 'channel', source_label: '通道本地配置', configured: true,
        },
        {
          field: 'smtp_server', env_key: 'CP_ALERT_SMTP_HOST', secret: false,
          local: '', local_configured: false, effective: '', source: 'default',
          source_label: '默认值（未配置）', configured: false,
        },
      ],
    }))
    render(<HubChannelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '配置通道 email_smtp' }))
    expect(screen.getByText(/这条通道还是空的/)).toBeTruthy()
    expect(screen.getByText(/smtp\.example\.com/)).toBeTruthy()
  })

  it('空通道：给出可复制的范例 + 说明（文案来自后端）', async () => {
    const writeText = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    setupWithView(viewOf({
      fields: [{
        field: 'smtp_server', env_key: 'CP_ALERT_SMTP_HOST', secret: false,
        local: '', local_configured: false, effective: '', source: 'default',
        source_label: '默认值（未配置）', configured: false,
      }],
    }))
    render(<HubChannelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '配置通道 email_smtp' }))

    expect(screen.getByText(/这条通道还是空的/)).toBeTruthy()
    expect(screen.getByText(/smtp\.example\.com/)).toBeTruthy()
    expect(screen.getByText(/465 = 隐式 TLS/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '复制范例 email_smtp' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(screen.getByText('已复制')).toBeTruthy()
  })

  it('主通道/本机通道各有一处徽标（多通道时谁是 .env 的那条要一眼看出）', async () => {
    setupWithView(viewOf())
    render(<HubChannelsPanel />)
    expect(await screen.findByText('主通道 · 写 .env')).toBeTruthy()
    cleanup()
    api.hubGet.mockReset()
    setupWithView(viewOf({ channel_id: 'email_smtp_2', primary: false }))
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/extensions/discover') {
        return {
          ok: true,
          builtin_channels: [],
          resolved_channels: [viewOf({ channel_id: 'email_smtp_2', primary: false })],
        }
      }
      if (url === '/api/extensions/list') {
        return { ok: true, extensions: [{ ext_id: 'email_smtp_2', ext_type: 'channel', name: '邮件 2', enabled: true }] }
      }
      return { ok: true }
    })
    render(<HubChannelsPanel />)
    expect(await screen.findByText('本机 · 不写 .env')).toBeTruthy()
  })

  it('口令不回显：来源徽标上屏，留空 = 不带该键（不误清授权码）', async () => {
    setupWithView(viewOf(), { smtp_server: 'smtp.example.com', 'smtp_password': 'pw-9x' })
    api.hubPost.mockResolvedValue({ ok: true, message: '已更新通道配置: email_smtp（.env 改 1 项 / 删 0 项）' })
    render(<HubChannelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '配置通道 email_smtp' }))

    // 口令框永远是空的（绝不回显），并以"已配置"提示
    const pwd = screen.getByLabelText('通道字段 email_smtp smtp_password') as HTMLInputElement
    expect(pwd.value).toBe('')
    expect(pwd.placeholder).toContain('已配置')
    // 高级 JSON 里也不能出现口令
    const area = screen.getByLabelText('通道配置 email_smtp') as HTMLTextAreaElement
    expect(area.value).not.toContain('pw-9x')

    const host = screen.getByLabelText('通道字段 email_smtp smtp_server')
    fireEvent.change(host, { target: { value: 'smtp2.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '保存通道配置 email_smtp' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalled())
    const body = api.hubPost.mock.calls.at(-1)?.[1] as { config: Record<string, unknown> }
    expect(body.config.smtp_server).toBe('smtp2.example.com')
    expect('smtp_password' in body.config, '留空必须不带该键（后端 = 不修改）').toBe(false)
  })

  it('清除口令是显式动作：点过之后保存发 null（后端据此删键）', async () => {
    setupWithView(viewOf(), { smtp_server: 'smtp.example.com', 'smtp_password': 'pw-9x' })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<HubChannelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '配置通道 email_smtp' }))

    fireEvent.click(screen.getByRole('button', { name: '清除口令 email_smtp' }))
    fireEvent.click(screen.getByRole('button', { name: '保存通道配置 email_smtp' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalled())
    const body = api.hubPost.mock.calls.at(-1)?.[1] as { config: Record<string, unknown> }
    expect(body.config.smtp_password).toBeNull()
  })

  it('字段留空保存 = 提交空串（后端据此从 .env 删键，回落 env/默认）', async () => {
    setupWithView(viewOf(), { smtp_server: 'smtp.example.com' })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<HubChannelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '配置通道 email_smtp' }))

    const host = screen.getByLabelText('通道字段 email_smtp smtp_server') as HTMLInputElement
    fireEvent.change(host, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: '保存通道配置 email_smtp' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalled())
    const body = api.hubPost.mock.calls.at(-1)?.[1] as { config: Record<string, unknown> }
    expect(body.config.smtp_server).toBe('')
  })

  it('多通道：新增自定义 id 走 install + params（类型/名称/空配置）', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true, message: '已安装' })
    render(<HubChannelsPanel />)
    await screen.findByText('Webhook 通道')

    fireEvent.change(screen.getByLabelText('新通道 id'), { target: { value: 'email_smtp_2' } })
    fireEvent.change(screen.getByLabelText('新通道名称'), { target: { value: '值班邮件' } })
    fireEvent.click(screen.getByRole('button', { name: '新增通道' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/install', {
      type: 'channel',
      source: 'email_smtp_2',
      params: {
        channel_type: 'smtp',
        name: '值班邮件',
        description: '界面新增的通道',
        config: { channel_type: 'smtp' },
      },
    }))
    expect(await screen.findByText(/已新增通道 email_smtp_2/)).toBeTruthy()
  })
})