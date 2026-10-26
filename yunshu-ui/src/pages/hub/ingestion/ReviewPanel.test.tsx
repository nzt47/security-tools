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
