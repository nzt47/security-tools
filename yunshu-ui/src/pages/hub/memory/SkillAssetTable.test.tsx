import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

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

function setup() {
  api.getEnvelope.mockResolvedValue({
    installed: [
      { id: 'alpha', name: 'Alpha', enabled: true, class_name: '交流与人格' },
      { id: 'runtime-only', name: '仅运行时分身', enabled: true, class_name: '未分类' },
    ],
    available: [],
  })
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt') {
      return {
        items: [{
          id: 'alpha', name: 'Alpha', description: '资产技能', status: 'published', enabled: true,
          review: { auto_assessed: true, review_verdict: 'ok', security_score: 90, quality_score: 80, compatibility_score: 70 },
        }],
      }
    }
    if (url === '/api/skills-mgmt/classes') return { groups: [{ name: '交流与人格', skills: [{ id: 'alpha' }] }] }
    if (url.includes('/assess/events')) return { records: [] }
    if (url.includes('/review/audit')) return { records: [] }
    return { ok: true }
  })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ records: [] }) })))
  render(<SkillAssetTable />)
}

describe('SkillAssetTable（LLM 统一表）', () => {
  it('资产行显示状态/评审/评分，并保留评审动态入口', async () => {
    setup()
    expect(await screen.findByText('Alpha')).toBeTruthy()
    expect(screen.getByText('已自动评估')).toBeTruthy()
    expect(screen.getByText('90/80/70')).toBeTruthy()
    expect(screen.getByRole('button', { name: '评审动态' })).toBeTruthy()
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt'))
  })

  it('仅运行时的技能也出现在表里，并标明无资产记录', async () => {
    setup()
    expect(await screen.findByText('仅运行时分身')).toBeTruthy()
    expect(screen.getByText('仅运行时·无资产记录')).toBeTruthy()
    expect(screen.getByText('无资产记录')).toBeTruthy()
  })
})
