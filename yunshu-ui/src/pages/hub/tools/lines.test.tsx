/**
 * 主线管理 —— 装配预览的「技能」区块（L2 `skills:` 的运行时判定）
 * ------------------------------------------------------------------
 * 需求：把主线档案里此前「只有 UI 与存储、没有消费方」的 `skills` 接上运行时，
 * 并在预览里如实呈现。锁死的不变量：
 *   1. 白名单模式 ⇒ 列出**后端给出的** allowed；声明了但目录里没有的 id 单独红色列出
 *      （写错 id 不许静默消失）；
 *   2. 不限制模式 ⇒ 显示「不限制」并给出后端的原因文案（空声明 = 不限制，不是「一个都不给」）；
 *   3. 前端**不重算**：文案一律取自后端 `source_label`，页面里不出现第二份判定；
 *   4. 旧后端不返回 `skills` ⇒ 整块不渲染，行为与本页接上技能判定之前完全一致。
 *
 * 页面自身会打三个接口（/api/agent-lines、/planes、/preview），故 fetch 按 URL 打桩。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

// ═══════════════════════════════════════════════════════════
//  固定响应（形状与后端 `SkillPack.to_dict()` 对齐）
// ═══════════════════════════════════════════════════════════

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
  skills: ['engineering-test-delivery', 'typo-skill'],
  prompt_note: '',
}

const LINE_LIST = { ok: true, active: 'engineering', lines: [LINE_PROFILE], broken: [] }
const PLANES = {
  ok: true,
  planes: [
    { key: 'resident', label: '常驻', hint: '', count: 0 },
    { key: 'perceive', label: '感知', hint: '', count: 1 },
    { key: 'act', label: '行动', hint: '', count: 1 },
    { key: 'govern', label: '治理', hint: '', count: 0 },
  ],
  plane_counts: {},
  effects: [],
  effect_order: ['read', 'write', 'execute', 'extend'],
  effect_note: '',
  risks: [],
  tools: [
    { name: 'read_file', category: '文件', plane: 'perceive', effect: 'read', risk: 'low', tags: [], needs_approval: false, internal: false },
  ],
  tool_count: 1,
  tools_without_declaration: [],
  tool_source: 'registry',
}

const PREVIEW_BASE = {
  line_id: 'engineering',
  tools: ['read_file'],
  count: 1,
  by_plane: { perceive: ['read_file'] },
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

/** 白名单：一条有效 + 一条 typo（unknown） */
const SKILLS_WHITELIST = {
  line_id: 'engineering',
  mode: 'whitelist',
  unrestricted: false,
  requested: ['engineering-test-delivery', 'typo-skill'],
  allowed: ['engineering-test-delivery'],
  unknown: ['typo-skill'],
  source: 'whitelist',
  source_label: '本线按 skills 声明做白名单',
}

/** 不限制：空声明 */
const SKILLS_UNRESTRICTED = {
  line_id: 'engineering',
  mode: 'unrestricted',
  unrestricted: true,
  requested: [],
  allowed: [],
  unknown: [],
  source: 'empty-declaration',
  source_label: '本线未声明技能（skills 为空）⇒ 不限制',
}

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

/** `POST /api/agent-lines/preview` 的应答（用例可换） */
let previewReply: Record<string, unknown> = {}

const fetchMock = vi.fn(async (url: string) => {
  const u = String(url)
  if (u.includes('/api/agent-lines/planes')) return json(PLANES)
  if (u.includes('/api/agent-lines/preview')) return json(previewReply)
  if (u.includes('/api/agent-lines')) return json(LINE_LIST)
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const { default: ToolsAgentLines, SkillPackBlock } = await import('./lines')

function renderPage() {
  return render(<ToolsAgentLines />)
}

beforeEach(() => {
  fetchMock.mockClear()
  previewReply = {
    ok: true,
    line_id: 'engineering',
    line: LINE_PROFILE,
    preview: PREVIEW_BASE,
    skills: SKILLS_WHITELIST,
    issues: [],
    notes: [],
    tool_source: 'registry',
    saved: false,
  }
})
afterEach(cleanup)

// ═══════════════════════════════════════════════════════════
//  用例
// ═══════════════════════════════════════════════════════════

describe('装配预览 · 技能区块', () => {
  it('白名单：列出 allowed，并把声明了但不存在的 id 红色单独列出（写错不许静默消失）', () => {
    const { container } = render(<SkillPackBlock skills={SKILLS_WHITELIST} />)
    expect(container.querySelector('[data-skill-pack="whitelist"]')).toBeTruthy()
    expect(container.querySelector('[data-skill-allowed="1"]')!.textContent)
      .toContain('engineering-test-delivery')
    const unknownBox = container.querySelector('[data-skill-unknown="1"]')!
    expect(unknownBox.textContent).toContain('typo-skill')
    // 未知 id 不得混进「本线允许注入」那一片
    expect(container.querySelector('[data-skill-allowed="1"]')!.textContent)
      .not.toContain('typo-skill')
  })

  it('不限制：显示「不限制」与后端原因文案（空声明 = 未表态，不是「一个都不给」）', () => {
    const { container } = render(<SkillPackBlock skills={SKILLS_UNRESTRICTED} />)
    expect(container.querySelector('[data-skill-pack="unrestricted"]')).toBeTruthy()
    expect(container.querySelector('[data-skill-unrestricted="1"]')).toBeTruthy()
    // 文案来自后端 source_label（前端不重算，避免第二份口径）
    expect(container.textContent).toContain(SKILLS_UNRESTRICTED.source_label)
    expect(container.textContent).toContain('不限制')
  })

  it('不限制：即便后端给了 allowed 也不渲染未知清单（两种模式互斥）', () => {
    const { container } = render(
      <SkillPackBlock skills={{ ...SKILLS_UNRESTRICTED, allowed: ['x'], unknown: ['y'] }} />)
    expect(container.querySelector('[data-skill-unknown="1"]')).toBeNull()
  })

  it('旧后端不返回 skills ⇒ 整块不渲染（行为与本页接上技能判定之前一致）', () => {
    const { container } = render(<SkillPackBlock skills={undefined} />)
    expect(container.querySelector('[data-skill-pack]')).toBeNull()
    const { container: c2 } = render(<SkillPackBlock skills={null} />)
    expect(c2.querySelector('[data-skill-pack]')).toBeNull()
  })

  it('页面接线：/preview 回的 skills 直接上屏（页面不做任何重算）', async () => {
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('[data-skill-pack="whitelist"]')).toBeTruthy()
    })
    const block = container.querySelector('[data-skill-pack="whitelist"]')!
    expect(block.textContent).toContain('本线按 skills 声明做白名单')
    expect(block.textContent).toContain('engineering-test-delivery')
    expect(block.textContent).toContain('typo-skill')
    expect(screen.getByText('技能')).toBeTruthy()
  })

  it('页面接线：后端给不限制 ⇒ 预览显示「不限制」', async () => {
    previewReply = { ...previewReply, skills: SKILLS_UNRESTRICTED }
    const { container } = renderPage()
    await waitFor(() => {
      expect(container.querySelector('[data-skill-pack="unrestricted"]')).toBeTruthy()
    })
    expect(container.querySelector('[data-skill-unrestricted="1"]')!.textContent)
      .toContain('未限定技能范围')
  })

  it('页面接线：旧后端无 skills 字段 ⇒ 预览照常渲染且没有技能区块', async () => {
    previewReply = { ...previewReply }
    delete (previewReply as Record<string, unknown>).skills
    const { container } = renderPage()
    // 等预览真的算完（面板出现「工具数」）再断言「没有技能区块」——
    // 否则这块断言会因为「还没渲染」而假通过。
    await waitFor(() => {
      expect(container.textContent).toContain('工具数')
    })
    expect(container.querySelector('[data-skill-pack]')).toBeNull()
  })
})