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

const fetchMock = vi.fn(async (url: string) => {
  const u = String(url)
  if (u.includes('/api/subagent/list')) {
    return envelope({
      ok: true, count: 1, channel: {},
      subagents: [{ name: 'alpha', model_id: 'gpt-4o', memory_provider: 'holographic', status: 'running' }],
    })
  }
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

  it('分身视图如实说明：工具集由主线档案决定，表单三个字段是声明字段', async () => {
    render(<WorkshopAgents />)
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument())
    expect(screen.getByText(/分身的工具集由「主线档案」决定/)).toBeInTheDocument()
  })
})
