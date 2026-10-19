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
  it('打开即拉 list / installed / recommend 三个只读面（不再拉 discover）', async () => {
    baseGets()
    render(<ExtensionsMarket />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/list'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/installed'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/market/recommend?limit=6'))
    expect(await screen.findByText('self_reflection')).toBeTruthy()
    expect(screen.getByText('自省反思')).toBeTruthy()
    // 内置项清单已不在市场拉取（分组整体迁走）
    expect(api.hubGet).not.toHaveBeenCalledWith('/api/extensions/discover')
  })

  it('内置行为（persona 技能）不再在市场安装：改成指路技能中心', async () => {
    // 2026-10-19：内置项的启停事实源是 SkillRegistry，入口在「技能中心 · LLM 技能」；
    // 市场里既不该有"安装 memory_summary"，也不该再出现「内置行为」分组标题。
    baseGets()
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    expect(screen.queryByRole('button', { name: '安装 memory_summary' })).toBeNull()
    // 分组标题（h3）不再存在；只剩指路文案里的同一串字
    expect(screen.queryByRole('heading', { name: /内置行为（/ })).toBeNull()
    expect(screen.getByText(/技能中心 · LLM 技能/)).toBeTruthy()
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

  it('插件包只发现/浏览：安装按钮不给，指到「插件清单」（禁止双入口）', async () => {
    api.hubGet.mockImplementation(async (url: string) => {
      if (url === '/api/extensions/list') return { ok: true, extensions: [] }
      if (url === '/api/extensions/installed') return { ok: true }
      if (url === '/api/extensions/discover') return { ok: true, builtin_skills: [] }
      if (url.startsWith('/api/extensions/market/recommend')) {
        return { ok: true, recommendations: [{ ext_type: 'plugin', id: 'demo-plugin', name: '演示插件包' }] }
      }
      if (url.startsWith('/api/extensions/market/search')) {
        return { ok: true, builtin: [{ id: 'demo-plugin', name: '演示插件包', ext_type: 'plugin' }], community: [], github: [] }
      }
      return { ok: true }
    })
    render(<ExtensionsMarket />)
    fireEvent.click(screen.getByRole('button', { name: '搜索扩展市场' }))

    await waitFor(() => expect(screen.getAllByText('演示插件包').length).toBeGreaterThan(0))
    expect(screen.queryByRole('button', { name: '安装 demo-plugin' })).toBeNull()
    expect(screen.queryByRole('button', { name: '安装推荐 demo-plugin' })).toBeNull()
    expect(screen.getAllByText(/到插件清单安装/).length).toBeGreaterThan(0)
  })

  it('内置通道与发送框已迁出市场，改成指路消息中心（禁止双入口）', async () => {
    baseGets()
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    // 分组标题（"内置通道（n）"）与发送框都不该再出现在市场
    expect(screen.queryByText(/^内置通道（/)).toBeNull()
    expect(screen.queryByRole('button', { name: '发送通道消息' })).toBeNull()
    expect(screen.queryByLabelText('通道 id')).toBeNull()
    // 替代入口必须可发现：指到 系统管理 · 消息中心
    expect(screen.getByText(/系统管理 · 消息中心/)).toBeTruthy()
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

  it('「内置 MCP 服务」组已迁出市场，改成指路 MCP 系统（禁止双入口）', async () => {
    baseGets()
    render(<ExtensionsMarket />)
    await screen.findByText('self_reflection')

    // 分组标题（"内置 MCP 服务（n）"）必须消失；只剩一句指路文案
    expect(screen.queryByText(/^内置 MCP 服务（/)).toBeNull()
    expect(screen.queryByRole('button', { name: '安装 filesystem' })).toBeNull()
    // 市场里仍会提到"MCP 系统"（指路文案），但不再有该分组的标题与安装按钮
    expect(screen.getByText(/工具调用 · MCP 系统/)).toBeTruthy()
  })
})
