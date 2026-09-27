/**
 * 经验库面板（ExperiencePanel）前端测试（方案 P3）
 *
 * 策略：mock @/lib/experienceApi，避免真实 HTTP。
 * 覆盖：固定提示、统计渲染、列表与详情、审阅动作、批次回滚、检索预览、空态。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const apiMock = vi.hoisted(() => ({
  fetchStats: vi.fn(),
  fetchList: vi.fn(),
  fetchDetail: vi.fn(),
  searchPreview: vi.fn(),
  reviewItem: vi.fn(),
  rollbackBatch: vi.fn(),
  ingestSamples: vi.fn(),
}))

vi.mock('@/lib/experienceApi', () => apiMock)

import ExperiencePanel from './index'

const STATS = {
  corpus_size: 507,
  by_verified: { pass: 368, unverified: 138, fail: 1 },
  by_task_type: { bugfix: 272 },
  by_lang: { python: 300 },
  pitfalls_total: 1290,
  review: {},
  batches: 3,
}

const ITEM = {
  id: 'abc123',
  task: '修复 pytest 断言失败',
  task_type: 'bugfix',
  stack: { lang: 'python', files_changed: 2 },
  diffs: [{ path: 'a.py', op: 'edit', diff: '-old\n+new', bytes: 8, truncated: false }],
  pitfalls: [{ symptom: 'tool error', verified_by: 'tool/result' }],
  verified: 'pass',
  created_at: '2026-09-27T00:00:00',
  review_status: 'pending',
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMock.fetchStats.mockResolvedValue({ ok: true, stats: STATS })
  apiMock.fetchList.mockResolvedValue({ ok: true, total: 1, items: [ITEM] })
  apiMock.fetchDetail.mockResolvedValue({ ok: true, item: ITEM })
  apiMock.searchPreview.mockResolvedValue({ ok: true, hits: [] })
  apiMock.reviewItem.mockResolvedValue({ ok: true })
  apiMock.rollbackBatch.mockResolvedValue({ ok: true, removed: 2, remaining: 0 })
})

describe('ExperiencePanel', () => {
  it('渲染方案要求的固定提示', async () => {
    render(<ExperiencePanel />)
    expect(screen.getByTestId('experience-notice').textContent)
      .toContain('投喂不等于学会')
    // 等首次加载落定，避免 act 警告
    await waitFor(() => expect(apiMock.fetchList).toHaveBeenCalled())
  })

  it('加载并展示统计', async () => {
    render(<ExperiencePanel />)
    await waitFor(() => expect(screen.getByText('507')).toBeTruthy())
    expect(screen.getByText('368')).toBeTruthy()
    expect(screen.getByText('1290')).toBeTruthy()
    expect(screen.getByText('3')).toBeTruthy()
  })

  it('列表展示样本，点击后加载详情', async () => {
    render(<ExperiencePanel />)
    const row = await screen.findByText('修复 pytest 断言失败')
    fireEvent.click(row)
    await waitFor(() => expect(apiMock.fetchDetail).toHaveBeenCalledWith('abc123'))
    // diff 渲染
    expect(await screen.findByText(/-old/)).toBeTruthy()
  })

  it('空库时给出可操作提示', async () => {
    apiMock.fetchList.mockResolvedValue({ ok: true, total: 0, items: [] })
    render(<ExperiencePanel />)
    expect(await screen.findByText(/库为空/)).toBeTruthy()
  })

  it('审阅：入库调用 reviewItem(accept) 并刷新', async () => {
    render(<ExperiencePanel />)
    fireEvent.click(await screen.findByText('修复 pytest 断言失败'))
    const btn = await screen.findByText('入库')
    fireEvent.click(btn)
    await waitFor(() => expect(apiMock.reviewItem).toHaveBeenCalledWith('abc123', 'accept'))
  })

  it('审阅：拒收与废弃走不同 action', async () => {
    render(<ExperiencePanel />)
    fireEvent.click(await screen.findByText('修复 pytest 断言失败'))
    fireEvent.click(await screen.findByText('拒收'))
    await waitFor(() => expect(apiMock.reviewItem).toHaveBeenCalledWith('abc123', 'reject'))
    fireEvent.click(screen.getByText('废弃'))
    await waitFor(() => expect(apiMock.reviewItem).toHaveBeenCalledWith('abc123', 'deprecate'))
  })

  it('批次回滚：未填 batch_id 时按钮禁用', async () => {
    render(<ExperiencePanel />)
    const btn = await screen.findByText('回滚')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('批次回滚：填入后调用并提示结果', async () => {
    render(<ExperiencePanel />)
    const input = await screen.findByLabelText('批次 ID')
    fireEvent.change(input, { target: { value: 'b123' } })
    fireEvent.click(screen.getByText('回滚'))
    await waitFor(() => expect(apiMock.rollbackBatch).toHaveBeenCalledWith('b123'))
    expect(await screen.findByText(/批次已回滚/)).toBeTruthy()
  })

  it('检索预览：命中展示分数与腿', async () => {
    apiMock.searchPreview.mockResolvedValue({
      ok: true,
      hits: [{ id: 'x1', score: 0.0154, legs: ['bm25', 'vector'], task: '命中任务', verified: 'pass' }],
    })
    render(<ExperiencePanel />)
    fireEvent.change(screen.getByLabelText('检索预览输入'), { target: { value: 'pytest' } })
    fireEvent.click(screen.getByText('预览'))
    expect(await screen.findByText('命中任务')).toBeTruthy()
    expect(screen.getByText('0.0154')).toBeTruthy()
    expect(screen.getByText('bm25+vector')).toBeTruthy()
  })

  it('检索无命中时给出 BM25 边界提示', async () => {
    render(<ExperiencePanel />)
    fireEvent.change(screen.getByLabelText('检索预览输入'), { target: { value: 'zzz' } })
    fireEvent.click(screen.getByText('预览'))
    expect(await screen.findByText(/语料过少时 BM25/)).toBeTruthy()
  })

  it('接口报错时展示错误框', async () => {
    apiMock.fetchStats.mockRejectedValue(new Error('HTTP 500'))
    render(<ExperiencePanel />)
    expect(await screen.findByText(/HTTP 500/)).toBeTruthy()
  })
})
