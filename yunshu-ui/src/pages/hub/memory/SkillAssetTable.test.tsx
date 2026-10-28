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

  it('表头对账：运行时 2 · 资产 1 · 仅运行时 1', async () => {
    setup()
    await screen.findByText('Alpha')
    expect(screen.getByText('运行时 2 · 资产 1 · 仅运行时 1')).toBeTruthy()
  })

  it('不再提示已退役的「维护」Tab / 已删除的「资产评审」Tab', async () => {
    setup()
    await screen.findByText('Alpha')
    expect(screen.queryByText(/待放行队列/)).toBeNull()
    expect(screen.queryByText(/资产评审/)).toBeNull()
  })

  it('一键归类未分类(N)：N 取运行时未分类数，点击同时驱动运行时与资产两侧', async () => {
    setup()
    const btn = await screen.findByRole('button', { name: '一键归类未分类(1)' })
    fireEvent.click(btn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/classes/run-auto'))
    expect(api.hubPost).toHaveBeenCalledWith('/api/skills/classify/run-auto')
  })

  it('登记仅运行时技能：先 dry-run 预览候选，确认后带 dry_run=false 落库', async () => {
    setup()
    await screen.findByText('Alpha')
    api.hubPost.mockImplementation(async (url: string, body?: { dry_run?: boolean }) => {
      if (url === '/api/skills-mgmt/backfill') {
        return body?.dry_run === false
          ? { ok: true, dry_run: false, candidates: [], created: [{ id: 'runtime-only' }], skipped: [] }
          : {
            ok: true, dry_run: true,
            candidates: [{ id: 'runtime-only', name: '仅运行时分身', source: 'legacy_migration' }],
            created: [], skipped: [],
          }
      }
      return { ok: true }
    })
    fireEvent.click(screen.getByRole('button', { name: /登记仅运行时技能/ }))
    expect(await screen.findByText('登记仅运行时技能为资产')).toBeTruthy()
    expect(screen.getByText('legacy_migration')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /确认登记/ }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/backfill', { dry_run: false }))
  })

  it('低频动作收进「更多」菜单（建议/版本/再定义/命令仍有入口）', async () => {
    setup()
    await screen.findByText('Alpha')
    const menu = document.querySelector('table details')
    expect(menu).toBeTruthy()
    for (const label of ['建议', '版本', '再定义', '命令']) {
      expect(menu?.textContent).toContain(label)
    }
  })


  it('再定义：应用草稿走 redraft/apply 写 skill.md（不再 PATCH 主轨），且不再有重复的 LLM 勾选', async () => {
    setup()
    await screen.findByText('Alpha')
    api.hubPost.mockImplementation(async (url: string) => {
      if (url === '/api/skills-mgmt/alpha/redraft') {
        return { ok: true, source: 'rules', current: { name: 'Alpha', description: '资产技能' },
          proposed: { name: 'Alpha 中文名', description: '把资产整理成可复用的技能说明。' } }
      }
      if (url === '/api/skills-mgmt/alpha/redraft/apply') return { ok: true, applied: ['description_zh'] }
      return { ok: true }
    })
    fireEvent.click(screen.getByRole('button', { name: /再定义/ }))
    expect(await screen.findByText(/再定义草稿/)).toBeTruthy()
    // 起草按钮已经区分 LLM/规则，额外的勾选是重复入口（用户要求去掉）
    expect(screen.queryByLabelText('起草时用 LLM')).toBeNull()
    await waitFor(() => expect(screen.getByDisplayValue(/把资产整理成/)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /应用草稿并评审/ }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/alpha/redraft/apply',
      { name: 'Alpha 中文名', description_zh: '把资产整理成可复用的技能说明。', source: 'rules' },
    ))
    // 不能再用 PATCH（那条白名单不含 description/description_zh，必然「200 但没改」）
    const calls = (globalThis.fetch as unknown as { mock?: { calls: unknown[][] } }).mock?.calls ?? []
    expect(calls.some((c) => (c[1] as { method?: string } | undefined)?.method === 'PATCH')).toBe(false)
    expect(await screen.findByText(/已写入 中文说明/)).toBeTruthy()
  })

  it('全部折叠：分组视图下一键收起所有分类行', async () => {
    setup()
    await screen.findByText('Alpha')
    fireEvent.click(screen.getByRole('button', { name: '按分类折叠' }))
    // 分组头出现（组头 + 行内分类徽章都会出现该类名，故用 findAllByText）
    await screen.findAllByText('交流与人格')
    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    await waitFor(() => expect(screen.queryByText('Alpha')).toBeNull())
    // 组头仍在（只是成员被收起），且按钮变成「全部展开」
    expect(screen.getAllByText('交流与人格').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '全部展开' })).toBeTruthy()
  })
})
