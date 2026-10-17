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

  it('上传 zip：走 multipart，令牌来自 authHeader（不能走 hubPost）', async () => {
    baseGets()
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true, skill: { skill_id: 's9' } }) }))
    vi.stubGlobal('fetch', fetchMock)
    render(<SkillMaintenance />)
    await screen.findByText('orphan-1')

    const file = new File(['zip-bytes'], 'demo.zip', { type: 'application/zip' })
    fireEvent.change(screen.getByLabelText('选择技能 zip 包'), { target: { files: [file] } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/skills-mgmt/upload')
    expect(init.method).toBe('POST')
    expect(init.body instanceof FormData).toBe(true)
    expect((init.body as FormData).get('file')).toBeTruthy()
    expect(await screen.findByText(/已安装：s9/)).toBeTruthy()
  })
})
