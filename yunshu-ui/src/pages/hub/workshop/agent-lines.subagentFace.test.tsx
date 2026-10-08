/**
 * 主线管理 —— 装配预览的「分身面」（第 4 面）区块
 * ------------------------------------------------------------------
 * 背景：主线档案要"四面共用"（工具 / 技能 / 提示词 / 分身），前三面在
 * `POST /api/agent-lines/preview` 里已有可视化口径，**分身面此前完全没有 UI** ——
 * `agent/subagent/assembly.py::resolve_subagent_assembly` 是纯函数，唯一消费方是派发路径
 * `agent/tools/fan_out_tools.py`（塞进 `DelegationContext.metadata`），没有任何读端点。
 * 后端已在 `GET /api/agent-lines/<id>` 与 `POST /api/agent-lines/preview` 的载荷里
 * 新增 `subagent_assembly`（复用同一个装配单入口）。本文件锁死的不变量：
 *   1. 前端**只渲染后端给的字段**，不自己重算"分身能拿到什么"
 *      （纯函数 readSubagentAssembly 单测钉住：只做形状规范化，不产生判定）；
 *   2. 装配单成立 ⇒ 显示允许的工具集 + 被拒/硬禁的人读说明 + 需人工确认；
 *   3. 点名的线装不上（available=false）⇒ 显示失败原因，且**不显示工具集**
 *      （fail-closed：把它画成"0 个工具"与"装不上"是两回事）；
 *   4. 旧后端没有该字段 ⇒ 整块不渲染，行为与本页接上它之前完全一致；
 *   5. 异形载荷（缺 available 布尔标志）按"没给"处理 —— 宁可不说，也不说错。
 *
 * 页面自身会打三个接口（/api/agent-lines、/planes、/preview），故 fetch 按 URL 打桩；
 * 查询一律走 data-testid，避免同一文案在多处出现时 query 命中多元素而假红。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'

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
  skills: [],
  prompt_note: '',
}

/** 后端「分身装配单」投影（agent/server_routes/routes_agent_lines.py::_subagent_assembly_payload） */
const ASSEMBLY_OK = {
  available: true,
  mode: 'line',
  semantics: 'named-resolved',
  semantics_note: '点名主线已装配：去 govern 平面、去 §5.7 机制 3 硬禁（子代理不含记忆读写）。',
  line_id: 'engineering',
  tools: ['read_file', 'grep', 'list_directory'],
  needs_approval: ['shell_execute'],
  note: "沿主线 engineering 装配（3 个工具）；另剔除 1 个 §5.7 机制 3 硬禁工具（子代理不含记忆读写）：['remember']",
  skills: ['engineering-test-delivery'],
  skills_mode: 'whitelist',
  skills_note: '白名单：本线声明的技能里 1 项可下发给分身。',
  prompt_note: '本线的交付标准是「改完并验证」。',
  prompt_source: 'line:engineering',
  reason: '',
}

/** 点名了但档案不存在 / 已停用 ⇒ fail-closed（不回退成全量授权） */
const ASSEMBLY_UNAVAILABLE = {
  ...ASSEMBLY_OK,
  available: false,
  mode: 'unavailable',
  semantics: 'named-but-unavailable',
  semantics_note: '点了名却装不上 ⇒ 派发时该任务就地失败（E_FAN_OUT_LINE_UNAVAILABLE），**绝不回退成全量授权**（fail-closed）。',
  reason: '主线不存在: __no_such_line__（未回退成全量授权）',
  tools: [],
  needs_approval: [],
  note: '',
}

const PLANES = {
  ok: true,
  planes: [
    { key: 'resident', label: '常驻', hint: '', count: 0 },
    { key: 'perceive', label: '感知', hint: '', count: 0 },
    { key: 'act', label: '行动', hint: '', count: 1 },
    { key: 'govern', label: '治理', hint: '', count: 0 },
  ],
  plane_counts: {},
  effects: [],
  effect_order: ['read', 'write', 'execute', 'extend'],
  effect_note: '',
  risks: [],
  tools: [
    { name: 'read_file', category: '文件', plane: 'act', effect: 'read', risk: 'low', tags: [], needs_approval: false, internal: false },
  ],
  tool_count: 1,
  tools_without_declaration: [],
  tool_source: 'registry',
}

const PREVIEW = {
  line_id: 'engineering',
  tools: ['read_file', 'grep'],
  count: 2,
  by_plane: { act: ['read_file', 'grep'] },
  denied_by_effect: [],
  denied_unknown: [],
  muted: [],
  truncated: [],
  reasons: {},
  needs_approval: [],
  max_tools: 20,
  over_budget: false,
  tools_meta: {},
}

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

/** 用例可换：/preview 的分身面字段（`null` = 旧后端不返回该字段） */
let assemblyReply: unknown = ASSEMBLY_OK

const fetchMock = vi.fn(async (url: string) => {
  const u = String(url)
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines/preview')) {
    const body: Record<string, unknown> = {
      ok: true, line_id: 'engineering', line: LINE_PROFILE,
      preview: PREVIEW, issues: [], notes: [], tool_source: 'registry', saved: true,
    }
    if (assemblyReply !== null) body.subagent_assembly = assemblyReply
    return json(body)
  }
  if (u.includes('/api/agent-lines')) {
    return json({ ok: true, active: 'engineering', lines: [LINE_PROFILE], broken: [] })
  }
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const { default: ToolsAgentLines } = await import('./agent-lines')
const { readSubagentAssembly } = await import('@/lib/agentLinesApi')

beforeEach(() => {
  fetchMock.mockClear()
  assemblyReply = ASSEMBLY_OK
})
afterEach(cleanup)

/**
 * 渲染页面并等预览真的算完（否则"没有某块"的断言会假通过）。
 *
 * 【不易】不要用 '工具数' 当就绪信号：它同时是 max_tools 字段提示的子串 ⇒ 页面一渲染
 * 就命中，断言会跑在 350ms 防抖的 /preview 之前。这里用**只存在于装配预览面板**的文案
 * （「在名额内」由 preview.over_budget 决定），并要求 /preview 确实打过。
 */
async function renderAndSettle() {
  const view = render(<ToolsAgentLines />)
  await waitFor(() => {
    expect(view.container.textContent).toContain('在名额内')
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes('/api/agent-lines/preview')),
    ).toBe(true)
  })
  return view
}

// ═══════════════════════════════════════════════════════════
//  纯函数：只读后端字段（不做判定）
// ═══════════════════════════════════════════════════════════

describe('readSubagentAssembly（只规范化后端字段）', () => {
  it('后端给了装配单 ⇒ 原样规范化，判定字段一个字都不改', () => {
    const got = readSubagentAssembly({ subagent_assembly: ASSEMBLY_OK })
    expect(got.present).toBe(true)
    expect(got.assembly).toEqual(ASSEMBLY_OK)
  })

  it('旧后端没有该字段 ⇒ present=false（整块不渲染）', () => {
    expect(readSubagentAssembly({ ok: true }).present).toBe(false)
    expect(readSubagentAssembly(null).present).toBe(false)
    expect(readSubagentAssembly(undefined).present).toBe(false)
    expect(readSubagentAssembly({ subagent_assembly: null }).present).toBe(false)
    expect(readSubagentAssembly({ subagent_assembly: 'x' }).present).toBe(false)
    expect(readSubagentAssembly({ subagent_assembly: [] }).present).toBe(false)
  })

  it('缺 available 布尔标志 ⇒ 也按"没给"处理（宁可不说，也不说错）', () => {
    const got = readSubagentAssembly({ subagent_assembly: { tools: ['read_file'] } })
    expect(got.present).toBe(false)
    expect(got.assembly).toBeNull()
  })

  it('脏条目被忽略且绝不抛（非字符串工具名、非串说明）', () => {
    const got = readSubagentAssembly({
      subagent_assembly: {
        available: false, mode: 'unavailable', tools: [1, null, 'read_file'],
        needs_approval: 'not-a-list', note: 12345, reason: undefined,
      },
    })
    expect(got.present).toBe(true)
    expect(got.assembly!.tools).toEqual(['read_file'])
    expect(got.assembly!.needs_approval).toEqual([])
    expect(got.assembly!.note).toBe('')
    expect(got.assembly!.reason).toBe('')
  })
})

// ═══════════════════════════════════════════════════════════
//  页面接线：装配预览如实呈现（全部走 data-testid）
// ═══════════════════════════════════════════════════════════

describe('装配预览 · 分身面（渲染后端给的字段）', () => {
  it('装配单成立 ⇒ 显示允许的工具集 + 被拒/硬禁说明 + 需人工确认', async () => {
    const { container } = await renderAndSettle()
    const block = container.querySelector('[data-testid="line-subagent-face"]')
    expect(block).toBeTruthy()
    expect(block!.getAttribute('data-subagent-mode')).toBe('line')
    expect(block!.getAttribute('data-subagent-semantics')).toBe('named-resolved')
    expect(block!.textContent).toContain('沿主线装配')

    const tools = block!.querySelector('[data-testid="line-subagent-tools"]')!
    expect(tools.textContent).toContain('read_file')
    expect(tools.textContent).toContain('grep')
    expect(block!.querySelector('[data-testid="line-subagent-approval"]')!.textContent)
      .toContain('shell_execute')

    const note = block!.querySelector('[data-testid="line-subagent-note"]')!.textContent!
    expect(note).toContain('§5.7 机制 3 硬禁')
    expect(note).toContain('remember')
    expect(block!.querySelector('[data-testid="line-subagent-semantics"]')!.textContent)
      .toContain('去 govern 平面')
    expect(block!.querySelector('[data-testid="line-subagent-skills-note"]')!.textContent)
      .toContain('白名单')
    expect(block!.querySelector('[data-testid="line-subagent-unavailable"]')).toBeNull()
  })

  it('点名了却装不上 ⇒ 显示失败原因，且不画成"0 个工具"', async () => {
    assemblyReply = ASSEMBLY_UNAVAILABLE
    const { container } = await renderAndSettle()
    const block = container.querySelector('[data-testid="line-subagent-face"]')!
    expect(block.getAttribute('data-subagent-semantics')).toBe('named-but-unavailable')
    expect(block.textContent).toContain('装不上')
    const bad = block.querySelector('[data-testid="line-subagent-unavailable"]')!
    expect(bad.textContent).toContain('主线不存在')
    expect(bad.textContent).toContain('未回退成全量授权')
    expect(block.querySelector('[data-testid="line-subagent-tools"]')).toBeNull()
    expect(block.querySelector('[data-testid="line-subagent-tools-empty"]')).toBeNull()
  })

  it('未点名 ⇒ 只读默认集（明确写「不是降级」）', async () => {
    assemblyReply = {
      ...ASSEMBLY_OK,
      mode: 'default-readonly',
      semantics: 'unnamed-default-readonly',
      semantics_note: '任务未点名 line 且无全局激活主线 ⇒ 只读默认集。**这不是降级**。',
      line_id: '',
      tools: ['read_file', 'grep'],
      needs_approval: [],
      note: '未指定 line 且无全局激活主线 ⇒ 使用只读默认集（写文件/Shell 不在内）',
    }
    const { container } = await renderAndSettle()
    const block = container.querySelector('[data-testid="line-subagent-face"]')!
    expect(block.getAttribute('data-subagent-mode')).toBe('default-readonly')
    expect(block.textContent).toContain('只读默认集（未点名）')
    expect(block.querySelector('[data-testid="line-subagent-semantics"]')!.textContent)
      .toContain('不是降级')
    expect(block.querySelector('[data-testid="line-subagent-tools"]')!.textContent)
      .toContain('read_file')
  })

  it('装配结果为空 ⇒ 如实说「一个都不给」，不说成"给全量"', async () => {
    assemblyReply = { ...ASSEMBLY_OK, tools: [], needs_approval: [], note: '沿主线 x 装配（0 个工具）' }
    const { container } = await renderAndSettle()
    const block = container.querySelector('[data-testid="line-subagent-face"]')!
    expect(block.querySelector('[data-testid="line-subagent-tools"]')).toBeNull()
    expect(block.querySelector('[data-testid="line-subagent-tools-empty"]')!.textContent)
      .toContain('一个都不给')
  })

  it('旧后端没有 subagent_assembly 字段 ⇒ 整块不渲染（行为与接上它之前一致）', async () => {
    assemblyReply = null
    const { container } = await renderAndSettle()
    expect(container.querySelector('[data-testid="line-subagent-face"]')).toBeNull()
    // 预览本体照常渲染（证明"没有区块"不是"预览还没算完"）
    expect(container.textContent).toContain('在名额内')
  })
})
