/**
 * 「主线 × 四面」总览页的守卫
 * ------------------------------------------------------------------
 * 本页是「宿主功能零清单进导航」的第三个实例（features/line-assembly/index.tsx）。
 * 锁死的不变量：
 *   1. 四个面**逐格**如实呈现后端字段（工具面 count/上限、技能面 白名单/不限制、
 *      提示词面 段数、分身面 mode+工具数）；
 *   2. 旧后端缺某一段字段 ⇒ 该格显示"后端未返回"，**不假装是 0**
 *      （"没有这一面"和"这一面是空的"是两回事）；
 *   3. 分身面不可用（点名的线装不上）⇒ 显示后端给的原因，而不是"0 个工具"；
 *   4. 单条详情取数失败 ⇒ 只影响那一行，其余行照常；
 *   5. manifest 四件齐全且被 featureRegistry 自动发现（零清单进导航）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'

const LINE_A = {
  id: 'engineering', name: '工程线', description: '', enabled: true,
  plane_weights: { act: 1 }, plane_floors: { act: 1 }, boost: [], mute: [], tags: [],
  max_tools: 20, effect_allow: ['read'], requires_approval: [], allow_govern: false,
  skills: [], prompt_note: '',
}
const LINE_B = {
  ...LINE_A, id: 'dev', name: '开发线', enabled: false,
}

const PREVIEW_A = {
  line_id: 'engineering', tools: ['read_file', 'grep'], count: 2,
  by_plane: { act: ['read_file', 'grep'] }, denied_by_effect: [], denied_unknown: [],
  muted: [], truncated: [], reasons: {}, needs_approval: ['shell_execute'], max_tools: 20,
  over_budget: false,
}
const PREVIEW_B = { ...PREVIEW_A, line_id: 'dev', tools: [], count: 1, needs_approval: [], max_tools: 6 }

const SKILLS_A = {
  line_id: 'engineering', mode: 'whitelist', unrestricted: false, requested: ['s1', 's2'],
  allowed: ['s1'], unknown: ['s2'], source: 'line', source_label: '本线声明',
}
const SKILLS_B = { ...SKILLS_A, line_id: 'dev', mode: 'unrestricted', unrestricted: true, allowed: [], unknown: [] }

const FRAG_A = [{ role: 'line', source: 'line:engineering', content: 'x', chars: 1, priority: 50, croppable: false }]

const SUBAGENT_A = {
  available: true, mode: 'line', semantics: 'named-resolved', semantics_note: '沿主线装配',
  line_id: 'engineering', tools: ['read_file'], needs_approval: [],
  note: '沿主线 engineering 装配（1 个工具）', skills: [], skills_mode: 'whitelist',
  skills_note: '', prompt_note: '', prompt_source: '', reason: '',
}
/** 点名了却装不上：显示原因，不画成"0 个工具" */
const SUBAGENT_B = {
  ...SUBAGENT_A, available: false, mode: 'unavailable', semantics: 'named-but-unavailable',
  tools: [], reason: '主线已停用: dev（未回退成全量授权）',
}

function json(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) } as unknown as Response
}

/** 用例可换：详情载荷的组装方式（用来模拟"旧后端缺字段"与"单条失败"） */
let detailFor: (id: string) => Record<string, unknown> = () => ({})

const fetchMock = vi.fn(async (url: string) => {
  const u = String(url)
  if (u.includes('/api/agent-lines/')) {
    const id = decodeURIComponent(u.split('/api/agent-lines/')[1])
    return json({ ok: true, line: id === 'dev' ? LINE_B : LINE_A, issues: [], tool_source: 'registry', ...detailFor(id) })
  }
  if (u.endsWith('/api/agent-lines')) {
    return json({ ok: true, active: 'engineering', lines: [LINE_A, LINE_B], broken: [] })
  }
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const { default: LineAssemblyOverview, manifest } = await import('./index')

beforeEach(() => {
  fetchMock.mockClear()
  detailFor = (id) => (id === 'dev'
    ? { preview: PREVIEW_B, skills: SKILLS_B, prompt_fragments: [], prompt_fragments_note: 'prompt_note 为空', subagent_assembly: SUBAGENT_B }
    : { preview: PREVIEW_A, skills: SKILLS_A, prompt_fragments: FRAG_A, prompt_fragments_note: '', subagent_assembly: SUBAGENT_A })
})
afterEach(cleanup)

async function renderSettled() {
  const view = render(<LineAssemblyOverview />)
  await waitFor(() => {
    expect(view.container.querySelector('[data-testid="face-subagent-engineering"]')!.textContent)
      .toContain('沿主线')
  })
  return view
}

describe('主线 × 四面 总览', () => {
  it('manifest 四件齐全（featureRegistry 的自动发现前提）', () => {
    expect(manifest.key).toBe('line-assembly')
    expect(manifest.label).toBeTruthy()
    expect(manifest.icon).toBeTruthy()
    expect(manifest.order).toBeGreaterThan(0)
  })

  it('零清单进导航：被 featureRegistry 自动发现', async () => {
    const { DISCOVERED_FEATURES } = await import('@/workbench/featureRegistry')
    expect(DISCOVERED_FEATURES.map((f) => f.key)).toContain('line-assembly')
  })

  it('四面逐格如实呈现（工具/技能/提示词/分身）', async () => {
    const { container } = await renderSettled()
    expect(container.querySelector('[data-testid="face-tools-engineering"]')!.textContent)
      .toContain('上限 20')
    expect(container.querySelector('[data-testid="face-tools-engineering"]')!.textContent)
      .toContain('需确认 1')
    expect(container.querySelector('[data-testid="face-skills-engineering"]')!.textContent)
      .toContain('白名单 1')
    expect(container.querySelector('[data-testid="face-skills-engineering"]')!.textContent)
      .toContain('写错 1')
    expect(container.querySelector('[data-testid="face-prompt-engineering"]')!.textContent)
      .toContain('1 段')
    expect(container.querySelector('[data-testid="face-prompt-engineering"]')!.textContent)
      .toContain('line:engineering')
    expect(container.querySelector('[data-testid="face-subagent-engineering"]')!.textContent)
      .toContain('1 个工具')
  })

  it('分身面装不上 ⇒ 显示后端原因，而不是"0 个工具"', async () => {
    const { container } = await renderSettled()
    const cell = container.querySelector('[data-testid="face-subagent-dev"]')!.textContent!
    expect(cell).toContain('装不上')
    expect(cell).toContain('主线已停用')
    expect(cell).not.toContain('0 个工具')
  })

  it('旧后端缺某一段 ⇒ 显示"后端未返回"，不假装 0', async () => {
    detailFor = () => ({ preview: PREVIEW_A })
    const { container } = await renderAndWait()
    expect(container.querySelector('[data-testid="face-skills-engineering"]')!.textContent)
      .toContain('后端未返回')
    expect(container.querySelector('[data-testid="face-prompt-engineering"]')!.textContent)
      .toContain('后端未返回')
    expect(container.querySelector('[data-testid="face-subagent-engineering"]')!.textContent)
      .toContain('后端未返回')
  })

  it('单条详情取数失败 ⇒ 只影响那一行', async () => {
    detailFor = (id) => {
      if (id === 'dev') throw new Error('boom')
      return { preview: PREVIEW_A, skills: SKILLS_A, prompt_fragments: FRAG_A, subagent_assembly: SUBAGENT_A }
    }
    const { container } = await renderSettled()
    expect(container.querySelector('[data-testid="face-tools-dev"]')!.textContent).toContain('读取失败')
    // 另一行照常
    expect(container.querySelector('[data-testid="face-subagent-engineering"]')!.textContent)
      .toContain('沿主线')
  })

  it('列表取数失败 ⇒ 响亮报错，不静默留白', async () => {
    fetchMock.mockImplementationOnce(async () => json({ error: '主线列表爆炸' }, 500))
    const view = render(<LineAssemblyOverview />)
    await waitFor(() => {
      expect(view.container.textContent).toContain('读取主线列表失败')
    })
    expect(view.container.textContent).toContain('主线列表爆炸')
  })
})

/** 与 renderSettled 同款，但就绪信号换成"三格都已显示占位/内容"（缺字段用例用） */
async function renderAndWait() {
  const view = render(<LineAssemblyOverview />)
  await waitFor(() => {
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/api/agent-lines/')).length)
      .toBeGreaterThanOrEqual(2)
    expect(view.container.textContent).toContain('后端未返回')
  })
  return view
}
