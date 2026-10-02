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
function installFetch(routes: { list?: Route; delegate?: Route; history?: Route }) {
  const calls: { url: string; body?: unknown }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    // 顺序有意：/api/subagent/history 与 /api/subagent/<name>/delegate 是两条端点，
    // 且 "delegations"/"history" 这类字面量容易误判，故先判 history
    const route = url.includes('/history')
      ? routes.history
      : url.includes('/delegate')
        ? routes.delegate
        : routes.list
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

  it('没有活跃分身时走临时分身端点（不再是"列表为空 ⇒ 无法委托"）', async () => {
    // 具名端点要求分身先存在，而容器跑完即回收 ⇒"禁用 + 静默 return"等于把界面变成死路。
    // 现契约：没有活跃分身时提交到 /api/subagent/delegate（现建现用、跑完即回收），
    // 与模型侧 delegate 工具同一条链路。
    const calls = installFetch({
      list: { status: 200, body: { ok: true, count: 0, channel: { llm: true, ok: true }, subagents: [] } },
      delegate: { status: 200, body: { ok: true, ephemeral: true, name: '临时分身', result: '临时分身的产出' } },
    })
    render(<SubagentMenu defaultTask="兜底任务内容" />)
    fireEvent.click(screen.getByRole('button', { name: /子代理/ }))
    await waitFor(() => expect(screen.getByText(/暂无活跃子代理/)).toBeInTheDocument())

    const btn = screen.getByRole('button', { name: /委托执行/ })
    expect(btn).toBeEnabled()
    expect(btn.textContent).toContain('临时分身')
    fireEvent.click(btn)

    await waitFor(() => expect(calls.some((c) => c.url === '/api/subagent/delegate')).toBe(true))
    expect(calls.find((c) => c.url === '/api/subagent/delegate')?.body).toMatchObject({ task: '兜底任务内容' })
    // 绝不允许拼出脏 URL（没有目标时曾经会走到的形态）
    expect(calls.some((c) => /\/api\/subagent\/(undefined|null)\/delegate/.test(c.url))).toBe(false)
    await waitFor(() => expect(screen.getByText(/临时分身的产出/)).toBeInTheDocument())
  })

  it('没有任务文本时按钮禁用（无内容可委托）', async () => {
    installFetch({ list: LIST_OK })
    render(<SubagentMenu />)
    await openMenu()
    expect(screen.getByRole('button', { name: /委托执行/ })).toBeDisabled()
  })

  /** 空活跃列表 + 有委派记录：这是线上"业务发生了但面板什么都没有"的那个场景 */
  const EMPTY_LIST = {
    status: 200,
    body: { ok: true, count: 0, channel: { llm: true, ok: true }, subagents: [] },
  }

  const HISTORY_OK = {
    status: 200,
    body: {
      ok: true,
      count: 2,
      total: 2,
      records: [
        {
          delegation_id: 'dlg-new', subagent: 'delegate-ab12', source: 'tool', ok: false,
          goal: '把 docs 下的设计稿抽取为步骤序列', duration_ms: 1200, tier: 'tier1',
          error_code: 'E_DELEGATION_INCOMPLETE', error: '目标过短',
          created_at: '2026-10-02T06:00:00+00:00',
        },
        {
          delegation_id: 'dlg-old', subagent: 'delegate-cd34', source: 'ui', ok: true,
          goal: '统计仓库 TODO 并给出表格', duration_ms: 800,
          created_at: '2026-10-02T05:00:00+00:00',
        },
      ],
    },
  }

  async function openMenuRaw() {
    fireEvent.click(screen.getByRole('button', { name: /子代理/ }))
  }

  it('活跃列表为空时也展示委派记录（发生过什么不依赖分身是否还活着）', async () => {
    installFetch({ list: EMPTY_LIST, history: HISTORY_OK })
    render(<SubagentMenu />)
    await openMenuRaw()

    await waitFor(() => expect(screen.getByText('把 docs 下的设计稿抽取为步骤序列')).toBeInTheDocument())
    expect(screen.getByText('统计仓库 TODO 并给出表格')).toBeInTheDocument()
    expect(screen.getByText(/共 2 条/)).toBeInTheDocument()
    // 成功/失败与来源标注都来自接口字段（不是写死文案）
    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getByText('成功')).toBeInTheDocument()
    expect(screen.getByText('模型工具')).toBeInTheDocument()
    expect(screen.getByText('界面')).toBeInTheDocument()
    expect(screen.getByText(/E_DELEGATION_INCOMPLETE/)).toBeInTheDocument()
  })

  it('「复用目标」把历史记录的目标回填到输入框', async () => {
    installFetch({ list: EMPTY_LIST, history: HISTORY_OK })
    render(<SubagentMenu />)
    await openMenuRaw()
    await waitFor(() => expect(screen.getByText('统计仓库 TODO 并给出表格')).toBeInTheDocument())

    fireEvent.click(screen.getAllByRole('button', { name: /复用目标/ })[1])
    expect((screen.getByPlaceholderText(/输入要委托的任务/) as HTMLTextAreaElement).value)
      .toBe('统计仓库 TODO 并给出表格')
  })

  it('陈旧选择：提示已不在活跃列表，并给出「清除选择」', async () => {
    localStorage.setItem('yunshu.subagent.delegate', 'sa-gone')
    installFetch({ list: LIST_OK, history: HISTORY_OK })
    render(<SubagentMenu />)
    await openMenu()

    await waitFor(() => expect(screen.getByText(/已保存的选择「sa-gone」已不在活跃列表/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /清除选择/ }))
    await waitFor(() => expect(screen.queryByText(/已保存的选择「sa-gone」/)).not.toBeInTheDocument())
    expect(localStorage.getItem('yunshu.subagent.delegate')).toBeNull()
  })

  it('陈旧选择不会把委派打到已消失的分身（回落到活跃分身）', async () => {
    // 修之前：localStorage 里的名字照发 ⇒ POST /api/subagent/sa-gone/delegate ⇒ 404「分身不存在」
    localStorage.setItem('yunshu.subagent.delegate', 'sa-gone')
    const calls = installFetch({
      list: LIST_OK,
      history: HISTORY_OK,
      delegate: { status: 200, body: { ok: true, result: 'ok' } },
    })
    render(<SubagentMenu />)
    await openMenu()
    typeTask('把仓库里的 TODO 统计成表格')
    clickDelegate()

    await waitFor(() => expect(calls.some((c) => c.url.includes('/delegate'))).toBe(true))
    expect(calls.find((c) => c.url.includes('/delegate'))?.url).toBe('/api/subagent/sa-1/delegate')
  })
  it('委托之后立刻刷新委派记录（刚刚这一下要马上出现在历史里）', async () => {
    const calls = installFetch({
      list: LIST_OK,
      history: HISTORY_OK,
      delegate: { status: 200, body: { ok: true, result: 'ok' } },
    })
    render(<SubagentMenu />)
    await openMenu()
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/history')).length).toBe(1))

    typeTask('把仓库里的 TODO 统计成表格')
    clickDelegate()
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/history')).length).toBeGreaterThan(1))
  })
})
