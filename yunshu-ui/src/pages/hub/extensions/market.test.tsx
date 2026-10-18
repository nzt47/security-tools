/**
 * 扩展市场 —— 11 条 /api/extensions 端点
 * ------------------------------------------------------------------
 * 锁四件事：
 *   1. 四个只读面（list / installed / discover / recommend）确实在页面打开时被调用；
 *   2. 安装带 {type, source}；
 *   3. 卸载是两步（先展开确认条，再点确认才发请求）——不可逆动作不能一点就走；
 *   4. 配置不是合法 JSON 时不发请求。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import ExtensionsMarket from './market'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function baseGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/extensions/list') {
      return {
        ok: true,
        extensions: [{
          ext_id: 'self_reflection', ext_type: 'skill', name: '自省反思',
          version: '0.1.0', enabled: true, config: { depth: 1 },
        }],
      }
    }
    if (url === '/api/extensions/installed') {
      return { ok: true, skills: [1, 2], plugins: [], channels: [], mcp_services: [] }
    }
    if (url === '/api/extensions/discover') {
      return {
        ok: true,
        builtin_skills: [{ id: 'memory_summary', name: '记忆摘要', installed: false }],
        builtin_mcp: [{ id: 'filesystem', name: '文件系统 MCP', installed: false }],
        builtin_channels: [{ id: 'webhook', name: 'Webhook 通道', installed: true }],
      }
    }
    if (url.startsWith('/api/extensions/market/recommend')) {
      return { ok: true, recommendations: [{ ext_type: 'skill', id: 'emotion_expression', name: '情感表达' }] }
    }
    if (url.startsWith('/api/extensions/market/search')) {
      return { ok: true, builtin: [{ id: 'self_reflection', name: '自省反思', ext_type: 'skill' }], community: [], github: [] }
    }
    return { ok: true }
  })
}

describe('ExtensionsMarket', () => {
  it('打开即拉 list / installed / discover / recommend 四个只读面', async () => {
    baseGets()
    render(<ExtensionsMarket />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/list'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/installed'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/discover'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/market/recommend?limit=6'))
    expect(await screen.findByText('self_reflection')).toBeTruthy()
    expect(screen.getByText('自省反思')).toBeTruthy()
  })

  it('安装内置技能带 {type, source}', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, message: '已安装' })
    render(<ExtensionsMarket />)
    await screen.findByText('自省反思')

    fireEvent.click(screen.getByRole('button', { name: '安装 memory_summary' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/install', {
      type: 'skill', source: 'memory_summary',
    }))
  })

  it('已启用的内置技能显示"已启用"且不给安装按钮（状态以 SkillRegistry 为准）', async () => {
    // 2026-10-18：这 7 个 persona 技能在文件轨 enabled=true、运行时一直在注入，
    // 但扩展存储可能没有安装记录 ⇒ 旧 UI 会显示"安装"。启用态改为读后端 enabled 字段。
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/extensions/list') return { ok: true, extensions: [] }
      if (url === '/api/extensions/installed') return { ok: true, skills: [], plugins: [], channels: [], mcp_services: [] }
      if (url === '/api/extensions/discover') {
        return {
          ok: true,
          builtin_skills: [{ id: 'memory_summary', name: '记忆摘要', installed: false, enabled: true, managed_in: 'skill-center' }],
          builtin_mcp: [], builtin_channels: [],
        }
      }
      if (url.startsWith('/api/extensions/market/recommend')) return { ok: true, recommendations: [] }
      return { ok: true }
    })
    render(<ExtensionsMarket />)
    expect(await screen.findByText('已启用')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '安装 memory_summary' })).toBeNull()
    // 分组改名：与技能中心里的 skill.md 资产区分开
    expect(screen.getByText(/内置行为（技能中心管理）/)).toBeTruthy()
  })

  it('卸载是两步：先展开确认条，再点确认才发请求', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, message: '已卸载' })
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    fireEvent.click(screen.getByRole('button', { name: '卸载 self_reflection' }))
    expect(await screen.findByText(/不可逆删除/)).toBeTruthy()
    expect(api.hubPost, '展开确认条时不该已经发请求').not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '确认卸载 self_reflection' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/uninstall', {
      type: 'skill', id: 'self_reflection', force: true,
    }))
  })

  it('启停带 enabled 的下一态（不是当前态）', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    fireEvent.click(screen.getByRole('button', { name: '切换 self_reflection' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/toggle', {
      type: 'skill', id: 'self_reflection', enabled: false,
    }))
  })

  it('配置：坏 JSON 不发请求；好 JSON 原样提交', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    fireEvent.click(screen.getByRole('button', { name: '配置 self_reflection' }))
    const box = await screen.findByLabelText('扩展配置 JSON')
    fireEvent.change(box, { target: { value: '{oops' } })
    fireEvent.click(screen.getByRole('button', { name: '保存扩展配置' }))
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.change(box, { target: { value: '{"depth":2}' } })
    fireEvent.click(screen.getByRole('button', { name: '保存扩展配置' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/configure', {
      type: 'skill', id: 'self_reflection', config: { depth: 2 },
    }))
  })

  it('市场搜索带上关键词；刷新索引走 POST', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, count: 12 })
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    fireEvent.change(screen.getByLabelText('市场搜索关键词'), { target: { value: '自省' } })
    fireEvent.click(screen.getByRole('button', { name: '搜索扩展市场' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith(
      '/api/extensions/market/search?q=' + encodeURIComponent('自省'),
    ))

    fireEvent.click(screen.getByRole('button', { name: '刷新社区索引' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/market/refresh', {}))
    expect(await screen.findByText(/社区索引已刷新：12/)).toBeTruthy()
  })

  it('通道发送：缺 id 或内容时按钮不可用', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    const send = screen.getByRole('button', { name: '发送通道消息' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('通道 id'), { target: { value: 'webhook' } })
    fireEvent.change(screen.getByLabelText('通道消息内容'), { target: { value: 'hello' } })
    expect(send.disabled).toBe(false)
    fireEvent.click(send)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/channels/send', {
      channel_id: 'webhook', message: 'hello',
    }))
  })

  it('不再有「Claude 技能」类型：改成指路技能中心的导入入口（不能只删组）', async () => {
    baseGets()
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    // 类型下拉/统计卡里都不该再出现"Claude 技能"
    expect(screen.queryByText('Claude 技能')).toBeNull()
    expect(screen.queryByRole('option', { name: 'Claude 技能' })).toBeNull()
    // 替代入口必须可发现：指到 技能中心 → 维护 → 从 .claude/skills 导入
    expect(screen.getByText(/技能中心 → 维护 →「从 \.claude\/skills 导入」/)).toBeTruthy()
  })
})
