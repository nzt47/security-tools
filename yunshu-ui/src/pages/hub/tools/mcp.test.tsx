/**
 * MCP 系统 —— 服务 CRUD/启停 + 内置模板就地安装
 * ------------------------------------------------------------------
 * 【2026-10-18】「内置 MCP 服务」从扩展市场搬到这里。锁两件事：
 *   1. 内置模板清单来自 /api/extensions/discover（builtin_mcp），
 *      installed/enabled 决定显示"安装"还是"已装/已启用"；
 *   2. 就地安装调 POST /api/extensions/install {type:'mcp', source:id}，
 *      装完立即重拉服务表（不要求用户回扩展市场看结果）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), hubDelete: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
  hubDelete: api.hubDelete,
}))

const env = vi.hoisted(() => ({ getEnvelope: vi.fn() }))
vi.mock('@/api/envelope', () => ({ getEnvelope: env.getEnvelope }))

import ToolsMcp from './mcp'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.hubDelete.mockReset()
  env.getEnvelope.mockReset()
})

function setup() {
  env.getEnvelope.mockResolvedValue({
    ok: true,
    services: [{ service_id: 's1', name: '已有服务', enabled: true, transport: 'stdio' }],
  })
  api.hubGet.mockResolvedValue({
    ok: true,
    builtin_mcp: [
      { id: 'filesystem', name: '文件系统 MCP', description: '安全的文件系统操作服务', installed: false },
      { id: 'github', name: 'GitHub MCP', description: 'GitHub API 集成', installed: true },
    ],
  })
}

describe('ToolsMcp', () => {
  it('渲染服务表 + 内置模板（已装的显示已装，未装给安装按钮）', async () => {
    setup()
    render(<ToolsMcp />)

    await waitFor(() => expect(env.getEnvelope).toHaveBeenCalledWith('/api/mcp/services'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/extensions/discover'))

    expect(await screen.findByText('已有服务')).toBeTruthy()
    expect(screen.getByText('文件系统 MCP')).toBeTruthy()
    expect(screen.getByText('GitHub MCP')).toBeTruthy()
    expect(screen.getByText('已装（未启用）')).toBeTruthy()
    expect(screen.getByRole('button', { name: '安装内置 MCP filesystem' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '安装内置 MCP github' })).toBeNull()
    // 内置组标题带上数量，便于一眼看出还有几个可装
    expect(screen.getByText(/内置 MCP 服务（可安装）（2）/)).toBeTruthy()
  })

  it('就地安装：POST {type:mcp, source:id}，装完重拉服务表', async () => {
    setup()
    api.hubPost.mockResolvedValue({ ok: true, message: '已安装' })
    render(<ToolsMcp />)
    await screen.findByText('文件系统 MCP')

    const before = env.getEnvelope.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: '安装内置 MCP filesystem' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/extensions/install', {
      type: 'mcp', source: 'filesystem',
    }))
    // load() 与 loadBuiltin() 各重拉一次 ⇒ 用户不必回扩展市场看结果
    await waitFor(() => expect(env.getEnvelope.mock.calls.length).toBeGreaterThan(before))
    await waitFor(() => expect(api.hubGet.mock.calls.length).toBeGreaterThan(1))
  })
})
