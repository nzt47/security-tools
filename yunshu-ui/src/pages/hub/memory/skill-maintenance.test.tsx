/**
 * 技能中心 · 维护 Tab —— 8 条批量运维端点
 * ------------------------------------------------------------------
 * 重点锁两条**安全语义**，这两条错了比"没接线"更糟：
 *   1. 删除类动作必须先预览；没勾确认时"真删"按钮必须是 disabled；
 *   2. dry_run 的取值由按钮决定（预览=true / 真删=false），不能靠后端默认值兜。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SkillMaintenance from './skill-maintenance'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
  vi.unstubAllGlobals()
})

function baseGets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/skills-mgmt/three-layer/summary') {
      return {
        ok: true,
        summary: {
          layer1_metadata: { skill_count: 12, estimated_tokens_per_skill: 100, estimated_total_tokens: 1200 },
          layer3_tools: { total_scripts: 4, total_temp_files: 1 },
        },
      }
    }
    if (url === '/api/skills-mgmt/cleanup/report') {
      return {
        ok: true, total_skills: 12, orphan_count: 1, unused_count: 1,
        orphans: [{ id: 'orphan-1', reason: '索引有、文件无' }],
        unused: [{ id: 'stale-1', usage_count: 0, idle_days: 40, reason: '零使用且停用闲置 40 天' }],
      }
    }
    if (url.startsWith('/api/skills-mgmt/queue')) {
      return {
        ok: true, total: 2,
        items: [
          { id: 'ext-ok', name: '外来技能A', source: 'github:u/r', review: { review_verdict: 'pass', blocked: false } },
          { id: 'ext-blocked', name: '外来技能B', source: 'url:https://x', review: { review_verdict: 'block', blocked: true } },
        ],
      }
    }
    return { ok: true }
  })
}

describe('SkillMaintenance', () => {
  it('三层摘要与清理总览上屏', async () => {
    baseGets()
    render(<SkillMaintenance />)

    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/three-layer/summary'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/cleanup/report'))
    expect(await screen.findByText('1200')).toBeTruthy()
    expect(screen.getByText('orphan-1')).toBeTruthy()
    expect(screen.getByText('stale-1')).toBeTruthy()
  })

  it('没勾确认时「真删」按钮不可用；预览按钮始终可用', async () => {
    baseGets()
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    const realRemove = screen.getByRole('button', { name: '清除孤儿' }) as HTMLButtonElement
    const preview = screen.getByRole('button', { name: '预览清除孤儿' }) as HTMLButtonElement
    expect(realRemove.disabled, '未确认就允许物理删除，是很贵的误点').toBe(true)
    expect(preview.disabled).toBe(false)
  })

  it('预览走 dry_run=true；勾选后真删走 dry_run=false', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, dry_run: true, found: 1, orphans: [], cleaned: [] })
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    fireEvent.click(screen.getByRole('button', { name: '预览清除孤儿' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/cleanup/orphans', { dry_run: true }))

    fireEvent.click(screen.getByLabelText('确认清除孤儿'))
    fireEvent.click(screen.getByRole('button', { name: '清除孤儿' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/cleanup/orphans', { dry_run: false }))
  })

  it('无用淘汰：阈值只在与填写时才带上', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, dry_run: true, found: 0, candidates: [], removed: [] })
    render(<SkillMaintenance />)
    await screen.findByText('stale-1')

    fireEvent.click(screen.getByRole('button', { name: '预览淘汰无用技能' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/cleanup/unused', { dry_run: true }))

    fireEvent.change(screen.getByLabelText('无用技能闲置天数'), { target: { value: '60' } })
    fireEvent.click(screen.getByRole('button', { name: '预览淘汰无用技能' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/cleanup/unused', { dry_run: true, unused_days: 60 },
    ))
  })

  it('自动合并带阈值与上限', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, scanned_pairs: 3, merged_pairs: [{ src: 'a', dst: 'b' }], skipped: 0 })
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    fireEvent.change(screen.getByLabelText('自动合并阈值'), { target: { value: '0.9' } })
    fireEvent.change(screen.getByLabelText('最多合并对数'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: '自动合并重复技能' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/auto-merge', {
      min_jaccard: 0.9, max_merges: 3,
    }))
    expect(await screen.findByText(/扫描 3 对/)).toBeTruthy()
  })

  it('手工合并：缺 id 时按钮不可用，填齐后带策略提交', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    const mergeBtn = screen.getByRole('button', { name: '合并两个技能' }) as HTMLButtonElement
    expect(mergeBtn.disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('合并源技能 id'), { target: { value: 'a' } })
    fireEvent.change(screen.getByLabelText('合并目标技能 id'), { target: { value: 'b' } })
    fireEvent.change(screen.getByLabelText('合并策略'), { target: { value: 'keep_dst' } })
    expect(mergeBtn.disabled).toBe(false)
    fireEvent.click(mergeBtn)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/merge', {
      src_id: 'a', dst_id: 'b', strategy: 'keep_dst', rebind_feedback: true,
    }))
  })

  it('从记忆抽象：只出草稿（auto_register 默认 false）', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({
      ok: true, total_clusters: 2, passed_clusters: 1, registered_count: 0,
      drafts: [{ draft_name: '每日对账', cluster_size: 4, success_rate: 0.9, quality_gate_passed: true }],
    })
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    fireEvent.click(screen.getByRole('button', { name: '从记忆抽象技能' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/abstract-from-memory', {
      days: 30, max_skills: 5, auto_register: false,
    }))
    expect(await screen.findByText('每日对账')).toBeTruthy()
  })

  it('外部导入待放行：队列上屏；阻断项不给放行；审核/放行走各自端点', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SkillMaintenance />)

    expect(await screen.findByText('外来技能A')).toBeTruthy()
    expect(await screen.findByText('外来技能B')).toBeTruthy()
    // 阻断项：放行按钮不可点（UI 不提供 force 豁免）
    expect((screen.getByRole('button', { name: '放行外来技能 ext-blocked' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '放行外来技能 ext-ok' }) as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '审核外来技能 ext-ok' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/ext-ok/review', {}))

    fireEvent.click(screen.getByRole('button', { name: '放行外来技能 ext-ok' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/ext-ok/publish'))
  })

  // ── 批量放行（#1219）：逐条勾选 + 阻断项受控 force 豁免 ──

  it('批量放行：未勾选时按钮不可点，且不提供全选/一键全放', async () => {
    baseGets()
    render(<SkillMaintenance />)
    await screen.findByText('外来技能A')

    const batch = screen.getByRole('button', { name: '批量放行所选技能' }) as HTMLButtonElement
    expect(batch.disabled).toBe(true)
    // 显式钉住"没有一键全放"：整块面板不应出现"全选"入口
    expect(screen.queryByRole('button', { name: /全选/ })).toBeNull()
    expect(screen.queryByLabelText(/全选/)).toBeNull()

    fireEvent.click(screen.getByLabelText('选择外来技能 ext-ok'))
    await waitFor(() => expect((screen.getByRole('button', { name: '批量放行所选技能' }) as HTMLButtonElement).disabled).toBe(false))
  })

  it('批量放行：未选阻断项 → 面板不带 force，提交只传 ids', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, published: ['ext-ok'], failed: [] })
    render(<SkillMaintenance />)
    await screen.findByText('外来技能A')

    fireEvent.click(screen.getByLabelText('选择外来技能 ext-ok'))
    fireEvent.click(screen.getByRole('button', { name: '批量放行所选技能' }))

    // 没有阻断项 ⇒ 不该出现豁免勾选/原因框
    expect(screen.queryByLabelText('确认对阻断项使用 force 豁免')).toBeNull()
    expect(screen.queryByLabelText('force 豁免原因')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '确认批量放行' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/queue/publish-batch', { ids: ['ext-ok'] },
    ))
  })

  it('批量放行：阻断项必须勾豁免 + 填原因，才允许带 force 提交', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({ ok: true, published: ['ext-blocked'], failed: [] })
    render(<SkillMaintenance />)
    await screen.findByText('外来技能B')

    fireEvent.click(screen.getByLabelText('选择外来技能 ext-blocked'))
    fireEvent.click(screen.getByRole('button', { name: '批量放行所选技能' }))

    const confirm = () => screen.getByRole('button', { name: '确认批量放行' }) as HTMLButtonElement
    expect(confirm().disabled, '没勾豁免就允许放行阻断项，等于没有闸门').toBe(true)
    expect(api.hubPost).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText('确认对阻断项使用 force 豁免'))
    expect(confirm().disabled, '勾了豁免但没填原因仍不可提交').toBe(true)

    fireEvent.change(screen.getByLabelText('force 豁免原因'), { target: { value: '安全评估已人工复核，同意豁免' } })
    await waitFor(() => expect(confirm().disabled).toBe(false))
    fireEvent.click(confirm())

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/queue/publish-batch',
      { ids: ['ext-blocked'], force: true, reason: '安全评估已人工复核，同意豁免' },
    ))
  })

  it('批量放行：部分失败逐条列出，不当成全绿', async () => {
    baseGets()
    api.hubPost.mockResolvedValue({
      ok: false,
      published: ['ext-ok'],
      failed: [{ id: 'ext-blocked', error: '未通过审核，禁止发布', code: 'SKILL_REVIEW_REJECTED' }],
    })
    render(<SkillMaintenance />)
    await screen.findByText('外来技能A')

    fireEvent.click(screen.getByLabelText('选择外来技能 ext-ok'))
    fireEvent.click(screen.getByLabelText('选择外来技能 ext-blocked'))
    fireEvent.click(screen.getByRole('button', { name: '批量放行所选技能' }))
    fireEvent.click(screen.getByLabelText('确认对阻断项使用 force 豁免'))
    fireEvent.change(screen.getByLabelText('force 豁免原因'), { target: { value: '人工复核后豁免' } })
    await waitFor(() => expect((screen.getByRole('button', { name: '确认批量放行' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByRole('button', { name: '确认批量放行' }))

    expect(await screen.findByText(/ext-blocked：未通过审核/)).toBeTruthy()
  })

  // ── 队列去重（#1224）：驳回从评估 Tab 搬到维护 Tab，语义不变 ──

  it('驳回：取消确认不发请求；确认后 DELETE /api/skills-mgmt/<id>', async () => {
    baseGets()
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    vi.stubGlobal('fetch', f)
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    render(<SkillMaintenance />)
    await screen.findByText('外来技能A')

    const reject = () => screen.getByRole('button', { name: '驳回外来技能 ext-ok' }) as HTMLButtonElement
    await waitFor(() => expect(reject().disabled).toBe(false))

    fireEvent.click(reject())
    expect(f, '取消确认就不该动数据').not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    fireEvent.click(reject())
    await waitFor(() => expect(f).toHaveBeenCalled())
    expect(f.mock.calls[0][0]).toBe('/api/skills-mgmt/ext-ok')
    expect((f.mock.calls[0][1] as { method?: string }).method).toBe('DELETE')
    expect(await screen.findByText(/已驳回 ext-ok/)).toBeTruthy()
  })

  it('导入入口已收敛：不再提供上传/扫描/按来源安装控件，只指向吞噬', async () => {
    baseGets()
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    expect(screen.queryByRole('tab', { name: '上传 zip' })).toBeNull()
    expect(screen.queryByRole('tab', { name: '从 .claude 导入' })).toBeNull()
    expect(screen.queryByLabelText('外部技能来源')).toBeNull()
    expect(screen.queryByRole('button', { name: '扫描 .claude/skills' })).toBeNull()
    const link = screen.getByRole('link', { name: '去吞噬获取' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('#/workbench?panel=ingestion')
  })
})
