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
 * 2026-10-30 用户报的 6 条（技能中心）里，能落到断言的部分：
 *  · 生成文件轨之后「评审-评估报告」没有内容（实际是「尚未评估」被渲染成 0 分）；
 *  · 发布有没有发生，界面上看不出来（状态徽章是英文枚举、发布按钮不置灰）；
 *  · 「是不是被调用过」没有徽章（只有 0/0/0 数字）；
 *  · 「查看内容」与「详情」重复（详情抽屉已含「使用说明」= skill.md 正文）。
 */
function setup(items: Record<string, unknown>[], runtime: Record<string, unknown>[] = []) {
  api.getEnvelope.mockResolvedValue({ installed: runtime, available: [] })
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt') return { items, total: items.length }
    if (url === '/api/skills-mgmt/classes') return { groups: [] }
    if (url.includes('/assess/events')) return { records: [] }
    if (url.includes('/review/audit')) return { records: [] }
    if (url.includes('/usage/stats')) return { skills: {}, tools: {}, totals: {} }
    return { ok: true }
  })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ records: [] }) })))
  render(<SkillAssetTable />)
}

describe('SkillAssetTable · 评估/发布/调用 可见性', () => {
  it('未评估的技能展开报告时写「尚未评估」，不再渲染 0 分「未发现评估问题」', async () => {
    setup([{ id: 'beta', name: 'Beta', status: 'approved', enabled: true, description: '未评估技能' }])
    await screen.findByText('Beta')
    fireEvent.click(screen.getByRole('button', { name: '展开评审-评估报告' }))
    const panel = await screen.findByTestId('assess-report-empty')
    expect(panel.textContent).toContain('尚未评估')
    expect(panel.textContent).toContain('评审-评估')
    // 旧实现会渲染这四个 0 分格
    expect(screen.queryByText('未发现评估问题（重复/安全/质量/兼容）。')).toBeNull()
  })

  it('状态徽章说人话：published → 已发布；approved → 已通过（未发布）', async () => {
    setup([
      { id: 'p1', name: '已发布技能', status: 'published', enabled: true, review: { auto_assessed: true } },
      { id: 'a1', name: '已通过技能', status: 'approved', enabled: true, review: { auto_assessed: true } },
    ])
    await screen.findByText('已发布技能')
    // 「已发布」会同时出现在状态徽章、注入徽章、发布按钮上 —— 断言至少存在，且英文枚举名不再直接上屏
    expect(screen.getAllByText('已发布').length).toBeGreaterThan(0)
    expect(screen.getByText('已通过（未发布）')).toBeTruthy()
    expect(screen.queryByText('published')).toBeNull()
    expect(screen.queryByText('approved')).toBeNull()
  })

  it('注入徽章不再把「已通过未发布」说成「待发布不触发」', async () => {
    setup([{ id: 'a1', name: '已通过技能', status: 'approved', enabled: true, review: { auto_assessed: true } }])
    await screen.findByText('已通过技能')
    expect(screen.getByText('已通过·注入生效（未发布）')).toBeTruthy()
    expect(screen.queryByText('已启用·待发布不触发')).toBeNull()
  })

  it('没有调用记录时给出「未调用」徽章（不是只有 0/0/0 数字）', async () => {
    setup([{ id: 'n1', name: '没人用过的技能', status: 'published', enabled: true, review: { auto_assessed: true } }])
    await screen.findByText('没人用过的技能')
    expect(screen.getByText('未调用')).toBeTruthy()
  })

  it('已发布的技能：发布按钮置灰且文案为「已发布」；已有文件轨时「生成文件轨」置灰', async () => {
    setup([{ id: 'p1', name: '已发布技能', status: 'published', enabled: true, has_file_track: true, review: { auto_assessed: true } }])
    await screen.findByText('已发布技能')
    const pub = screen.getByRole('button', { name: '已发布' }) as HTMLButtonElement
    expect(pub.disabled).toBe(true)
    fireEvent.click(screen.getByTitle('更多操作：建议 / 版本 / 再定义 / 命令'))
    const mat = screen.getByRole('button', { name: /生成文件轨/ }) as HTMLButtonElement
    expect(mat.disabled).toBe(true)
  })

  it('「查看内容」已合并进「详情」：行内不再有两个入口', async () => {
    setup([{ id: 'p1', name: '已发布技能', status: 'published', enabled: true, review: { auto_assessed: true } }])
    await screen.findByText('已发布技能')
    expect(screen.queryByRole('button', { name: '查看内容' })).toBeNull()
    expect(screen.getByRole('button', { name: '详情' })).toBeTruthy()
  })
})
