/**
 * 主线管理 —— 装配预览的「本线会注入的提示词片段（role=line）」区块
 * ------------------------------------------------------------------
 * 背景：LineProfile.prompt_note 此前是**死字段** —— UI 写着"随本线注入的系统
 * 提示词片段"、data/agent_lines/*.yaml 里有内容、REST 层存取它，但运行时**没有
 * 任何一处消费**。后端已把它接到系统提示词（agent/orchestrator/prompt_builder.py
 * ::line_fragment_for_profile → role=line 片段），并在
 * `POST /api/agent-lines/preview|validate` 的响应里给出
 * `prompt_fragments` / `prompt_fragments_note`。本文件锁死的不变量：
 *   1. 前端**只渲染后端给的字段**，不自己重算"会不会注入"（纯函数 readPromptFragments
 *      单测钉住：它只做形状规范化，不产生判定）；
 *   2. 后端给了片段 ⇒ 显示 role / source / priority / 可裁剪性 / 片段原文；
 *   3. 后端给了空列表 ⇒ 显示后端给的人读原因（字段为空 / 本线停用）；
 *   4. 旧后端没有该字段 ⇒ 整块不渲染，行为与本页接上它之前完全一致；
 *   5. "本线是否真的生效"用前端已有的两个事实组合（激活指针 + 草案是否已保存）。
 *
 * 页面自身会打三个接口（/api/agent-lines、/planes、/preview），故 fetch 按 URL 打桩；
 * 查询一律走 data-testid，避免同一文案在多处出现时 query 命中多元素而假红。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'

const NOTE = '本线的交付标准是「改完并验证」：不得留下未验证的改动。'

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
  prompt_note: NOTE,
}

/** 后端「本线会注入的片段」（agent/server_routes/routes_agent_lines.py 的投影） */
const FRAGMENTS_OK = [{
  role: 'line',
  source: 'line:engineering',
  content: NOTE,
  chars: NOTE.length,
  priority: 50,
  croppable: false,
}]

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
  tools: ['read_file'],
  count: 1,
  by_plane: { act: ['read_file'] },
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

/** 用例可换：档案列表（默认 engineering 已激活） */
let lineListReply: Record<string, unknown> = {}
/** 用例可换：/preview 的片段字段（`null` = 旧后端不返回该字段） */
let fragmentsReply: unknown = FRAGMENTS_OK
let fragmentsNoteReply = ''

const fetchMock = vi.fn(async (url: string) => {
  const u = String(url)
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines/preview')) {
    const body: Record<string, unknown> = {
      ok: true, line_id: 'engineering', line: LINE_PROFILE,
      preview: PREVIEW, issues: [], notes: [], tool_source: 'registry', saved: true,
    }
    if (fragmentsReply !== null) {
      body.prompt_fragments = fragmentsReply
      body.prompt_fragments_note = fragmentsNoteReply
    }
    return json(body)
  }
  if (u.includes('/api/agent-lines')) return json(lineListReply)
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const { default: ToolsAgentLines, readPromptFragments } = await import('./lines')

beforeEach(() => {
  fetchMock.mockClear()
  lineListReply = { ok: true, active: 'engineering', lines: [LINE_PROFILE], broken: [] }
  fragmentsReply = FRAGMENTS_OK
  fragmentsNoteReply = ''
})
afterEach(cleanup)

/**
 * 渲染页面并等预览真的算完（否则"没有某块"的断言会假通过）。
 *
 * 【不易】不要用 '工具数' 当就绪信号：它同时是 max_tools 字段提示
 * 「单轮最多暴露给模型的工具数」的子串 ⇒ 页面一渲染就命中，断言会跑在
 * 350ms 防抖的 /preview 之前。这里用**只存在于装配预览面板**的文案
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

describe('readPromptFragments（只规范化后端字段）', () => {
  it('后端给了片段 ⇒ 原样规范化，note 为空', () => {
    const got = readPromptFragments({ prompt_fragments: FRAGMENTS_OK, prompt_fragments_note: '' })
    expect(got.available).toBe(true)
    expect(got.note).toBe('')
    expect(got.fragments).toEqual([{
      role: 'line', source: 'line:engineering', content: NOTE,
      chars: NOTE.length, priority: 50, croppable: false,
    }])
  })

  it('后端给空列表 + 原因 ⇒ available 仍为 true（区块要显示原因）', () => {
    const got = readPromptFragments({
      prompt_fragments: [], prompt_fragments_note: 'prompt_note 为空 ⇒ 不注入',
    })
    expect(got.available).toBe(true)
    expect(got.fragments).toEqual([])
    expect(got.note).toBe('prompt_note 为空 ⇒ 不注入')
  })

  it('旧后端没有该字段 ⇒ available=false（整块不渲染）', () => {
    expect(readPromptFragments({ ok: true }).available).toBe(false)
    expect(readPromptFragments(null).available).toBe(false)
    expect(readPromptFragments(undefined).available).toBe(false)
  })

  it('脏条目被忽略且绝不抛（缺 role/content、非对象）', () => {
    const got = readPromptFragments({
      prompt_fragments: [null, 'x', { role: 'line' }, { content: '没有 role' },
                         { role: 'line', content: '好的一条' }],
      prompt_fragments_note: 123,
    })
    expect(got.fragments.map((f) => f.content)).toEqual(['好的一条'])
    // note 不是字符串 ⇒ 退化为空串，不做类型转换发明
    expect(got.note).toBe('')
  })

  it('缺 chars/priority 时按内容长度与 0 兜底（只补展示字段，不改判定）', () => {
    const got = readPromptFragments({ prompt_fragments: [{ role: 'line', content: 'abcd' }] })
    expect(got.fragments[0].chars).toBe(4)
    expect(got.fragments[0].priority).toBe(0)
    expect(got.fragments[0].croppable).toBe(false)
    expect(got.fragments[0].source).toBe('')
  })
})

// ═══════════════════════════════════════════════════════════
//  页面接线：装配预览如实呈现（全部走 data-testid）
// ═══════════════════════════════════════════════════════════

describe('装配预览 · role=line 片段（渲染后端给的字段）', () => {
  it('后端给片段 ⇒ 显示来源、优先级、不可裁剪与片段原文', async () => {
    const { container } = await renderAndSettle()
    const block = container.querySelector('[data-testid="line-prompt-fragment"]')
    expect(block).toBeTruthy()
    expect(block!.querySelector('[data-testid="line-fragment-source"]')!.textContent)
      .toBe('line:engineering')
    expect(block!.textContent).toContain('priority=50')
    expect(block!.textContent).toContain('不可裁剪（硬片段）')
    expect(block!.querySelector('pre')!.textContent).toBe(NOTE)
    expect(container.querySelectorAll('[data-testid="line-fragment-item"]')).toHaveLength(1)
  })

  it('本线已激活且已保存 ⇒ 明确写「本轮对话会注入」', async () => {
    const { container } = await renderAndSettle()
    expect(container.querySelector('[data-testid="line-fragment-state"]')!.textContent)
      .toContain('本轮对话会注入')
  })

  it('本线未激活 ⇒ 写「本线未激活」，不假装已生效', async () => {
    lineListReply = { ok: true, active: null, lines: [LINE_PROFILE], broken: [] }
    const { container } = await renderAndSettle()
    expect(container.querySelector('[data-testid="line-fragment-state"]')!.textContent)
      .toContain('本线未激活')
  })

  it('后端给空列表 ⇒ 显示后端给的原因，且不渲染任何片段', async () => {
    fragmentsReply = []
    fragmentsNoteReply = 'prompt_note 为空 ⇒ 不注入 role=line 片段；系统提示词与未装线时逐字一致'
    const { container } = await renderAndSettle()
    const empty = container.querySelector('[data-testid="line-fragment-empty"]')
    expect(empty).toBeTruthy()
    expect(empty!.textContent).toContain('不注入')
    expect(empty!.textContent).toContain('与未装线时逐字一致')
    expect(container.querySelectorAll('[data-testid="line-fragment-item"]')).toHaveLength(0)
  })

  it('旧后端没有 prompt_fragments 字段 ⇒ 整块不渲染（行为与接上它之前一致）', async () => {
    fragmentsReply = null
    const { container } = await renderAndSettle()
    expect(container.querySelector('[data-testid="line-prompt-fragment"]')).toBeNull()
    // 预览本体照常渲染（证明"没有区块"不是"预览还没算完"）
    expect(container.textContent).toContain('在名额内')
  })
})
