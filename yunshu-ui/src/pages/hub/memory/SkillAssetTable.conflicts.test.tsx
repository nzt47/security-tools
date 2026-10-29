import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn(), getEnvelope: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))
vi.mock('@/api/envelope', () => ({ getEnvelope: api.getEnvelope }))
vi.mock('@/lib/callability', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchCapabilityManifest: vi.fn(async () => ({ manifest: { skills: [], vocabulary: { mark: [] } }, runtime_skills: [] })),
}))

import SkillAssetTable from './SkillAssetTable'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  api.getEnvelope.mockReset()
  vi.unstubAllGlobals()
})

/**
 * 2026-10-30：把「交互抢占冲突 / 原生重叠无增量」两句提示变成可执行动作。
 * 这里锁三件事：计划上屏、只对勾选项执行、apply 必须带 confirm=true（不静默动手）。
 */
function setup(plan: Record<string, unknown>) {
  api.getEnvelope.mockResolvedValue({ installed: [], available: [] })
  api.hubPost.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt/conflicts/plan') return plan
    if (url === '/api/skills-mgmt/conflicts/apply') return { ok: true, count: 1, applied: [] }
    return { ok: true }
  })
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt') return { items: [], total: 0 }
    if (url.includes('/assess/events')) return { records: [] }
    if (url.includes('/usage/stats')) return { skills: {}, tools: {}, totals: {} }
    return { ok: true }
  })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ records: [] }) })))
  render(<SkillAssetTable />)
}

describe('技能冲突自动处置（界面）', () => {
  it('计划上屏：显示建议停用的技能与原因，勾选后可确认停用', async () => {
    setup({
      ok: true,
      summary: { scanned: 2, disable: 1, keep: 1, escalate: 0, usage_days: 7 },
      items: [
        { skill_id: 'dup-skill', name: '重复技能', kind: 'trigger_collision', action: 'disable',
          score: 60, reason: '近 7 天 0 命中/0 加载/0 执行，且综合评分 60.0 < 85' },
        { skill_id: 'busy-skill', name: '在用技能', kind: 'native_overlap', action: 'keep',
          score: 95, reason: '近 7 天有使用记录（在用技能不自动停用）' },
      ],
    })
    fireEvent.click(await screen.findByRole('button', { name: /自动处置冲突/ }))
    expect(await screen.findByText('dup-skill')).toBeTruthy()
    expect(screen.getByText('重复技能')).toBeTruthy()
    expect(screen.getByText('在用技能')).toBeTruthy()
    expect(screen.getByText('建议停用')).toBeTruthy()
    expect(screen.getByText('保持启用')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /确认停用\(1\)/ }))
    await waitFor(() => {
      const call = api.hubPost.mock.calls.find((c) => c[0] === '/api/skills-mgmt/conflicts/apply')
      expect(call).toBeTruthy()
      expect(call?.[1]).toMatchObject({ confirm: true })
      expect((call?.[1] as { items: { skill_id: string }[] }).items.map((i) => i.skill_id)).toEqual(['dup-skill'])
    })
  })

  it('没有冲突时给出空态，确认停用按钮不可点', async () => {
    setup({ ok: true, summary: { scanned: 0, disable: 0, keep: 0, escalate: 0 }, items: [] })
    fireEvent.click(await screen.findByRole('button', { name: /自动处置冲突/ }))
    expect(await screen.findByText(/没有需要处置的冲突/)).toBeTruthy()
    const btn = screen.getByRole('button', { name: /确认停用\(0\)/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })
})
