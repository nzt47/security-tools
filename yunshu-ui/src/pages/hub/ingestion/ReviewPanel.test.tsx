/**
 * 吞噬 · 草稿评审 Tab（从技能中心维护迁入）。
 *
 * 锁住原安全语义：阻断项单条不放行；force 必须勾豁免 + 填原因；
 * 批量放行只认显式勾选；驳回先确认。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import ReviewPanel from './ReviewPanel'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  vi.unstubAllGlobals()
})

function setup() {
  api.hubGet.mockResolvedValue({
    ok: true, total: 2,
    items: [
      { id: 'ext-ok', name: '外来技能A', source: 'github:u/r', review: { review_verdict: 'pass', blocked: false } },
      { id: 'ext-blocked', name: '外来技能B', source: 'url:https://x', review: { review_verdict: 'block', blocked: true } },
    ],
  })
  api.hubPost.mockResolvedValue({ ok: true })
  render(<ReviewPanel />)
}

describe('ReviewPanel', () => {
  it('队列上屏；阻断项单条放行不可点', async () => {
    setup()
    expect(await screen.findByText('外来技能A')).toBeTruthy()
    expect(await screen.findByText('外来技能B')).toBeTruthy()
    expect((screen.getByRole('button', { name: '放行外来技能 ext-blocked' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '放行外来技能 ext-ok' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('列出吞噬骨架草稿（它们不在技能队列里，必须在评审页可见）', async () => {
    api.hubGet.mockImplementation((url: string) => url === '/api/ingestion/drafts'
      ? Promise.resolve({ data: { items: [{
        draft_id: 'ing_abc123', form: 'workflow', skill_name: '代码重构',
        created_at: '2026-10-26T11:57:55+08:00', warnings: ['a', 'b', 'c'],
        draft_dir: 'C:/repo/data/ingestion/drafts/ing_abc123',
      }] } })
      : Promise.resolve({ ok: true, total: 1, items: [{
        id: 'ext-ok', name: '外来技能A', source: 'github:u/r', review: { review_verdict: 'pass', blocked: false },
      }] }))
    render(<ReviewPanel />)

    expect(await screen.findByText('ing_abc123')).toBeTruthy()
    expect(screen.getByText('工作流')).toBeTruthy()
    expect(screen.getByText('代码重构')).toBeTruthy()
    expect(screen.getByText('警告 3 条')).toBeTruthy()
    expect(screen.getByRole('button', { name: '删除骨架草稿 ing_abc123' })).toBeTruthy()
  })

  it('AI 绑定：先预览建议，应用后自动登记为工作流', async () => {
    api.hubGet.mockImplementation((url: string) => url === '/api/ingestion/drafts'
      ? Promise.resolve({ data: { items: [{
        draft_id: 'ing_ai', form: 'workflow', skill_name: '自动绑定演示',
        created_at: '2026-10-26T19:00:00+08:00', warnings: ['骨架'],
        draft_dir: 'C:/repo/data/ingestion/drafts/ing_ai',
        source_ref: 'path:C:/skills/demo', bound_steps: 0, total_steps: 2,
      }] } })
      : Promise.resolve({ ok: true, total: 0, items: [] }))
    const f = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/autobind')) {
        const body = JSON.parse(String(init.body))
        return body.apply
          ? { ok: true, json: async () => ({ data: { report: { bound: 2, total: 2, model: 'fake' }, saved: {} } }) }
          : { ok: true, json: async () => ({ data: { report: { bound: 2, total: 2, model: 'fake', unbound: [] }, workflow: {} } }) }
      }
      if (url.endsWith('/register')) {
        return { ok: true, json: async () => ({ data: { workflow_id: 'wf-demo' } }) }
      }
      return { ok: false, json: async () => ({ detail: 'unexpected' }) }
    })
    vi.stubGlobal('fetch', f)
    vi.stubGlobal('confirm', vi.fn(() => true))
    render(<ReviewPanel />)
    await screen.findByText('ing_ai')
    const registerBtn = screen.getByRole('button', { name: '登记为工作流 ing_ai' }) as HTMLButtonElement
    expect(registerBtn.disabled, '未绑定时不允许登记').toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'AI 绑定 ing_ai' }))
    expect(await screen.findByText(/AI 建议：绑定/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '应用绑定 ing_ai' }))
    await waitFor(() => expect(screen.getByText(/已登记为工作流 wf-demo/)).toBeTruthy())
    expect(f.mock.calls.some((call) => String(call[0]).endsWith('/autobind'))).toBe(true)
    expect(f.mock.calls.some((call) => String(call[0]).endsWith('/register'))).toBe(true)
  })

  it('按技能安装：调 install-skill 并给出结果', async () => {
    api.hubGet.mockImplementation((url: string) => url === '/api/ingestion/drafts'
      ? Promise.resolve({ data: { items: [{
        draft_id: 'ing_sk', form: 'workflow', skill_id: 'code-refactor', skill_name: '代码重构',
        source_ref: 'path:C:/skills/code-refactor', bound_steps: 0, total_steps: 18,
      }] } })
      : Promise.resolve({ ok: true, total: 0, items: [] }))
    const f = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true, json: async () => ({ data: { result: { skill_id: 'code-refactor' } } }),
    }))
    vi.stubGlobal('fetch', f)
    vi.stubGlobal('confirm', vi.fn(() => true))
    render(<ReviewPanel />)
    await screen.findByText('ing_sk')
    fireEvent.click(screen.getByRole('button', { name: '按技能安装 ing_sk' }))
    await waitFor(() => expect(screen.getByText(/已按技能安装：code-refactor/)).toBeTruthy())
    expect(String(f.mock.calls[0][0])).toContain('/api/ingestion/drafts/ing_sk/install-skill')
  })

  it('tool 草稿：AI 实现 → 校验 → 写回 → 登记为工具', async () => {
    let implemented = false
    api.hubGet.mockImplementation((url: string) => url === '/api/ingestion/drafts'
      ? Promise.resolve({ data: { items: [{
        draft_id: 'ing_tool', form: 'tool', skill_id: 'demo_tool', skill_name: '演示工具',
        created_at: '2026-10-26T21:44:22+08:00', warnings: ['骨架'],
        draft_dir: 'C:/repo/data/ingestion/drafts/ing_tool',
        source_ref: 'path:C:/skills/demo', implemented,
      }] } })
      : Promise.resolve({ ok: true, total: 0, items: [] }))
    const f = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/implement')) {
        const body = JSON.parse(String(init.body))
        if (body.apply) {
          implemented = true
          return { ok: true, json: async () => ({ data: { saved: { ok: true } } }) }
        }
        return { ok: true, json: async () => ({ data: {
          validation: { ok: true, errors: [], warnings: [] }, code: 'def demo_tool(**kwargs): pass' } }) }
      }
      if (url.endsWith('/register-tool')) {
        return { ok: true, json: async () => ({ data: { tool_name: 'demo_tool' } }) }
      }
      return { ok: false, json: async () => ({ detail: 'unexpected' }) }
    })
    vi.stubGlobal('fetch', f)
    vi.stubGlobal('confirm', vi.fn(() => true))
    render(<ReviewPanel />)
    await screen.findByText('ing_tool')
    const regBtn = screen.getByRole('button', { name: '登记为工具 ing_tool' }) as HTMLButtonElement
    expect(regBtn.disabled, '未写回实现前不能登记').toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'AI 实现 ing_tool' }))
    expect(await screen.findByText(/静态校验通过/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '应用实现 ing_tool' }))
    await waitFor(() => expect(screen.getByText(/已写回实现草稿/)).toBeTruthy())
    await waitFor(() => expect(
      (screen.getByRole('button', { name: '登记为工具 ing_tool' }) as HTMLButtonElement).disabled,
    ).toBe(false))

    fireEvent.click(screen.getByRole('button', { name: '登记为工具 ing_tool' }))
    await waitFor(() => expect(screen.getByText(/已登记为工具 demo_tool/)).toBeTruthy())
    expect(f.mock.calls.some((call) => String(call[0]).endsWith('/register-tool'))).toBe(true)
  })

  it('审核 / 放行走各自端点', async () => {
    setup()
    await screen.findByText('外来技能A')
    fireEvent.click(screen.getByRole('button', { name: '审核外来技能 ext-ok' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/ext-ok/review', {}))
    fireEvent.click(screen.getByRole('button', { name: '放行外来技能 ext-ok' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/ext-ok/publish'))
  })

  it('批量放行：阻断项必须勾豁免 + 填原因才允许 force', async () => {
    setup()
    await screen.findByText('外来技能B')
    fireEvent.click(screen.getByLabelText('选择外来技能 ext-blocked'))
    fireEvent.click(screen.getByRole('button', { name: '批量放行所选技能' }))

    const confirm = () => screen.getByRole('button', { name: '确认批量放行' }) as HTMLButtonElement
    expect(confirm().disabled).toBe(true)

    fireEvent.click(screen.getByLabelText('确认对阻断项使用 force 豁免'))
    await waitFor(() => expect(confirm().disabled).toBe(true))
    fireEvent.change(screen.getByLabelText('force 豁免原因'), { target: { value: '业务确认后可豁免' } })
    await waitFor(() => expect(confirm().disabled).toBe(false))
    fireEvent.click(confirm())

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/queue/publish-batch', {
      ids: ['ext-blocked'], force: true, reason: '业务确认后可豁免',
    }))
  })

  it('批量放行：未选阻断项不带 force', async () => {
    setup()
    await screen.findByText('外来技能A')
    fireEvent.click(screen.getByLabelText('选择外来技能 ext-ok'))
    fireEvent.click(screen.getByRole('button', { name: '批量放行所选技能' }))
    fireEvent.click(screen.getByRole('button', { name: '确认批量放行' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/queue/publish-batch', {
      ids: ['ext-ok'],
    }))
  })

  it('驳回：取消确认不发请求；确认后 DELETE', async () => {
    setup()
    await screen.findByText('外来技能A')
    const f = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    vi.stubGlobal('fetch', f)
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)

    fireEvent.click(screen.getByRole('button', { name: '驳回外来技能 ext-ok' }))
    expect(f).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: '驳回外来技能 ext-ok' }))
    await waitFor(() => expect(f).toHaveBeenCalled())
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[0]).toBe('/api/skills-mgmt/ext-ok')
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('DELETE')
  })
})
