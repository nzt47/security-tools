/**
 * 主线管理 —— 工具行「需确认」徽章 = 可点的豁免开关
 * ------------------------------------------------------------------
 * 需求：把只读的「需确认」徽章变成开关，且业务规则必须在 UI 上如实体现。
 * 锁死的不变量：
 *   1. `exemptable=true` ⇒ 点击先问放宽理由，再 POST /api/cp/tool-exemptions，
 *      并**以后端返回的 exempt 列表为真值**重绘（不是本地翻转）；
 *   2. `exemptable=false` ⇒ 不可点、不发请求，title 里给 blocked_reason（人要知道为什么点不动）；
 *   3. 后端拒绝（settings_denied / locked_by_env …）⇒ code + message 必须上屏，徽章不得假装已生效；
 *   4. 豁免接口整体不可用 ⇒ 本页主线档案 / 工具目录照常渲染，徽章退回静态标识。
 *
 * 页面自身会打三个接口（/api/agent-lines、/planes、/preview）与豁免接口，
 * 故 fetch 按 URL 路由打桩，只断言本需求相关的调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ═══════════════════════════════════════════════════════════
//  固定响应
// ═══════════════════════════════════════════════════════════

const PLANES = {
  ok: true,
  planes: [
    { key: 'resident', label: '常驻', hint: '', count: 0 },
    { key: 'perceive', label: '感知', hint: '', count: 1 },
    { key: 'act', label: '行动', hint: '', count: 2 },
    { key: 'govern', label: '治理', hint: '', count: 0 },
  ],
  plane_counts: {},
  effects: [],
  effect_order: ['read', 'write', 'execute', 'extend'],
  effect_note: '',
  risks: [],
  tools: [
    { name: 'write_file', category: '文件', plane: 'act', effect: 'write', risk: 'high', tags: [], needs_approval: true, internal: false },
    { name: 'read_file', category: '文件', plane: 'perceive', effect: 'read', risk: 'low', tags: [], needs_approval: false, internal: false },
    { name: 'shell_execute', category: '系统', plane: 'act', effect: 'execute', risk: 'critical', tags: [], needs_approval: true, internal: false },
    { name: 'delegate', category: '编排', plane: 'act', effect: 'extend', risk: 'medium', tags: [], needs_approval: true, internal: false },
  ],
  tool_count: 4,
  tools_without_declaration: [],
  tool_source: 'registry',
}

const LINE_PROFILE = {
  id: 'engineering',
  name: '工程线',
  description: '',
  enabled: true,
  plane_weights: { resident: 1, perceive: 1, act: 1 },
  plane_floors: { resident: 1, perceive: 1, act: 1 },
  boost: [], mute: [], tags: [],
  max_tools: 20,
  effect_allow: ['read', 'write', 'execute'],
  requires_approval: [],
  allow_govern: false,
  skills: [], prompt_note: '',
}

const LINE_LIST = { ok: true, active: 'engineering', lines: [LINE_PROFILE], broken: [] }

/** 预览（后端算）：面板只读它，字段给全以免面板少渲染 */
const PREVIEW = {
  ok: true,
  line_id: 'engineering',
  line: LINE_PROFILE,
  issues: [],
  notes: [],
  tool_source: 'registry',
  saved: true,
  preview: {
    line_id: 'engineering',
    tools: ['write_file', 'read_file', 'shell_execute'],
    count: 3,
    by_plane: { act: ['write_file', 'shell_execute'], perceive: ['read_file'] },
    denied_by_effect: [],
    denied_unknown: [],
    muted: [],
    truncated: [],
    reasons: {},
    needs_approval: ['write_file', 'shell_execute'],
    max_tools: 20,
    over_budget: false,
    tools_meta: {},
  },
}

const EXEMPTIONS = {
  ok: true,
  exempt: ['delegate'],
  source: 'ui_override',
  items: [
    { tool: 'write_file', level: 'L2', effect: 'write', risk: 'high', exempt: false, exemptable: true, blocked_reason: '' },
    { tool: 'delegate', level: 'L2', effect: 'extend', risk: 'medium', exempt: true, exemptable: true, blocked_reason: '' },
    { tool: 'shell_execute', level: 'L3', effect: 'execute', risk: 'critical', exempt: false, exemptable: false, blocked_reason: '描述符硬要求：执行任意命令必须每次人工确认' },
    { tool: 'read_file', level: 'L1', effect: 'read', risk: 'low', exempt: false, exemptable: true, blocked_reason: '' },
  ],
}

const LOCKED_REASON = '描述符硬要求：执行任意命令必须每次人工确认'

// ═══════════════════════════════════════════════════════════
//  fetch 打桩（按 URL 路由）
// ═══════════════════════════════════════════════════════════

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

/** GET /api/cp/tool-exemptions 的应答（用例可换成失败态） */
let exemptionsGet: () => Response = () => json(EXEMPTIONS)
/** POST /api/cp/tool-exemptions 的应答 */
let exemptionsPost: (body: Record<string, unknown>) => Response =
  (body) => json({ ok: true, changed: true, exempt: [String(body.tool)], outcome: { applied: true } })

const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
  const u = String(url)
  const method = String(init?.method ?? 'GET').toUpperCase()
  if (u.includes('/api/cp/tool-exemptions')) {
    if (method === 'POST') {
      return exemptionsPost(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>)
    }
    return exemptionsGet()
  }
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines/preview')) return json(PREVIEW)
  if (u.includes('/api/agent-lines')) return json(LINE_LIST)
  return json({})
})

const promptMock = vi.fn((_message?: string, _default?: string): string | null => '受控环境批量迁移')

const { default: ToolsAgentLines } = await import('./lines')

// ═══════════════════════════════════════════════════════════
//  查询助手
// ═══════════════════════════════════════════════════════════

const toggleOf = (c: HTMLElement, tool: string) =>
  c.querySelector<HTMLElement>(`[data-exempt-toggle="${tool}"]`)
const lockedOf = (c: HTMLElement, tool: string) =>
  c.querySelector<HTMLElement>(`[data-exempt-locked="${tool}"]`)
const unknownOf = (c: HTMLElement, tool: string) =>
  c.querySelector<HTMLElement>(`[data-exempt-unknown="${tool}"]`)

/** 只取 POST /api/cp/tool-exemptions 的调用（GET 同名端点不算） */
function postCalls() {
  return fetchMock.mock.calls.filter(([url, init]) => (
    String(url).includes('/api/cp/tool-exemptions')
    && String((init as RequestInit | undefined)?.method ?? '').toUpperCase() === 'POST'
  ))
}

function lastPostBody(): Record<string, unknown> {
  const calls = postCalls()
  const init = calls[calls.length - 1]?.[1] as RequestInit | undefined
  return JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
}

/** 渲染到「工具选择器已出来」为止（编辑器出现即代表 planes/主线都加载完） */
async function renderPage() {
  const utils = render(<ToolsAgentLines />)
  await waitFor(() => expect(toggleOf(utils.container, 'write_file')).toBeTruthy())
  return utils
}

beforeEach(() => {
  fetchMock.mockClear()
  promptMock.mockClear()
  exemptionsGet = () => json(EXEMPTIONS)
  exemptionsPost = (body) =>
    json({ ok: true, changed: true, exempt: [String(body.tool)], outcome: { applied: true } })
  vi.stubGlobal('fetch', fetchMock)
  window.prompt = promptMock as unknown as typeof window.prompt
})
afterEach(cleanup)

// ═══════════════════════════════════════════════════════════
//  用例
// ═══════════════════════════════════════════════════════════

describe('工具豁免开关（需确认徽章）', () => {
  it('exemptable 行：点击先问理由，POST 到 /api/cp/tool-exemptions，并按返回值重绘为「已豁免」', async () => {
    const { container } = await renderPage()
    // 初始：后端 exempt 列表里没有 write_file ⇒ 需确认
    expect(toggleOf(container, 'write_file')!.getAttribute('data-exempt')).toBe('required')

    fireEvent.click(toggleOf(container, 'write_file')!)

    await waitFor(() => expect(postCalls().length).toBe(1))
    expect(String(postCalls()[0][0])).toBe('/api/cp/tool-exemptions')
    expect(promptMock).toHaveBeenCalledTimes(1)
    expect(lastPostBody()).toEqual({
      tool: 'write_file', exempt: true, reason: '受控环境批量迁移',
    })

    // 两处选择器（boost / mute）都按真值同步
    await waitFor(() => {
      expect(container.querySelectorAll('[data-exempt-toggle="write_file"][data-exempt="exempt"]').length).toBe(2)
    })
  })

  it('exemptable=false 行：不可点、不发请求，title 给出 blocked_reason', async () => {
    const { container } = await renderPage()

    const locked = lockedOf(container, 'shell_execute')
    expect(locked).toBeTruthy()
    expect(locked!.tagName).toBe('SPAN')            // 不是按钮 ⇒ 点不动是设计如此
    expect(locked!.getAttribute('aria-disabled')).toBe('true')
    expect(locked!.getAttribute('title')).toContain(LOCKED_REASON)
    expect(toggleOf(container, 'shell_execute')).toBeNull()

    fireEvent.click(locked!)
    // 给事件循环一个机会（若真发了请求，这里必然能看到）
    await Promise.resolve()
    expect(postCalls().length).toBe(0)
    expect(lockedOf(container, 'shell_execute')).toBeTruthy()
  })

  it('后端拒绝时把 code + message 原样上屏，徽章保持「需确认」（不假装已生效）', async () => {
    exemptionsPost = () => json({
      ok: false,
      code: 'settings_denied',
      message: '该工具被开关中心策略拒绝：需要管理员在设置里放行',
    }, 403)

    const { container } = await renderPage()
    fireEvent.click(toggleOf(container, 'write_file')!)

    await waitFor(() => {
      expect(screen.getByText(/settings_denied/)).toBeInTheDocument()
    })
    expect(screen.getByText(/该工具被开关中心策略拒绝/)).toBeInTheDocument()
    // 徽章未被本地翻转
    expect(toggleOf(container, 'write_file')!.getAttribute('data-exempt')).toBe('required')
    // 豁免失败不得拖垮页面：工具目录（planes 数据）仍在
    expect(screen.getAllByText('read_file').length).toBeGreaterThan(0)
    expect(screen.getAllByText('shell_execute').length).toBeGreaterThan(0)
  })

  it('以后端返回的 exempt 列表为真值：返回空列表时连原本已豁免的工具也回到「需确认」', async () => {
    // 后端说"什么也没豁免"——与本次点击的意图相反，UI 必须听后端的
    exemptionsPost = () => json({ ok: true, changed: true, exempt: [], outcome: { note: 'policy_rolled_back' } })

    const { container } = await renderPage()
    expect(toggleOf(container, 'delegate')!.getAttribute('data-exempt')).toBe('exempt')

    fireEvent.click(toggleOf(container, 'write_file')!)
    await waitFor(() => expect(postCalls().length).toBe(1))

    await waitFor(() => {
      // 刚点的 write_file 没变成已豁免（本地翻转的话这里会变成 exempt）
      expect(toggleOf(container, 'write_file')!.getAttribute('data-exempt')).toBe('required')
      // 且被返回列表"取消"的 delegate 也同步回需确认 ⇒ 证明确实整体按真值重绘
      expect(toggleOf(container, 'delegate')!.getAttribute('data-exempt')).toBe('required')
    })
  })

  it('收紧（exempt:false）不问理由，请求体不带 reason', async () => {
    exemptionsPost = () => json({ ok: true, changed: true, exempt: [], outcome: {} })
    const { container } = await renderPage()
    expect(toggleOf(container, 'delegate')!.getAttribute('data-exempt')).toBe('exempt')

    fireEvent.click(toggleOf(container, 'delegate')!)
    await waitFor(() => expect(postCalls().length).toBe(1))

    expect(promptMock).not.toHaveBeenCalled()
    expect(lastPostBody()).toEqual({ tool: 'delegate', exempt: false })
  })

  it('changed=false（例如已在豁免名单里）不算失败：原话上屏、用中性提示，徽章仍按返回列表', async () => {
    exemptionsPost = () => json({
      ok: true,
      changed: false,
      exempt: ['write_file'],
      message: 'write_file 已在豁免名单里（未重复写入）',
      source: 'ui_override',
      source_label: '开关中心覆盖层',
      env_locked: false,
      items: EXEMPTIONS.items.map((it) => (it.tool === 'write_file' ? { ...it, exempt: true } : it)),
    })

    const { container } = await renderPage()
    fireEvent.click(toggleOf(container, 'write_file')!)

    await waitFor(() => expect(postCalls().length).toBe(1))
    await waitFor(() => {
      expect(screen.getByText(/write_file 已在豁免名单里（未重复写入）/)).toBeInTheDocument()
    })
    // 中性提示（amber）而不是红框报错：后端没改是因为本来就不需要改
    expect(screen.getByText(/changed=false/)).toBeInTheDocument()
    expect(screen.queryByText(/放宽失败/)).toBeNull()
    // 徽章按返回的 exempt 列表（含 write_file）重绘
    expect(toggleOf(container, 'write_file')!.getAttribute('data-exempt')).toBe('exempt')
    // POST 回包里自带整套视图 ⇒ 来源口径也跟着刷新
    expect(screen.getByText(/开关中心覆盖层/)).toBeInTheDocument()
  })

  it('环境变量锁定：状态条明说"改了也会被判 locked_by_env"', async () => {
    exemptionsGet = () => json({ ...EXEMPTIONS, source: 'env', source_label: '', env_locked: true })

    render(<ToolsAgentLines />)
    await waitFor(() => {
      expect(screen.getByText(/该开关由环境变量锁定/)).toBeInTheDocument()
    })
    expect(screen.getByText(/locked_by_env/)).toBeInTheDocument()
    // 本地表回落：后端没给 source_label 时显示"环境变量"
    expect(screen.getByText(/来源：环境变量/)).toBeInTheDocument()
  })

  it('用户取消理由输入 ⇒ 不提交', async () => {
    promptMock.mockReturnValueOnce(null)
    const { container } = await renderPage()

    fireEvent.click(toggleOf(container, 'write_file')!)
    await Promise.resolve()
    expect(postCalls().length).toBe(0)
    expect(toggleOf(container, 'write_file')!.getAttribute('data-exempt')).toBe('required')
  })

  it('豁免接口不可用：错误上屏，徽章退回静态标识，本页其余数据照常渲染', async () => {
    exemptionsGet = () => json({ ok: false, code: 'not_found', message: '豁免端点未上线' }, 404)

    // 这条路径下没有开关可等（退化成静态徽章），故不能用 renderPage 的等待条件
    const { container } = render(<ToolsAgentLines />)
    await waitFor(() => {
      expect(screen.getByText(/工具豁免状态读取失败/)).toBeInTheDocument()
    })
    expect(screen.getByText(/not_found/)).toBeInTheDocument()
    expect(screen.getByText(/豁免端点未上线/)).toBeInTheDocument()
    // 静态徽章（不是开关）
    expect(unknownOf(container, 'write_file')).toBeTruthy()
    expect(toggleOf(container, 'write_file')).toBeNull()
    // 主线档案与工具目录照常
    expect(screen.getAllByText('engineering').length).toBeGreaterThan(0)
    expect(screen.getAllByText('read_file').length).toBeGreaterThan(0)
  })
})
