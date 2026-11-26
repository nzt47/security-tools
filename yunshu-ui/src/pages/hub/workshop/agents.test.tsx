/**
 * 装配车间 —— 五视图容器守卫（2026-10-08 组装台并入；2026-10-15 系统自我复制并入）
 * ------------------------------------------------------------------
 * 五个视图：① 分身 / ② 组装台 / ③ 带走·自复制 / ④ 主线档案 / ⑤ 主线 × 四面。
 * 锁死：四 Tab 顺序、点击即渲染、localStorage 记忆、非法值回落；
 * 以及"创建入口收敛到组装台"（本页不再有创建表单）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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
  role: {
    default_tier: 'template', role_text_max_chars: 2000,
    templates: [{ id: 'code_review', title: '代码审查', body: '…', note: '' }, { id: 'research', title: '资料调研', body: '…', note: '' }],
    tiers: [
      { value: 'template', label: '受控模板', red: false, audit: false },
      { value: 'template+text', label: '模板 + 自由文本（进约束）', red: false, audit: true },
      { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true, audit: true },
    ],
    // 方案库：编辑角色弹窗的"一键填入"（与组装台同源，只有 code_review 有）
    prompt_sets: [{ role_id: 'code_review', role_title: '代码审查', sets: [{ id: 'cr-strict', title: '严格审查', scene: '只看 diff', body: '你是严格审查者：逐项给出位置/问题/依据/建议。', chars: 26 }] }],
  },
  subagents: [
    { name: 'alpha', model_id: 'deepseek-v4-pro', memory_provider: 'holographic', status: 'running', llm: { requested: 'deepseek-v4-pro', model: 'deepseek-v4-pro', source: 'explicit', error: '', temperature: null }, role: { template: 'code_review', tier: 'template', source: 'role_template:code_review', fragment_chars: 78, constraints: 0, audit_required: false, red: false, error: '' } },
    { name: 'beta', model_id: '', memory_provider: 'holographic', status: 'idle', llm_temperature: 0.25, llm: { requested: '', model: 'deepseek-flash', source: 'inherit', error: '', temperature: 0.25 }, role: { template: '', tier: 'template', source: '', fragment_chars: 0, constraints: 0, audit_required: false, red: false, error: '' } },
    { name: 'gamma', model_id: 'gpt-4', memory_provider: 'holographic', status: 'idle', llm: { requested: 'gpt-4', model: 'deepseek-flash', source: 'fallback-after-error', error: '派生模型 gpt-4 失败（RuntimeError: 模型名不被接受）：已回退到母体模型 deepseek-flash', temperature: null }, role: { template: 'research', tier: 'full-system', source: 'role_template:research', fragment_chars: 120, constraints: 0, audit_required: true, red: true, error: '' } },
    // 词表改过名 / 旧记录：模板 id 不在当前目录里，编辑弹窗必须原样保留（不能悄悄卸角色）
    { name: 'delta', model_id: '', memory_provider: 'default', status: 'idle', llm: { requested: '', model: 'deepseek-flash', source: 'inherit', error: '', temperature: null }, role: { template: 'legacy_role', tier: 'template', source: 'role_template:legacy_role', fragment_chars: 0, constraints: 0, audit_required: false, red: false, error: '未知角色模板 legacy_role' } },
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
/** 运行中列表（默认空；只有「运行中」用例会填） */
let runRows: Array<Record<string, unknown>> = []
/** Team 任务板（默认空；只有「任务板」用例会填） */
let boardRows: Array<Record<string, unknown>> = []
/** 下一次认领是否回 409（验证冲突要显示出来，不能吞） */
let boardConflict = false
/** 下一批 reload 请求是否要回 400（验证"业务拒绝不关弹窗"） */
let failReload = false
const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  const u = String(url)
  if (init?.method === 'POST') {
    let body: Record<string, unknown> = {}
    try { body = JSON.parse(String(init.body ?? '{}')) } catch { body = {} }
    postCalls.push({ url: u, body })
    if (failReload && u.includes('/reload')) return json({ ok: false, error: 'role_text 超长（2001 > 2000 字符）' }, 400)
    if (boardConflict && u.includes('/api/subagent/board/claim')) {
      return json({ ok: false, code: 'task_claimed', error: '任务已被 alice 认领（租约到 12:00）' }, 409)
    }
    return envelope({ ok: true })
  }
  if (u.includes('/api/subagent/list')) return envelope(LIST_PAYLOAD)
  if (u.includes('/api/subagent/capabilities')) return json(CAPABILITIES)
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines')) {
    if (/\/api\/agent-lines\/[^/?]+$/.test(u)) return json(DETAIL)
    return json({ ok: true, active: 'engineering', lines: [LINE], broken: [] })
  }
  if (u.includes('/api/subagent/runs')) {
    return json({ ok: true, count: runRows.length, running: runRows.length, runs: runRows })
  }
  if (u.includes('/api/subagent/board')) {
    const counts: Record<string, number> = { pending: 0, running: 0, done: 0, failed: 0, superseded: 0 }
    for (const row of boardRows) {
      const key = String(row.status || 'pending')
      counts[key] = (counts[key] || 0) + 1
    }
    return json({ ok: true, records: boardRows, counts, count: boardRows.length, total: boardRows.length })
  }
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const mod = await import('./agents')
const WorkshopAgents = mod.default
const { WORKSHOP_VIEWS, WORKSHOP_VIEW_STORAGE_KEY } = mod

beforeEach(() => {
  localStorage.clear(); fetchMock.mockClear(); postCalls.length = 0
  failReload = false; runRows = []; boardRows = []; boardConflict = false
})
afterEach(cleanup)

describe('装配车间 · 五视图容器', () => {
  it('五个视图 Tab 并列且顺序正确', () => {
    render(<WorkshopAgents />)
    expect(screen.getByRole('tablist', { name: '分身装配视图' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(
      ['分身', '组装台', '带走 / 自复制', '主线档案', '主线 × 四面'],
    )
    expect(WORKSHOP_VIEWS.map((v) => v.id)).toEqual(
      ['agents', 'assembly', 'replicate', 'lines', 'line-assembly'],
    )
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

  it('点「带走 / 自复制」⇒ 渲染 bundle 导出视图（原独立叶子「系统自我复制」已并入）', async () => {
    render(<WorkshopAgents />)
    fireEvent.click(screen.getByRole('tab', { name: /带走/ }))
    await waitFor(() => expect(screen.getByTestId('replicate-export')).toBeInTheDocument())
    expect(localStorage.getItem(WORKSHOP_VIEW_STORAGE_KEY)).toBe('replicate')
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
    expect(screen.getByText(/工具源（tool_sources）仍是/)).toBeInTheDocument()
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

// ═══════════════════════════════════════════════════════════
//  编辑已有分身的角色（2026-11-22）
// ------------------------------------------------------------------
// 口径：复用 POST /api/subagent/<name>/reload（不新增写路由）；
// 正文不回显 ⇒ "留空 = 保留" 与 "显式清空" 必须是两种请求体；
// 红档 full-system 必须先确认（确认前一个请求都不许发）。
// ═══════════════════════════════════════════════════════════
describe('分身 · 编辑角色', () => {
  const openEditor = async (name: string) => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText(name)).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('subagent-edit-role-' + name))
    await waitFor(() => expect(screen.getByTestId('role-editor')).toBeInTheDocument())
  }
  const selectValue = (testId: string) => (screen.getByTestId(testId) as HTMLSelectElement).value
  const textarea = () => screen.getByTestId('role-editor-text') as HTMLTextAreaElement

  it('入口在行操作里；打开时按当前生效角色预填模板与档位', async () => {
    await openEditor('alpha')
    expect(selectValue('role-editor-template')).toBe('code_review')
    expect(selectValue('role-editor-mode')).toBe('template')
    expect(screen.getByTestId('role-editor-current').textContent).toContain('代码审查')
  })

  it('正文不回显：留空 + 非默认档 ⇒ 请求体不带 role_text（后端沿用现值）', async () => {
    await openEditor('alpha')
    fireEvent.change(screen.getByTestId('role-editor-mode'), { target: { value: 'template+text' } })
    fireEvent.click(screen.getByTestId('role-editor-save'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].url).toContain('/api/subagent/alpha/reload')
    expect(postCalls[0].body).toEqual({ role_template: 'code_review', role_mode: 'template+text' })
    await waitFor(() => expect(screen.queryByTestId('role-editor')).toBeNull())
  })

  it('切回受控模板档 ⇒ 显式发空串（否则默认档配旧正文会被后端 400，永远切不回去）', async () => {
    await openEditor('alpha')
    fireEvent.change(screen.getByTestId('role-editor-mode'), { target: { value: 'template+text' } })
    fireEvent.change(screen.getByTestId('role-editor-mode'), { target: { value: 'template' } })
    fireEvent.click(screen.getByTestId('role-editor-save'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body).toEqual({ role_template: 'code_review', role_mode: 'template', role_text: '' })
  })

  it('勾「清空现有自由文本」⇒ 发空串（用户显式表态，不靠留空猜）', async () => {
    await openEditor('alpha')
    fireEvent.change(screen.getByTestId('role-editor-mode'), { target: { value: 'template+text' } })
    fireEvent.click(screen.getByTestId('role-editor-clear'))
    fireEvent.click(screen.getByTestId('role-editor-save'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body).toEqual({ role_template: 'code_review', role_mode: 'template+text', role_text: '' })
  })

  it('输入正文（方案模板一键填入）⇒ 带 role_text，且默认档自动抬到 template+text', async () => {
    await openEditor('alpha')
    const body = '你是严格审查者：逐项给出位置/问题/依据/建议。'
    fireEvent.click(screen.getByTestId('role-editor-set-cr-strict'))
    expect(textarea().value).toBe(body)
    expect(selectValue('role-editor-mode')).toBe('template+text')
    expect(screen.getByTestId('role-editor-count').textContent).toContain(String(body.length))
    fireEvent.click(screen.getByTestId('role-editor-save'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body).toEqual({ role_template: 'code_review', role_mode: 'template+text', role_text: body })
  })

  it('红档 full-system：先二次确认；取消 ⇒ 一个请求都不发，确认 ⇒ 才提交', async () => {
    await openEditor('alpha')
    fireEvent.change(screen.getByTestId('role-editor-mode'), { target: { value: 'full-system' } })
    fireEvent.click(screen.getByTestId('role-editor-save'))
    const confirm = await screen.findByRole('dialog', { name: '红档：自由文本进系统提示词' })
    expect(postCalls.length).toBe(0)
    fireEvent.click(within(confirm).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '红档：自由文本进系统提示词' })).toBeNull())
    expect(postCalls.length).toBe(0)

    fireEvent.click(screen.getByTestId('role-editor-save'))
    fireEvent.click(within(await screen.findByRole('dialog', { name: '红档：自由文本进系统提示词' })).getByRole('button', { name: '保存为红档' }))
    await waitFor(() => expect(postCalls.length).toBe(1))
    // 红档 + 正文留空 ⇒ 同样"不发 role_text"（保留现有正文；要清空得单独勾）
    expect(postCalls[0].body).toEqual({ role_template: 'code_review', role_mode: 'full-system' })
  })

  it('后端 400 ⇒ 原样显示原因且弹窗不关（不假成功）', async () => {
    failReload = true
    await openEditor('alpha')
    fireEvent.click(screen.getByTestId('role-editor-save'))
    const err = await screen.findByTestId('role-editor-error')
    expect(err.textContent).toContain('role_text 超长')
    expect(screen.getByTestId('role-editor')).toBeInTheDocument()
  })

  it('目录外模板 id 原样保留（不显示成空、不悄悄卸角色）', async () => {
    await openEditor('delta')
    expect(selectValue('role-editor-template')).toBe('legacy_role')
    expect(screen.getByTestId('role-editor-out-of-catalog').textContent).toContain('目录外')
    const values = Array.from((screen.getByTestId('role-editor-template') as HTMLSelectElement).options).map((o) => o.value)
    expect(values).toContain('legacy_role')
    fireEvent.click(screen.getByTestId('role-editor-save'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].body).toEqual({ role_template: 'legacy_role', role_mode: 'template', role_text: '' })
  })

  it('未装角色的分身：模板空 + 正文框禁用（没有可依附的受控来源）', async () => {
    await openEditor('beta')
    expect(selectValue('role-editor-template')).toBe('')
    expect(textarea()).toBeDisabled()
    expect(screen.getByTestId('role-editor-text-hint').textContent).toContain('未装角色')
  })
})

// ═══════════════════════════════════════════════════════════
//  运行中控制（E2-1b）
// ------------------------------------------------------------------
// 口径：send 不打断当前轮（下一轮生效）；interrupt 是协作式（不是硬杀）；
// 关闭开关时后端 404 ⇒ 整块隐藏，不把"未启用"报成错误。
// ═══════════════════════════════════════════════════════════
describe('分身 · 运行中控制', () => {
  const RUN = {
    run_id: 'r-abc123', subagent: 'alpha', delegation_id: 'dlg-1', status: 'running',
    rounds: 2, elapsed_s: 3.6, last_output: '正在读 plugins/chat.py',
  }

  it('没有 run 时给一行提示，不占一张空表', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('runs-empty')).toBeInTheDocument())
    expect(screen.queryByTestId('running-runs')).toBeNull()
  })

  it('有 run 时列出 run_id/状态/轮次，并能追加指令（下一轮生效）', async () => {
    runRows = [RUN]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('run-row-r-abc123')).toBeInTheDocument())
    expect(screen.getByTestId('run-row-r-abc123').textContent).toContain('alpha')
    expect(screen.getByText('r-abc123')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('run-input-r-abc123'), { target: { value: '收窄到 plugins/chat.py' } })
    fireEvent.click(screen.getByTestId('run-send-r-abc123'))
    await waitFor(() => expect(postCalls.some((c) => c.url.includes('/api/subagent/runs/r-abc123/message'))).toBe(true))
    const call = postCalls.find((c) => c.url.includes('/message'))!
    expect(call.body).toEqual({ message: '收窄到 plugins/chat.py' })
  })

  it('打断发到 interrupt 端点（带原因），结束后整行禁用', async () => {
    runRows = [RUN]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('run-interrupt-r-abc123')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('run-interrupt-r-abc123'))
    await waitFor(() => expect(postCalls.some((c) => c.url.includes('/api/subagent/runs/r-abc123/interrupt'))).toBe(true))
    expect(postCalls.find((c) => c.url.includes('/interrupt'))!.body).toEqual({ reason: '界面打断' })

    cleanup()
    runRows = [{ ...RUN, status: 'interrupted' }]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('run-row-r-abc123')).toBeInTheDocument())
    expect(screen.getByTestId('run-send-r-abc123')).toBeDisabled()
    expect(screen.getByTestId('run-interrupt-r-abc123')).toBeDisabled()
  })
})

// ═══════════════════════════════════════════════════════════
//  Team 任务板（E2-2）
// ------------------------------------------------------------------
// 口径：认领/释放/交接都是写端点；冲突（他人持有租约）必须如实显示成错误；
// 非持有者的释放/交接按钮 disabled（服务端仍是最终裁判）。
// ═══════════════════════════════════════════════════════════
describe('分身 · Team 任务板', () => {
  const TASK = {
    task_id: 'dlg-task-1', status: 'pending', goal: '把设计稿拆成步骤', owner: '',
    version: 0, round: 1,
  }

  it('没有任务时给一行空态', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-empty')).toBeInTheDocument())
    expect(screen.getByTestId('team-board')).toBeInTheDocument()
  })

  it('列出任务与状态计数，并显示认领者', async () => {
    boardRows = [{ ...TASK, owner: 'alice', version: 2 }]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-row-dlg-task-1')).toBeInTheDocument())
    expect(screen.getByTestId('board-row-dlg-task-1').textContent).toContain('把设计稿拆成步骤')
    expect(screen.getByTestId('board-owner-dlg-task-1').textContent).toContain('alice')
    expect(screen.getByTestId('board-counts').textContent).toContain('待办 1')
  })

  it('认领默认用顶部身份，成功后发到 claim 端点', async () => {
    boardRows = [{ ...TASK }]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-claim-dlg-task-1')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('board-me'), { target: { value: 'bob' } })
    fireEvent.click(screen.getByTestId('board-claim-dlg-task-1'))
    await waitFor(() => expect(postCalls.some((c) => c.url.includes('/api/subagent/board/claim'))).toBe(true))
    const call = postCalls.find((c) => c.url.includes('/board/claim'))!
    expect(call.body).toEqual({ task_id: 'dlg-task-1', owner: 'bob' })
    expect(localStorage.getItem('yunshu.team.owner')).toBe('bob')
  })

  it('他人已认领时认领/释放按钮禁用', async () => {
    boardRows = [{ ...TASK, owner: 'alice' }]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-claim-dlg-task-1')).toBeInTheDocument())
    expect(screen.getByTestId('board-claim-dlg-task-1')).toBeDisabled()
    expect(screen.getByTestId('board-release-dlg-task-1')).toBeDisabled()
  })

  it('冲突 409 要显示成错误（不吞）', async () => {
    boardRows = [{ ...TASK, owner: '' }]
    boardConflict = true
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-claim-dlg-task-1')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('board-claim-dlg-task-1'))
    await waitFor(() => expect(screen.getByText(/任务已被 alice 认领/)).toBeInTheDocument())
  })

  it('持有者交接：输入目标人后发到 handoff 端点', async () => {
    boardRows = [{ ...TASK, owner: '我', version: 1 }]
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByTestId('board-handoff-dlg-task-1')).toBeInTheDocument())
    expect(screen.getByTestId('board-release-dlg-task-1')).not.toBeDisabled()
    fireEvent.change(screen.getByTestId('board-handoff-input-dlg-task-1'), { target: { value: 'carol' } })
    fireEvent.click(screen.getByTestId('board-handoff-dlg-task-1'))
    await waitFor(() => expect(postCalls.some((c) => c.url.includes('/board/handoff'))).toBe(true))
    expect(postCalls.find((c) => c.url.includes('/board/handoff'))!.body).toEqual(
      { task_id: 'dlg-task-1', to: 'carol', owner: '我' },
    )
  })
})