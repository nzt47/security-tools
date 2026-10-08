/**
 * 组装台守卫（pages/hub/workshop/assembly.tsx + lib/subagentApi.ts）
 * ------------------------------------------------------------------
 * 锁死三件"自解释"不变量：
 *   1. **装配预览 = 真实请求体**：预览文本 JSON.parse 后必须与 POST body 深相等
 *      （同一个 `buildCreatePayload`；回退成分开拼立刻红）；
 *   2. **未表态不发键**：温度留空不发 `llm_temperature`、自由文本留空不发 `role_text`
 *      （0 是"要最确定性"，必须发出去）；
 *   3. **主权清单三态由后端给**：未接线面显示"声明未接线"，展开可见证据/缺口/阶段。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'

function json(body: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response
}
function envelope(data: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: (k: string) => (k === 'X-Envelope' ? 'v2' : null) }, json: async () => ({ code: 200, data }), text: async () => JSON.stringify({ code: 200, data }) } as unknown as Response
}

const LIST_PAYLOAD = {
  ok: true, count: 0, channel: {}, subagents: [],
  llm: { model: 'deepseek-flash', provider: 'deepseek', options: [{ model: 'deepseek-flash', source: 'deployment' }, { model: 'deepseek-v4-pro', source: 'declared' }] },
  role: { default_tier: 'template', role_text_max_chars: 2000, templates: [{ id: 'code_review', title: '代码审查', body: '…', note: '' }, { id: 'research', title: '资料调研', body: '…', note: '' }], tiers: [{ value: 'template', label: '受控模板', red: false, audit: false }, { value: 'template+text', label: '模板 + 自由文本（进约束）', red: false, audit: true }, { value: 'full-system', label: '自由文本进系统提示词（红档）', red: true, audit: true }] },
}

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
    return json({ ok: true })
  }
  if (u.includes('/api/subagent/list')) return envelope(LIST_PAYLOAD)
  if (u.includes('/api/subagent/capabilities')) return json(CAPABILITIES)
  return json({})
})
vi.stubGlobal('fetch', fetchMock)

const AssemblyConsole = (await import('./assembly')).default

beforeEach(() => { fetchMock.mockClear(); postCalls.length = 0 })
afterEach(cleanup)

async function settle() {
  const view = render(<AssemblyConsole />)
  await waitFor(() => expect(screen.getByTestId('assembly-preview')).toBeInTheDocument())
  return view
}

function previewJson(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId('assembly-preview').textContent ?? '{}')
}

describe('组装台 · 生成与自解释', () => {
  it('起点模板「代码审查」把角色模板设为 code_review', async () => {
    await settle()
    fireEvent.click(screen.getByTestId('assembly-template-code_review'))
    expect((screen.getByTestId('assembly-role-template') as HTMLSelectElement).value).toBe('code_review')
  })

  it('装配预览 = 真实 POST body（同一序列化函数，回退即红）', async () => {
    await settle()
    fireEvent.change(screen.getByTestId('assembly-name'), { target: { value: 'sovereign-1' } })
    fireEvent.change(screen.getByTestId('assembly-role-template'), { target: { value: 'code_review' } })
    fireEvent.change(screen.getByTestId('assembly-temperature'), { target: { value: '0.2' } })
    fireEvent.change(screen.getByTestId('assembly-tools'), { target: { value: 'builtin, mcp:fs' } })
    fireEvent.click(screen.getByTestId('assembly-permission-write'))
    const preview = previewJson()
    fireEvent.click(screen.getByTestId('assembly-create'))
    await waitFor(() => expect(postCalls.length).toBe(1))
    expect(postCalls[0].url).toContain('/api/subagent/create')
    expect(postCalls[0].body).toEqual(preview)
    expect(preview.name).toBe('sovereign-1')
    expect(preview.role_template).toBe('code_review')
    expect(preview.tool_sources).toEqual(['builtin', 'mcp:fs'])
    expect(preview.permissions).toEqual(['read', 'write'])
  })

  it('名称留空 ⇒ 生成按钮禁用', async () => {
    await settle()
    expect(screen.getByTestId('assembly-create')).toBeDisabled()
  })

  it('温度留空 ⇒ 不发 llm_temperature；填 0 ⇒ 发 0（"要最确定性"必须发）', async () => {
    await settle()
    expect('llm_temperature' in previewJson()).toBe(false)
    fireEvent.change(screen.getByTestId('assembly-temperature'), { target: { value: '0' } })
    expect(previewJson().llm_temperature).toBe(0)
  })

  it('自由文本：留空不发；填了但默认档则如实发出并提示（由后端 400，不静默吞）', async () => {
    await settle()
    expect('role_text' in previewJson()).toBe(false)
    fireEvent.change(screen.getByTestId('assembly-role-template'), { target: { value: 'code_review' } })
    fireEvent.change(screen.getByTestId('assembly-role-text'), { target: { value: '  只审查不要改代码  ' } })
    // 默认档：**不静默丢**，预览里如实带着（后端会 400 点名要显式选档）
    expect(previewJson().role_text).toBe('只审查不要改代码')
    expect(screen.getByTestId('assembly-role-text-hint')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('assembly-role-mode'), { target: { value: 'template+text' } })
    expect(previewJson().role_text).toBe('只审查不要改代码')
    expect(previewJson().role_mode).toBe('template+text')
    expect(screen.queryByTestId('assembly-role-text-hint')).toBeNull()
  })

  it('主权清单：三态来自后端，未接线面显示"声明未接线"', async () => {
    await settle()
    await waitFor(() => expect(screen.getByTestId('sovereignty-summary')).toBeInTheDocument())
    expect(screen.getByTestId('sovereignty-face-role').textContent).toContain('拥有')
    expect(screen.getByTestId('sovereignty-face-memory_scope').textContent).toContain('声明未接线')
    expect(screen.getByTestId('sovereignty-summary').textContent).toContain('L2')
  })

  it('点主权面 ⇒ 展开"凭什么"（证据 / 证据文件 / 缺口 / 阶段）', async () => {
    await settle()
    await waitFor(() => expect(screen.getByTestId('sovereignty-face-memory_scope')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('sovereignty-face-memory_scope').querySelector('button') as HTMLElement)
    const text = screen.getByTestId('sovereignty-face-memory_scope').textContent ?? ''
    expect(text).toContain('tenancy 地基在')
    expect(text).toContain('agent/memory/tenancy.py')
    expect(text).toContain('memory_provider 无消费方')
    expect(text).toContain('S3')
  })

  it('记忆字段如实标注为"声明字段"（不假装已接线）', async () => {
    await settle()
    expect(screen.getByTestId('assembly-memory-note').textContent).toContain('声明字段')
  })

  it('生成成功 ⇒ 显示成功消息', async () => {
    await settle()
    fireEvent.change(screen.getByTestId('assembly-name'), { target: { value: 'sovereign-2' } })
    fireEvent.click(screen.getByTestId('assembly-create'))
    await waitFor(() => expect(screen.getByTestId('assembly-message').textContent).toContain('sovereign-2'))
  })
})