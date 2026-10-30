/**
 * SessionAgentMenu 测试 —— 本会话智能体（分身）选择器
 * ------------------------------------------------------------------
 * 会话级 agent 与 SubagentMenu 的"一次性委托"是两件事：
 *   1. 下拉含「母体（默认）」+ GET /api/subagent/list 返回的各分身名；
 *   2. 选择后 PUT /api/sessions/<id>/agent {agent}（母体 = null）并回调刷新；
 *   3. 失败（列表拉取 / 切换）必须上屏，不静默。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const { SessionAgentMenu } = await import('./SessionAgentMenu')

interface Route {
  status: number
  body: unknown
  /** 是否带 X-Envelope 头（默认 true；false = 模拟"端点没迁移信封"的旧响应） */
  env?: boolean
}

interface Call {
  url: string
  method: string
  body?: Record<string, unknown>
}

function installFetch(handlers: {
  list?: Route
  setAgent?: Route | ((body: Record<string, unknown> | undefined) => Route)
}) {
  const calls: Call[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined
    calls.push({ url, method, body })
    let route: Route
    if (url.includes('/api/subagent/list')) {
      route = handlers.list ?? { status: 200, body: { ok: true, subagents: [] } }
    } else if (/\/api\/sessions\/[^/]+\/agent$/.test(url)) {
      route =
        typeof handlers.setAgent === 'function'
          ? handlers.setAgent(body)
          : (handlers.setAgent ?? { status: 200, body: { ok: true, agent: body?.agent ?? null } })
    } else {
      route = { status: 200, body: {} }
    }
    // 【2026-10-30】列表端点真实返回统一信封（X-Envelope: v2，载荷在 data 里）。
    // 这里的桩必须照真实契约写：旧版本返回扁平 {ok, subagents}，恰好把
    // "读顶层 r.subagents" 的缺陷掩盖了（§8 教训：替身照想象写）。
    const isList = url.includes('/api/subagent/list')
    const wire = isList ? { code: 200, data: route.body } : route.body
    const envVersion = isList && route.env !== false ? 'v2' : null
    return {
      ok: route.status >= 200 && route.status < 300,
      status: route.status,
      headers: {
        get: (name: string) => (name.toLowerCase() === 'x-envelope' ? envVersion : null),
      },
      json: async () => wire,
      text: async () => JSON.stringify(wire),
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

const LIST_OK: Route = {
  status: 200,
  body: {
    ok: true,
    count: 2,
    subagents: [
      { name: 'sa-1', model_id: 'deepseek-v4-flash', status: '就绪' },
      { name: 'sa-2', model_id: 'deepseek-v4-pro' },
    ],
  },
}

async function openMenu(label: RegExp = /智能体：/) {
  fireEvent.click(screen.getByRole('button', { name: label }))
  await waitFor(() => expect(screen.getByText('母体（默认）')).toBeInTheDocument())
}

describe('SessionAgentMenu · 会话智能体选择', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('下拉含母体 + 分身列表，选择后 PUT /agent 并回调', async () => {
    const calls = installFetch({ list: LIST_OK })
    const onChanged = vi.fn()
    render(<SessionAgentMenu sessionId="s1" agent={null} onChanged={onChanged} />)

    expect(screen.getByRole('button', { name: '智能体：母体' })).toBeInTheDocument()
    await openMenu()
    await waitFor(() => expect(screen.getByText('sa-1')).toBeInTheDocument())
    expect(screen.getByText('sa-2')).toBeInTheDocument()

    fireEvent.click(screen.getByText('sa-1'))
    await waitFor(() => {
      const call = calls.find((c) => /\/api\/sessions\/s1\/agent$/.test(c.url))
      expect(call?.method).toBe('PUT')
      expect(call?.body).toEqual({ agent: 'sa-1' })
    })
    expect(onChanged).toHaveBeenCalledWith('sa-1')
  })

  it('当前会话 agent 回显在按钮上（从父层传入）', async () => {
    installFetch({ list: LIST_OK })
    render(<SessionAgentMenu sessionId="s1" agent="sa-2" />)
    expect(screen.getByRole('button', { name: '智能体：sa-2' })).toBeInTheDocument()
  })

  it('选择「母体（默认）」→ PUT agent:null 并回调 null', async () => {
    const calls = installFetch({ list: LIST_OK })
    const onChanged = vi.fn()
    render(<SessionAgentMenu sessionId="s1" agent="sa-1" onChanged={onChanged} />)

    await openMenu(/智能体：sa-1/)
    fireEvent.click(screen.getByText('母体（默认）'))
    await waitFor(() => {
      const call = calls.find((c) => /\/api\/sessions\/s1\/agent$/.test(c.url))
      expect(call?.body).toEqual({ agent: null })
    })
    expect(onChanged).toHaveBeenCalledWith(null)
  })

  it('切换失败时把错误上屏（不静默）', async () => {
    installFetch({
      list: LIST_OK,
      setAgent: { status: 500, body: { error: '数据库写入失败' } },
    })
    const onChanged = vi.fn()
    render(<SessionAgentMenu sessionId="s1" agent={null} onChanged={onChanged} />)

    await openMenu()
    await waitFor(() => expect(screen.getByText('sa-1')).toBeInTheDocument())
    fireEvent.click(screen.getByText('sa-1'))

    await waitFor(() =>
      expect(screen.getByText(/切换智能体失败：数据库写入失败/)).toBeInTheDocument(),
    )
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('分身列表拉取失败时上屏', async () => {
    installFetch({ list: { status: 500, body: { error: 'subagent disabled' } } })
    render(<SessionAgentMenu sessionId="s1" agent={null} />)
    fireEvent.click(screen.getByRole('button', { name: '智能体：母体' }))
    await waitFor(() => expect(screen.getByText(/智能体列表不可用/)).toBeInTheDocument())
  })

  it('当前 agent 已不在活跃列表时也可见可选，并提示已下线', async () => {
    installFetch({ list: LIST_OK })
    render(<SessionAgentMenu sessionId="s1" agent="sa-gone" />)
    await openMenu(/智能体：sa-gone/)
    await waitFor(() => expect(screen.getByText(/已不在活跃列表/)).toBeInTheDocument())
    // 触发器回显 + 下拉里的可选项各一处
    expect(screen.getAllByText('sa-gone').length).toBe(2)
  })

  it('列表端点缺信封头时上屏错误，而不是静默显示「暂无活跃分身」', async () => {
    // 2026-10-30 回归：用户已创建分身（/api/subagent/list count=1），菜单却显示
    // 「暂无活跃分身」——根因是 hubGet 读顶层 r.subagents 而载荷在 data 里。
    // 改用 getEnvelope 后，未迁移/被回退的端点必须报错，不能再假装"没有分身"。
    installFetch({ list: { status: 200, body: { ...(LIST_OK.body as Record<string, unknown>) }, env: false } })
    render(<SessionAgentMenu sessionId="s1" agent={null} />)
    fireEvent.click(screen.getByRole('button', { name: '智能体：母体' }))
    await waitFor(() => expect(screen.getByText(/智能体列表不可用/)).toBeInTheDocument())
    expect(screen.queryByText('暂无活跃分身')).not.toBeInTheDocument()
  })

  it('未选择会话时按钮禁用', () => {
    installFetch({ list: LIST_OK })
    render(<SessionAgentMenu sessionId={null} agent={null} />)
    expect(screen.getByRole('button', { name: '智能体：母体' })).toBeDisabled()
  })
})
