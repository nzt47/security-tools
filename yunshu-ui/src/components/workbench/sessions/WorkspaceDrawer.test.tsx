/**
 * WorkspaceDrawer 测试 —— 工作空间来源（随项目 / 本会话自绑 / 默认）
 * ------------------------------------------------------------------
 * 后端 GET /api/sessions/<id>/workspace 新增 source 字段后，抽屉要把三种来源讲清楚：
 *   - source='project'：随项目的工作空间（可本会话临时覆盖）；
 *   - source='session'：本会话自绑，解除后回到项目/默认；
 *   - 无 source（旧后端）：按 custom 回退（默认 / 已绑定自定义工作区）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { WorkspaceDrawer } from './WorkspaceDrawer'
import type { SessionMeta } from '../../../lib/sessionApi'

const SESSION = { id: 's1', title: '会话一' } as SessionMeta

interface Route {
  status: number
  body: unknown
}

function installFetch(workspace: Route) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const route: Route = url.includes('/workspace') ? workspace : { status: 200, body: { workspaces: [] } }
    return {
      ok: route.status >= 200 && route.status < 300,
      status: route.status,
      json: async () => route.body,
      text: async () => JSON.stringify(route.body),
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fn)
}

describe('WorkspaceDrawer · 工作空间来源', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('source=project：显示「随项目的工作空间（path）」并说明可本会话临时覆盖', async () => {
    installFetch({
      status: 200,
      body: {
        session_id: 's1',
        root: 'C:/work/alpha',
        source: 'project',
        project_id: 'proj-1',
        custom: false,
        exists: true,
        files: [],
        truncated: false,
      },
    })
    render(<WorkspaceDrawer session={SESSION} onClose={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('随项目的工作空间')).toBeInTheDocument())
    expect(screen.getByText(/随项目的工作空间（C:\/work\/alpha）/)).toBeInTheDocument()
    expect(screen.getByText(/本会话临时覆盖/)).toBeInTheDocument()
  })

  it('source=session：显示本会话自绑，并说明解除后回到项目/默认', async () => {
    installFetch({
      status: 200,
      body: {
        session_id: 's1',
        root: 'C:/custom/ws',
        source: 'session',
        custom: true,
        exists: true,
        files: [],
        truncated: false,
      },
    })
    render(<WorkspaceDrawer session={SESSION} onClose={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('已绑定自定义工作区')).toBeInTheDocument())
    expect(screen.getByText(/当前为本会话自绑工作区/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '恢复默认工作空间' })).toBeInTheDocument()
  })

  it('旧后端无 source：按 custom 回退为默认工作空间', async () => {
    installFetch({
      status: 200,
      body: {
        session_id: 's1',
        root: 'C:/yunshu/data/sessions/s1/workspace',
        custom: false,
        exists: true,
        files: [],
        truncated: false,
      },
    })
    render(<WorkspaceDrawer session={SESSION} onClose={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('默认工作空间')).toBeInTheDocument())
  })
})
