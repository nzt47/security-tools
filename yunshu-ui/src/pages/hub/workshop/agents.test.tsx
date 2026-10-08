/**
 * 装配车间 —— 四视图容器守卫（2026-10-08 组装台并入后）
 * ------------------------------------------------------------------
 * 四个视图：① 分身 / ② 组装台 / ③ 主线档案 / ④ 主线 × 四面。
 * 锁死：四 Tab 顺序、点击即渲染、localStorage 记忆、非法值回落；
 * 以及"创建入口收敛到组装台"（本页不再有创建表单）。
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
  planes: [{ key: 'act', label: '行动', hint: '', count: 1 }],
  plane_counts: {}, effects: [], effect_order: ['read'], effect_note: '', risks: [],
  tools: [{ name: 'read_file', category: '文件', plane: 'act', effect: 'read', risk: 'low', tags: [], needs_approval: false, internal: false }],
  tool_count: 1, tools_without_declaration: [], tool_source: 'registry', defaults: {},
}
const DETAIL = {
  ok: true, line: LINE,
  preview: { line_id: 'engineering', tools: ['read_file'], count: 1, by_plane: { act: ['read_file'] }, denied_by_effect: [], denied_unknown: [], muted: [], truncated: [], reasons: {}, needs_approval: [], max_tools: 20, over_budget: false, tools_meta: {} },
  issues: [], tool_source: 'registry',
}

function json(body: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response
}
function envelope(data: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: (k: string) => (k === 'X-Envelope' ? 'v2' : null) }, json: async () => ({ code: 200, data }), text: async () => JSON.stringify({ code: 200, data }) } as unknown as Response
}

const LIST_PAYLOAD = {
  ok: true, count: 3, channel: {},
  llm: { model: 'deepseek-flash', provider: 'deepseek', options: [{ model: 'deepseek-flash', source: 'deployment' }] },
  role: { default_tier: 'template', role_text_max_chars: 2000, templates: [{ id: 'code_review', title: '代码审查', body: '…', note: '' }, { id: 'research', title: '资料调研', body: '…', note: '' }], tiers: [{ value: 'template', label: '受控模板', red: false, audit: false }, { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true, audit: true }] },
  subagents: [
    { name: 'alpha', model_id: 'deepseek-v4-pro', memory_provider: 'holographic', status: 'running', llm: { requested: 'deepseek-v4-pro', model: 'deepseek-v4-pro', source: 'explicit', error: '', temperature: null }, role: { template: 'code_review', tier: 'template', source: 'role_template:code_review', fragment_chars: 78, constraints: 0, audit_required: false, red: false, error: '' } },
    { name: 'beta', model_id: '', memory_provider: 'holographic', status: 'idle', llm_temperature: 0.25, llm: { requested: '', model: 'deepseek-flash', source: 'inherit', error: '', temperature: 0.25 }, role: { template: '', tier: 'template', source: '', fragment_chars: 0, constraints: 0, audit_required: false, red: false, error: '' } },
    { name: 'gamma', model_id: 'gpt-4', memory_provider: 'holographic', status: 'idle', llm: { requested: 'gpt-4', model: 'deepseek-flash', source: 'fallback-after-error', error: '派生模型 gpt-4 失败（RuntimeError: 模型名不被接受）：已回退到母体模型 deepseek-flash', temperature: null }, role: { template: 'research', tier: 'full-system', source: 'role_template:research', fragment_chars: 120, constraints: 0, audit_required: true, red: true, error: '' } },
  ],
}

/** 主权清单（普通 JSON）：字段与 capacities 投影同形，这里只需最小可用集 */
const CAPABILITIES = {
  ok: true,
  capabilities: {
    summary: { owned: 1, partial: 1, missing: 0, total: 2 },
    maturity: { current: 'L2', levels: [{ key: 'L2', label: '拥有型', note: '' }] },
    layers: [{ key: 'identity', label: '主体', question: '它是谁', faces: ['role'] }, { key: 'memory', label: '记忆', question: '记得什么', faces: ['memory_scope'] }],
    faces: [
      { key: 'role', layer: 'identity', label: '角色与人格', question: '它是谁', state: 'owned', state_label: '拥有', evidence: '受控角色模板词表', evidence_files: ['agent/subagent/role_templates.py'], gap: '', next_stage: '' },
      { key: 'memory_scope', layer: 'memory', label: '记忆域与档位', question: '记得什么', state: 'partial', state_label: '声明未接线', evidence: 'tenancy 地基在', evidence_files: ['agent/memory/tenancy.py'], gap: 'memory_provider 无消费方', next_stage: 'S3' },
    ],
  },
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
  if (u.includes('/api/subagent/capabilities')) return json(CAPABILITIES)
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

beforeEach(() => { localStorage.clear(); fetchMock.mockClear(); postCalls.length = 0 })
afterEach(cleanup)

describe('装配车间 · 四视图容器', () => {
  it('四个视图 Tab 并列且顺序正确', () => {
    render(<WorkshopAgents />)
    expect(screen.getByRole('tablist', { name: '分身装配视图' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['分身', '组装台', '主线档案', '主线 × 四面'])
    expect(WORKSHOP_VIEWS.map((v) => v.id)).toEqual(['agents', 'assembly', 'lines', 'line-assembly'])
  })

  it('默认「分身」视图：只列分身 + 前往组装台（无创建表单）', async () => {
    render(<WorkshopAgents />)
    expect(screen.getByRole('tab', { name: /^分身$/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('go-assembly')).toBeInTheDocument()
    expect(screen.queryByText('创建分身')).toBeNull()
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/subagent/list'))).toBe(true)
      expect(screen.getByText('alpha')).toBeInTheDocument()
    })
  })

  it('点「组装台」⇒ 渲染组装台与主权清单', async () => {
    render(<WorkshopAgents />)
    fireEvent.click(screen.getByRole('tab', { name: /^组装台$/ }))
    await waitFor(() => {
      expect(screen.getByTestId('assembly-preview')).toBeInTheDocument()
      expect(screen.getByTestId('sovereignty-panel')).toBeInTheDocument()
    })
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('assembly')
  })

  it('「前往组装台」按钮切到组装台', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('go-assembly'))
    await waitFor(() => expect(screen.getByTestId('assembly-preview')).toBeInTheDocument())
  })

  it('点「主线档案」⇒ 渲染主线档案视图', async () => {
    const { container } = render(<WorkshopAgents />)
    fireEvent.click(screen.getByRole('tab', { name: /主线档案/ }))
    await waitFor(() => expect(container.textContent).toContain('装配预览（实时）'))
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('lines')
  })

  it('点「主线 × 四面」⇒ 渲染总览视图', async () => {
    const { container } = render(<WorkshopAgents />)
    fireEvent.click(screen.getByRole('tab', { name: /主线 × 四面/ }))
    await waitFor(() => expect(container.querySelector('[data-testid="line-assembly-overview"]')).toBeTruthy())
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('line-assembly')
  })

  it('记忆值合法 ⇒ 重开回到上次视图；非法 ⇒ 回落「分身」', async () => {
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'assembly')
    render(<WorkshopAgents />)
    expect(screen.getByRole('tab', { name: /^组装台$/ })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(screen.getByTestId('assembly-preview')).toBeInTheDocument())
    cleanup()
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'nope')
    render(<WorkshopAgents />)
    expect(screen.getByRole('tab', { name: /^分身$/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('initialView 优先于记忆值', () => {
    localStorage.setItem(WORKSHOP_VIEW_STORAGE_KEY, 'agents')
    render(<WorkshopAgents initialView="line-assembly" />)
    expect(screen.getByRole('tab', { name: /主线 × 四面/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('分身视图如实说明：创建去组装台；模型/角色已接线，记忆/工具源仍是声明字段', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    expect(screen.getByText(/装配一个分身请到「组装台」/)).toBeInTheDocument()
    expect(screen.getByText(/模型已接线/)).toBeInTheDocument()
    expect(screen.getByText(/记忆提供商 \/ 工具源仍是/)).toBeInTheDocument()
  })

  it('模型列显示实际生效 + 角色列显示模板与档位徽章', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    const cell = (name: string) => screen.getByTestId(`subagent-llm-${name}`).textContent ?? ''
    expect(cell('alpha')).toContain('deepseek-v4-pro')
    expect(cell('alpha')).toContain('指定模型')
    expect(cell('beta')).toContain('跟随母体')
    expect(cell('gamma')).toContain('指定未生效')
    const role = (name: string) => screen.getByTestId(`subagent-role-${name}`).textContent ?? ''
    expect(role('alpha')).toContain('代码审查')
    expect(role('alpha')).toContain('受控模板')
    expect(role('beta')).toContain('未装')
    expect(role('gamma')).toContain('资料调研')
    expect(role('gamma')).toContain('红档')
  })

  it('未生效那行把后端回退原因原样挂在 title 上', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('gamma')).toBeInTheDocument())
    const cell = screen.getByTestId('subagent-llm-gamma')
    expect(cell.querySelector('[title]')?.getAttribute('title')).toContain('派生模型 gpt-4 失败')
  })
})