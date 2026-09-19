/**
 * SubagentMenu 测试 —— 真委派链路与失败可视化
 * ------------------------------------------------------------------
 * 线上反馈「子代理没跑通」的根因：前端调的是 /api/subagent/<name>/execute
 * （容器**占位骨架**：不调 LLM，只回一段"骨架实现"文案，HTTP 200）。
 * 修好后前端改调 /api/subagent/<name>/delegate（真执行器链路），且必须：
 *   1. 展示真结果（result/tier/trace）；
 *   2. 失败时展示 error_code + error（不能只显示 "HTTP 409"）；
 *   3. 无执行通道时提前提示（channel.ok=false）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const { SubagentMenu } = await import('./SubagentMenu')

interface Route {
  status: number
  body: unknown
}

/** 按 URL 分派的 fetch 桩（记录调用，供断言"打的是哪个端点"） */
function installFetch(routes: { list?: Route; delegate?: Route }) {
  const calls: { url: string; body?: unknown }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const route = url.includes('/delegate') ? routes.delegate : routes.list
    const r = route ?? { status: 200, body: { ok: true } }
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => r.body,
    } as unknown as Response
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

const LIST_OK = {
  status: 200,
  body: {
    ok: true,
    count: 1,
    channel: { llm: true, cli: false, ok: true },
    subagents: [
      { name: 'sa-1', model_id: 'deepseek-v4-flash', memory_provider: 'short_term', context_used: 0, context_window: 4096 },
    ],
  },
}

/** 打开菜单（点击工具条按钮 → 触发列表加载） */
async function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: /子代理/ }))
  await waitFor(() => expect(screen.getByText('sa-1')).toBeInTheDocument())
}

function typeTask(text: string) {
  const ta = screen.getByPlaceholderText(/输入要委托的任务/)
  fireEvent.change(ta, { target: { value: text } })
}

function clickDelegate() {
  const btn = screen.getByRole('button', { name: /委托执行/ })
  fireEvent.click(btn)
  return btn
}

describe('SubagentMenu · 真委派', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('委托执行打的是 /delegate（不是占位骨架 /execute），并展示真结果', async () => {
    const calls = installFetch({
      list: LIST_OK,
      delegate: {
        status: 200,
        body: {
          ok: true, name: 'sa-1', result: '子代理真实产出：3 个文件已抽取',
          tier: 'tier1', duration_ms: 1234, trace_id: 'trace-xyz', delegation_id: 'dlg-ui-abc',
        },
      },
    })
    render(<SubagentMenu />)
    await openMenu()
    typeTask('把 docs 抽取为步骤序列并给出清单')
    clickDelegate()

    await waitFor(() => expect(screen.getByText(/子代理真实产出：3 个文件已抽取/)).toBeInTheDocument())
    const delegateCall = calls.find((c) => c.url.includes('/delegate'))
    expect(delegateCall?.url).toBe('/api/subagent/sa-1/delegate')
    expect(delegateCall?.url).not.toContain('/execute')
    expect(delegateCall?.body).toMatchObject({ task: '把 docs 抽取为步骤序列并给出清单' })
    // 成功画面：状态 + trace
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText(/trace-xyz/)).toBeInTheDocument()
    expect(screen.getByText(/tier=tier1/)).toBeInTheDocument()
  })

  it('失败时展示 error_code 与 error（而不是只有 HTTP 状态码）', async () => {
    installFetch({
      list: LIST_OK,
      delegate: { status: 409, body: { ok: false, error_code: 'E_DELEGATION_NO_CHANNEL', error: '未配置执行通道（既无 LLM 也无外部 agent CLI）' } },
    })
    render(<SubagentMenu />)
    await openMenu()
    typeTask('一个足够长的目标任务')
    clickDelegate()

    await waitFor(() => expect(screen.getByText('E_DELEGATION_NO_CHANNEL')).toBeInTheDocument())
    expect(screen.getByText(/未配置执行通道/)).toBeInTheDocument()
    expect(screen.queryByText(/HTTP 409/)).not.toBeInTheDocument()
  })

  it('无执行通道时提前提示（channel.ok=false），不必等点击失败', async () => {
    installFetch({
      list: { status: 200, body: { ...LIST_OK.body, channel: { llm: false, cli: false, ok: false } } },
    })
    render(<SubagentMenu />)
    await openMenu()
    expect(screen.getByText(/未配置执行通道（_llm 为空且 CP_SUBAGENT_AGENT_CLI 未设置）/)).toBeInTheDocument()
  })

  it('高级项填写后随请求体透传（八要素可选项）', async () => {
    const calls = installFetch({
      list: LIST_OK,
      delegate: { status: 200, body: { ok: true, result: 'ok' } },
    })
    render(<SubagentMenu />)
    await openMenu()
    typeTask('请统计仓库 TODO 并给出表格')

    fireEvent.click(screen.getByRole('button', { name: /高级：约束/ }))
    fireEvent.change(screen.getByPlaceholderText(/只读仓库/), { target: { value: '只读, 不联网' } })
    fireEvent.change(screen.getByPlaceholderText(/不得删除任何文件/), { target: { value: '不得删除任何文件' } })
    fireEvent.change(screen.getByPlaceholderText('文本要点'), { target: { value: 'markdown 表格' } })
    fireEvent.change(screen.getByPlaceholderText('4000'), { target: { value: '1500' } })
    fireEvent.change(screen.getByPlaceholderText('120'), { target: { value: '45' } })
    clickDelegate()

    await waitFor(() => expect(calls.some((c) => c.url.includes('/delegate'))).toBe(true))
    const body = calls.find((c) => c.url.includes('/delegate'))?.body as Record<string, unknown>
    expect(body.constraints).toEqual(['只读', '不联网'])
    expect(body.prohibitions).toEqual(['不得删除任何文件'])
    expect(body.artifact_format).toBe('markdown 表格')
    expect(body.budget_tokens).toBe(1500)
    expect(body.timeout_seconds).toBe(45)
  })

  it('无分身时不发起委派（按钮禁用）', async () => {
    const calls = installFetch({
      list: { status: 200, body: { ok: true, count: 0, channel: { llm: true, ok: true }, subagents: [] } },
    })
    render(<SubagentMenu defaultTask="兜底任务内容" />)
    fireEvent.click(screen.getByRole('button', { name: /子代理/ }))
    await waitFor(() => expect(screen.getByText(/暂无活跃子代理/)).toBeInTheDocument())
    const btn = screen.getByRole('button', { name: /委托执行/ })
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(calls.some((c) => c.url.includes('/delegate'))).toBe(false)
  })
})
