import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

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
 * 2026-10-30 用户反馈：「合并」按钮/选项找不到。
 * 事实是合并能力早就有（/assess/merge-safe + undo），但入口只在「详情 → 重复 Tab」，
 * 且那个 Tab 的提示语还写着「只报告；合并归到别处」。
 * 这里锁两条新入口：行内「更多 → 合并重复…」与评估报告里的「合并重复…」。
 */
function setup(items: Record<string, unknown>[]) {
  api.getEnvelope.mockResolvedValue({ installed: [], available: [] })
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt') return { items, total: items.length }
    if (url === '/api/skills-mgmt/classes') return { groups: [] }
    if (url.includes('/assess/events')) return { records: [] }
    if (url.includes('/review/audit')) return { records: [] }
    if (url.includes('/usage/stats')) return { skills: {}, tools: {}, totals: {} }
    if (url.includes('/duplicates')) return { ok: true, total: 1, duplicates: [{ skill_id: 'dup-twin', name: '重复双胞胎', jaccard: 0.91 }] }
    return { ok: true }
  })
  api.hubPost.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt/assess/merge-safe') return { ok: true, merge_id: 'm-123' }
    return { ok: true }
  })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ records: [] }) })))
  render(<SkillAssetTable />)
}

describe('合并重复技能入口', () => {
  it('行内「更多 → 合并重复…」能打开弹窗，并提交 merge-safe（dst=本技能）', async () => {
    setup([{ id: 'alpha', name: 'Alpha', status: 'published', enabled: true, review: { auto_assessed: true } }])
    await screen.findByText('Alpha')
    fireEvent.click(screen.getByTitle('更多操作：建议 / 版本 / 再定义 / 命令'))
    fireEvent.click(screen.getByRole('button', { name: /合并重复…/ }))
    const modal = await screen.findByTestId('merge-duplicates-modal')
    expect(within(modal).getByText('dup-twin')).toBeTruthy()
    fireEvent.click(within(modal).getByRole('button', { name: '合并到本技能' }))
    fireEvent.click(within(modal).getByRole('button', { name: '确认合并' }))
    await waitFor(() => {
      const call = api.hubPost.mock.calls.find((c) => c[0] === '/api/skills-mgmt/assess/merge-safe')
      expect(call).toBeTruthy()
      expect(call?.[1]).toEqual({ src_id: 'dup-twin', dst_id: 'alpha', strategy: 'auto' })
    })
    expect(await within(modal).findByRole('button', { name: /撤销合并（m-123）/ })).toBeTruthy()
  })

  it('评估报告里出现重复/重叠发现项时，报告面板也有「合并重复…」出口', async () => {
    setup([{ id: 'beta', name: 'Beta', status: 'approved', enabled: true,
      review: { auto_assessed: true, review_verdict: 'ok', score: 80,
        findings: [{ code: 'OVL_OPERATION_OVERLAP', severity: 'warn', message: '与 X 操作重叠' }] } }])
    await screen.findByText('Beta')
    fireEvent.click(screen.getByRole('button', { name: '展开评审-评估报告' }))
    await screen.findByText(/评审-评估报告/)
    // 行内「更多」菜单里也有同名按钮 ⇒ 用 testid 锁定报告面板里那个
    const btn = await screen.findByTestId('report-merge-btn')
    fireEvent.click(btn)
    expect(await screen.findByTestId('merge-duplicates-modal')).toBeTruthy()
  })
})
