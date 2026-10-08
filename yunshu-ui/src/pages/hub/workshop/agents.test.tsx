/**
 * 装配车间 —— 「分身创建与组装」三视图容器守卫（2026-10-08 合并后）
 * ------------------------------------------------------------------
 * 本次合并把两条分散入口收进同一页：
 *   · 原「工具调用 → 主线管理」Tab（`pages/hub/tools/lines.tsx` → `workshop/agent-lines.tsx`）
 *   · 原 `features/line-assembly`（宿生功能零清单导航项 → `workshop/line-assembly.tsx`，manifest 已删）
 * 锁死的不变量：
 *   1. 三个视图 Tab 并列：分身 / 主线档案 / 主线 × 四面（顺序=阅读顺序）；
 *   2. 点击即切换，且**真的把对应视图渲染出来**（懒加载完成后可见其独有标记）；
 *   3. 视图选择记忆在 localStorage；非法值回落「分身」；
 *   4. 分身视图仍打 `/api/subagent/list` 并显示列表（搬家没有搬丢功能）。
 *
 * 三个子视图各自打自己的接口 → 统一 stub fetch（信封端点带 X-Envelope: v2）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'

const LINE = {
  id: 'engineering', name: '工程线', description: '', enabled: true,
  plane_weights: { act: 1 }, plane_floors: { act: 1 }, boost: [], mute: [], tags: [],
  max_tools: 20, effect_allow: ['read'], requires_approval: [], allow_govern: false,
  skills: [], prompt_note: '',
}

const PLANES = {
  ok: true,
  planes: [
    { key: 'resident', label: '常驻', hint: '', count: 0 },
    { key: 'perceive', label: '感知', hint: '', count: 0 },
    { key: 'act', label: '行动', hint: '', count: 1 },
    { key: 'govern', label: '治理', hint: '', count: 0 },
  ],
  plane_counts: {}, effects: [], effect_order: ['read', 'write', 'execute', 'extend'],
  effect_note: '', risks: [],
  tools: [{ name: 'read_file', category: '文件', plane: 'act', effect: 'read', risk: 'low', tags: [], needs_approval: false, internal: false }],
  tool_count: 1, tools_without_declaration: [], tool_source: 'registry', defaults: {},
}

const DETAIL = {
  ok: true,
  line: LINE,
  preview: {
    line_id: 'engineering', tools: ['read_file'], count: 1, by_plane: { act: ['read_file'] },
    denied_by_effect: [], denied_unknown: [], muted: [], truncated: [], reasons: {},
    needs_approval: [], max_tools: 20, over_budget: false, tools_meta: {},
  },
  issues: [], tool_source: 'registry',
}

/** 普通 JSON 响应（走 apiClient.request：读 text()） */
function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

/** 统一信封响应（走 getEnvelope：必须带 X-Envelope: v2 且体是 {code,data}） */
function envelope(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => (k === 'X-Envelope' ? 'v2' : null) },
    json: async () => ({ code: 200, data }),
    text: async () => JSON.stringify({ code: 200, data }),
  } as unknown as Response
}

/** 后端 `GET /api/subagent/list` 载荷：三行覆盖"指定 / 跟随 / 指定未生效"三档 */
const LIST_PAYLOAD = {
  ok: true, count: 3, channel: {},
  llm: {
    model: 'deepseek-flash', provider: 'deepseek',
    options: [
      { model: 'deepseek-flash', source: 'deployment' },
      { model: 'deepseek-v4-pro', source: 'declared' },
    ],
  },
  // 角色目录（后端 role_catalog()）：模板候选 + 三档语义（前端不自己维护第二份清单）
  role: {
    default_tier: 'template',
    role_text_max_chars: 2000,
    templates: [
      { id: 'generic_readonly', title: '通用只读执行体', body: '…', note: '' },
      { id: 'code_review', title: '代码审查', body: '…', note: '' },
      { id: 'research', title: '资料调研', body: '…', note: '' },
    ],
    tiers: [
      { value: 'template', label: '受控模板', red: false, audit: false },
      { value: 'template+text', label: '模板 + 自由文本（进约束）', red: false, audit: true },
      { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true, audit: true },
    ],
  },
  subagents: [
    {
      name: 'alpha', model_id: 'deepseek-v4-pro', memory_provider: 'holographic', status: 'running',
      llm: { requested: 'deepseek-v4-pro', model: 'deepseek-v4-pro', source: 'explicit', error: '', temperature: null },
      role: { template: 'code_review', tier: 'template', source: 'role_template:code_review', fragment_chars: 78, constraints: 0, audit_required: false, red: false, error: '' },
    },
    {
      name: 'beta', model_id: '', memory_provider: 'holographic', status: 'idle', llm_temperature: 0.25,
      llm: { requested: '', model: 'deepseek-flash', source: 'inherit', error: '', temperature: 0.25 },
      role: { template: '', tier: 'template', source: '', fragment_chars: 0, constraints: 0, audit_required: false, red: false, error: '' },
    },
    {
      name: 'gamma', model_id: 'gpt-4', memory_provider: 'holographic', status: 'idle',
      llm: {
        requested: 'gpt-4', model: 'deepseek-flash', source: 'fallback-after-error',
        error: '派生模型 gpt-4 失败（RuntimeError: 模型名不被接受）：已回退到母体模型 deepseek-flash',
        temperature: null,
      },
      role: { template: 'research', tier: 'full-system', source: 'role_template:research', fragment_chars: 120, constraints: 0, audit_required: true, red: true, error: '' },
    },
  ],
}

const postCalls: { url: string; body: Record<string, unknown> }[] = []

const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  const u = String(url)
  if (init?.method === 'POST') {
    let body: Record<string, unknown> = {}
    try { body = JSON.parse(String(init.body ?? '{}')) } catch { body = {} }
    postCalls.push({ url: u, body })
    return envelope({ ok: true })
  }
  if (u.includes('/api/subagent/list')) return envelope(LIST_PAYLOAD)
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines')) {
    if (/\/api\/agent-lines\/[^/?]+$/.test(u)) return json(DETAIL)
    return json({ ok: true, active: 'engineering', lines: [LINE], broken: [] })
  }
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const mod = await import('./agents')
const WorkshopAgents = mod.default
const { WORKSHOP_VIEWS, WORKSHOP_VIEW_STORAGE_KEY } = mod

beforeEach(() => {
  localStorage.clear()
  fetchMock.mockClear()
  postCalls.length = 0
})
afterEach(cleanup)

async function renderSettled(props: Record<string, unknown> = {}) {
  const view = render(<WorkshopAgents {...props} />)
  return view
}

describe('装配车间 · 分身创建与组装（三视图）', () => {
  it('三个视图 Tab 并列且顺序正确（分身 → 主线档案 → 主线 × 四面）', () => {
    render(<WorkshopAgents />)
    expect(screen.getByRole('tablist', { name: '分身装配视图' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      '分身', '主线档案', '主线 × 四面',
    ])
    expect(WORKSHOP_VIEWS.map((v) => v.id)).toEqual(['agents', 'lines', 'line-assembly'])
  })

  it('默认「分身」视图：渲染原有创建入口与分身列表（搬家没搬丢功能）', async () => {
    render(<WorkshopAgents />)
    expect(screen.getByRole('tab', { name: /^分身$/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('创建分身')).toBeInTheDocument()
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/subagent/list'))).toBe(true)
      expect(screen.getByText('alpha')).toBeInTheDocument()
    })
  })

  it('点「主线档案」⇒ 渲染迁入的主线档案视图（原工具调用→主线管理）', async () => {
    const { container } = await renderSettled()
    fireEvent.click(screen.getByRole('tab', { name: /主线档案/ }))
    expect(screen.getByRole('tab', { name: /主线档案/ })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => {
      expect(container.textContent).toContain('装配预览（实时）')
    })
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('lines')
  })

  it('点「主线 × 四面」⇒ 渲染迁入的总览视图（原零清单导航项）', async () => {
    const { container } = await renderSettled()
    fireEvent.click(screen.getByRole('tab', { name: /主线 × 四面/ }))
    await waitFor(() => {
      expect(container.querySelector('[data-testid="line-assembly-overview"]')).toBeTruthy()
    })
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('line-assembly')
  })

  it('记忆值合法 ⇒ 重开页面回到上次视图', async () => {
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'line-assembly')
    const { container } = await renderSettled()
    expect(screen.getByRole('tab', { name: /主线 × 四面/ })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => {
      expect(container.querySelector('[data-testid="line-assembly-overview"]')).toBeTruthy()
    })
  })

  it('记忆值非法 ⇒ 回落「分身」（不白屏）', async () => {
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'nope')
    render(<WorkshopAgents />)
    expect(screen.getByRole('tab', { name: /^分身$/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('创建分身')).toBeInTheDocument()
  })

  it('initialView 优先于记忆值', () => {
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'agents')
    render(<WorkshopAgents initialView="line-assembly" />)
    expect(screen.getByRole('tab', { name: /主线 × 四面/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('分身视图如实说明：工具集由主线档案决定；模型已接线、记忆/工具源仍是声明字段', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    expect(screen.getByText(/工具集由「主线档案」决定/)).toBeInTheDocument()
    expect(screen.getByText(/模型已接线/)).toBeInTheDocument()
    expect(screen.getByText(/记忆提供商 \/ 工具源仍是/)).toBeInTheDocument()
  })

  // ═══════════════════════════════════════════════════════════
  //  分身独立 LLM：表格显示"实际生效"，表单不再暗示"声明即生效"
  // ═══════════════════════════════════════════════════════════

  it('模型列显示**实际生效**的模型（不是配置里那串声明的名字）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    const cell = (name: string) =>
      screen.getByTestId(`subagent-llm-${name}`).textContent ?? ''
    expect(cell('alpha')).toContain('deepseek-v4-pro')
    expect(cell('alpha')).toContain('指定模型')
    // beta 配置里 model_id 是空串 ⇒ 显示母体当前模型，并标"跟随母体"
    expect(cell('beta')).toContain('deepseek-flash')
    expect(cell('beta')).toContain('跟随母体')
    // gamma 指定了 gpt-4 但没生效 ⇒ 显示母体模型 + 红档"指定未生效"（不许静默）
    expect(cell('gamma')).toContain('deepseek-flash')
    expect(cell('gamma')).toContain('指定未生效')
    expect(cell('gamma')).toContain('回退原因')
  })

  it('未生效那行把后端回退原因原样挂在 title 上', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('gamma')).toBeInTheDocument())
    const cell = screen.getByTestId('subagent-llm-gamma')
    const reason = cell.querySelector('[title]')
    expect(reason?.getAttribute('title')).toContain('派生模型 gpt-4 失败')
  })

  it('创建表单：模型候选来自后端（部署默认 + 已声明），placeholder 写明"留空=跟随母体"', async () => {
    const { container } = render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    const input = screen.getByTestId('subagent-model-input')
    expect(input.getAttribute('placeholder')).toContain('跟随母体')
    expect(input.getAttribute('placeholder')).toContain('deepseek-flash')
    const options = Array.from(container.querySelectorAll('#subagent-model-options option'))
      .map((o) => o.getAttribute('value'))
    expect(options).toEqual(['deepseek-flash', 'deepseek-v4-pro'])
  })

  it('模型留空创建 ⇒ POST model_id 为空串（"跟随母体"是真语义，不抄母体模型名）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'delta' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].url).toContain('/api/subagent/create')
    expect(postCalls[0].body.model_id).toBe('')
  })

  it('填了模型则按填的提交', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'epsilon' } })
    fireEvent.change(screen.getByTestId('subagent-model-input'), { target: { value: '  deepseek-v4-pro  ' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body.model_id).toBe('deepseek-v4-pro')
  })

  // ── 生成温度：表态才显示、表态才发送（"没表态" ≠ "0.0"）──────

  it('温度列：表态的显示 T=…，未表态的**不显示**（不把"没表态"说成"最确定性"）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    const beta = screen.getByTestId('subagent-llm-beta')
    expect(beta.querySelector('[data-testid="subagent-llm-temperature"]')?.textContent).toBe('T=0.25')
    const alpha = screen.getByTestId('subagent-llm-alpha')
    expect(alpha.querySelector('[data-testid="subagent-llm-temperature"]')).toBeNull()
  })

  it('温度留空创建 ⇒ **不发** llm_temperature 键（= 不干预执行器默认，而不是发 0）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'zeta' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect('llm_temperature' in postCalls[0].body).toBe(false)
  })

  it('温度填了则按数字提交（含 0 —— 0 是"要最确定性"，必须发出去）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'eta' } })
    fireEvent.change(screen.getByTestId('subagent-temperature-input'), { target: { value: '0' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body.llm_temperature).toBe(0)
  })

  // ═══════════════════════════════════════════════════════════
  //  角色（受控模板）：默认档/红档都在表里如实显示；自由文本必须显式选档才发送
  // ═══════════════════════════════════════════════════════════

  it('角色列显示生效模板与档位徽章（红档必须红）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    const cell = (name: string) => screen.getByTestId(`subagent-role-${name}`).textContent ?? ''
    // alpha：默认档（受控模板），显示模板**标题**而不是 id
    expect(cell('alpha')).toContain('代码审查')
    expect(cell('alpha')).toContain('受控模板')
    // beta：未装角色 ⇒ 明确写“未装”，不假装有角色
    expect(cell('beta')).toContain('未装')
    // gamma：full-system ⇒ 红档徽章（自由文本进系统提示词，不许静默）
    expect(cell('gamma')).toContain('资料调研')
    expect(cell('gamma')).toContain('红档')
  })

  it('角色模板候选来自后端词表（不是前端硬编码清单）', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    const select = screen.getByTestId('subagent-role-template') as HTMLSelectElement
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toEqual(['', 'generic_readonly', 'code_review', 'research'])
    const mode = screen.getByTestId('subagent-role-mode') as HTMLSelectElement
    expect(Array.from(mode.options).map((o) => o.value)).toEqual([
      'template', 'template+text', 'full-system',
    ])
  })

  it('角色留空创建 ⇒ 发空模板与默认档，且**不发** role_text 键', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'theta' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body.role_template).toBe('')
    expect(postCalls[0].body.role_mode).toBe('template')
    expect('role_text' in postCalls[0].body).toBe(false)
  })

  it('显式选 template+text 并填自由文本 ⇒ 三个角色字段都发出', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByText('创建分身'))
    fireEvent.change(screen.getByPlaceholderText('分身名称 *'), { target: { value: 'iota' } })
    fireEvent.change(screen.getByTestId('subagent-role-template'), { target: { value: 'code_review' } })
    fireEvent.change(screen.getByTestId('subagent-role-mode'), { target: { value: 'template+text' } })
    fireEvent.change(screen.getByTestId('subagent-role-text'), { target: { value: '  只审查不要改代码  ' } })
    fireEvent.click(screen.getByText('组装分身'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body.role_template).toBe('code_review')
    expect(postCalls[0].body.role_mode).toBe('template+text')
    expect(postCalls[0].body.role_text).toBe('只审查不要改代码')
  })
})
