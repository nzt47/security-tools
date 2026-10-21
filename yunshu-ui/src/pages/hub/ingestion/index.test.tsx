/**
 * 吞噬页 —— 预览 → 去重/重写 → 显式确认 → 落库/草稿。
 *
 * 锁三条安全语义：L2 未重写不可提交；未勾确认不可提交；merge_into 只在 skill 形态提交。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  fetchIngestionSources: vi.fn(),
  previewIngestion: vi.fn(),
  commitIngestion: vi.fn(),
}))
vi.mock('./api', () => api)

import IngestionPage from './index'

const SKILL = {
  id: 'alpha', name: 'Alpha', description: '处理 Alpha', version: '1.0.0',
  author: '', category: 'ingested', source: 'ingestion:skill_md', tags: [],
  content_type: 'markdown', scripts: {}, body: '# Alpha',
}

function plan(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 'ig_1', source: {}, source_ref: 'path:C:/alpha', adapter: 'skill_md',
    adapter_scores: {}, conformance: { level: 'L1', reasons: [] },
    recommendation: { form: 'skill', reason: '适配器可补齐', confidence: 0.6 },
    skill: SKILL, actions: ['预览动作'], warnings: [], requires_rewrite: false,
    requires_confirm: true, rewritten: false, archive_id: '', rewrite_errors: [],
    dedupe: [{
      kind: 'existing', id: 'alpha-old', name: 'Alpha Old', similarity: 0.91,
      action: 'merge', strong: true, matched: [], match: 'content', reason: '内容哈希完全一致',
    }],
    merge_suggestion: {
      target_id: 'alpha-old', target_name: 'Alpha Old', jaccard: 0.91,
      action: 'merge', reason: '内容哈希完全一致',
    },
    raw: {}, ...overrides,
  }
}

function result(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 'ig_1', skill_id: 'alpha', name: 'Alpha', version: '1.0.0',
    scripts_count: 0, adapter: 'skill_md', conformance: 'L1', warnings: [],
    installed: true, rewritten: false, archive_id: '', merged: false,
    merged_into: '', removed_id: '', queued: true, form: 'skill', draft: false,
    draft_dir: '', stage: 'borrowed', stage_verdict: 'applied', ...overrides,
  }
}

afterEach(() => {
  cleanup()
  api.fetchIngestionSources.mockReset()
  api.previewIngestion.mockReset()
  api.commitIngestion.mockReset()
})

function setup() {
  api.fetchIngestionSources.mockResolvedValue({
    roots: [], candidates: [], stats: { roots: 0, candidates: 0, previewable: 0 },
  })
  render(<IngestionPage />)
}

async function previewPlan(p = plan()) {
  api.previewIngestion.mockResolvedValue(p)
  fireEvent.change(screen.getByLabelText('本机路径'), { target: { value: 'C:/alpha' } })
  fireEvent.click(screen.getByRole('button', { name: '预览计划' }))
  await screen.findByText('alpha')
}

describe('IngestionPage', () => {
  it('首屏显示唯一获取入口与来源方式', async () => {
    setup()
    expect(screen.getByRole('heading', { name: '吞噬' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '本机路径' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '粘贴文本' })).toBeTruthy()
    await waitFor(() => expect(api.fetchIngestionSources).toHaveBeenCalled())
  })

  it('预览后显示 L1、去重建议与合并目标', async () => {
    setup()
    await previewPlan()
    expect(api.previewIngestion).toHaveBeenCalledWith({ path: 'C:/alpha' }, false)
    expect(screen.getAllByText('L1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('alpha-old').length).toBeGreaterThan(0)
    expect(screen.getByText('建议合并到')).toBeTruthy()
  })

  it('L2 必须走 LLM 重写，重写后再提交', async () => {
    setup()
    const l2 = plan({
      conformance: { level: 'L2', reasons: ['不符合'] }, requires_rewrite: true,
      skill: null, recommendation: { form: 'skill', reason: '需重写', confidence: 0.3 },
      dedupe: [], merge_suggestion: null,
    })
    api.previewIngestion.mockResolvedValueOnce(l2).mockResolvedValueOnce(plan({ rewritten: true, archive_id: 'ir_1' }))
    fireEvent.change(screen.getByLabelText('本机路径'), { target: { value: 'C:/doc' } })
    fireEvent.click(screen.getByRole('button', { name: '预览计划' }))
    await screen.findAllByText('需重写')
    const rewrite = screen.getByRole('button', { name: 'LLM 重写并预览' })
    fireEvent.click(rewrite)
    await waitFor(() => expect(api.previewIngestion).toHaveBeenLastCalledWith({ path: 'C:/doc' }, true))
    await screen.findByText('alpha')
  })

  it('未勾确认不可提交；勾选后提交带 merge_into', async () => {
    setup()
    await previewPlan()
    const commit = screen.getByRole('button', { name: '安装为技能' }) as HTMLButtonElement
    expect(commit.disabled, '未确认就允许落库，是很贵的误点').toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '填入合并目标' }))
    fireEvent.click(screen.getByLabelText('我已核对预览计划'))
    const mergeCommit = screen.getByRole('button', { name: '合并进 alpha-old' }) as HTMLButtonElement
    await waitFor(() => expect(mergeCommit.disabled).toBe(false))

    api.commitIngestion.mockResolvedValue(result({ merged: true, merged_into: 'alpha-old', installed: true, queued: false }))
    fireEvent.click(mergeCommit)
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledWith({
      source: { path: 'C:/alpha' }, plan_id: 'ig_1', confirm: true,
      form: 'skill', merge_into: 'alpha-old',
    }))
    expect(await screen.findByText('已合并')).toBeTruthy()
  })

  it('切到 tool 形态后只提交草稿，不带 merge_into', async () => {
    setup()
    await previewPlan()
    fireEvent.change(screen.getByLabelText('落库形态'), { target: { value: 'tool' } })
    fireEvent.click(screen.getByLabelText('我已核对预览计划'))
    fireEvent.click(screen.getByRole('button', { name: '生成工具草稿' }))
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledWith({
      source: { path: 'C:/alpha' }, plan_id: 'ig_1', confirm: true, form: 'tool',
    }))
  })
})
