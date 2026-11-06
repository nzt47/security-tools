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
  directIngestion: vi.fn(),
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
  api.directIngestion.mockReset()
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
  it('可切到存量治理 Tab', async () => {
    setup()
    await waitFor(() => expect(api.fetchIngestionSources).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('tab', { name: '存量治理' }))
    expect(screen.getByText('存量资产治理')).toBeTruthy()
    expect(screen.getByRole('button', { name: '盘点并生成计划' })).toBeTruthy()
  })

  it('首屏显示唯一获取入口与来源方式', async () => {
    setup()
    expect(screen.getByRole('heading', { name: '能力获取' })).toBeTruthy()
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

  it('未勾确认不可提交；勾选后以 draft_only 生成技能草稿', async () => {
    setup()
    await previewPlan()
    const commit = screen.getByRole('button', { name: '生成技能草稿' }) as HTMLButtonElement
    expect(commit.disabled, '未确认就允许落库，是很贵的误点').toBe(true)

    fireEvent.click(screen.getByLabelText('我已核对预览计划'))
    await waitFor(() => expect(commit.disabled).toBe(false))

    // S5：能力获取页不再安装/合并 —— 只产出草稿，登记/启用走转化队列
    api.commitIngestion.mockResolvedValue(result({ draft: true, installed: false, queued: false, draft_dir: 'data/ingestion/drafts/ing_x' }))
    fireEvent.click(commit)
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledWith({
      source: { path: 'C:/alpha' }, plan_id: 'ig_1', confirm: true,
      form: 'skill', draft_only: true,
    }))
    expect(await screen.findByText('草稿')).toBeTruthy()
  })

  it('推荐工作流时解释"骨架草稿"，并可一键改为按技能草稿', async () => {
    setup()
    await previewPlan(plan({
      recommendation: { form: 'workflow', reason: '正文有 3 个稳定编号步骤且无脚本', confidence: 0.55 },
    }))
    // 预览后表单默认仍是技能；工作流推荐只作为建议 + 明确后果说明
    expect((screen.getByLabelText('落库形态') as HTMLSelectElement).value).toBe('skill')
    expect(screen.getAllByText(/只产出/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '采用（生成骨架草稿）' }))
    await waitFor(() => expect((screen.getByLabelText('落库形态') as HTMLSelectElement).value).toBe('workflow'))
    fireEvent.click(screen.getByRole('button', { name: '改为按技能草稿' }))
    await waitFor(() => expect((screen.getByLabelText('落库形态') as HTMLSelectElement).value).toBe('skill'))
  })

  it('未勾确认时给出可读的原因（不是死的灰按钮）', async () => {
    setup()
    await previewPlan()
    expect(screen.getByText(/先勾选上方「我已核对预览计划」/)).toBeTruthy()
  })

  it('切到 tool 形态后也只提交草稿（draft_only，无 merge_into）', async () => {
    setup()
    await previewPlan()
    fireEvent.change(screen.getByLabelText('落库形态'), { target: { value: 'tool' } })
    fireEvent.click(screen.getByLabelText('我已核对预览计划'))
    fireEvent.click(screen.getByRole('button', { name: '生成工具草稿' }))
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledWith({
      source: { path: 'C:/alpha' }, plan_id: 'ig_1', confirm: true, form: 'tool', draft_only: true,
    }))
  })

  it('直通：一次调用出草稿（allow_rewrite=true，仍只落草稿）', async () => {
    setup()
    api.directIngestion.mockResolvedValue({
      result: result({ draft: true, installed: false, queued: false, skill_id: 'alpha', draft_dir: 'data/ingestion/drafts/ing_x' }),
      plan: { plan_id: 'ig_1', level: 'L2', adapter: 'docs', rewritten: true, dedupe: [], warnings: [], merge_suggestion: null },
    })
    fireEvent.click(screen.getByRole('tab', { name: '粘贴文本' }))
    fireEvent.change(screen.getByLabelText('粘贴文档或提示词'), { target: { value: '把会议记录整理成待办' } })
    fireEvent.click(screen.getByRole('button', { name: '直通生成草稿' }))
    await waitFor(() => expect(api.directIngestion).toHaveBeenCalledWith(expect.objectContaining({
      text: '把会议记录整理成待办', form: 'skill', allow_rewrite: true, confirm: true,
    })))
    expect(await screen.findByText(/已直通生成草稿/)).toBeTruthy()
    expect(api.commitIngestion).not.toHaveBeenCalled()
  })

  it('批量获取：勾选多条 → 逐条预览 + 只生成草稿（不安装/不注册/不启用）', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.fetchIngestionSources.mockResolvedValue({
      roots: [], stats: { roots: 0, candidates: 2, previewable: 2 },
      candidates: [
        { root: 'C:/root', ecosystem: 'claude', name: 'alpha', path: 'C:/root/alpha/SKILL.md', kind: 'skill', formats: [], previewable: true, symlink: false, warnings: [] },
        { root: 'C:/root', ecosystem: 'claude', name: 'beta', path: 'C:/root/beta/SKILL.md', kind: 'skill', formats: [], previewable: true, symlink: false, warnings: [] },
      ],
    })
    api.previewIngestion.mockResolvedValue(plan())
    api.commitIngestion.mockResolvedValue(result({ draft: true, installed: false, queued: false, draft_dir: 'data/ingestion/drafts/x' }))
    render(<IngestionPage />)
    await screen.findByLabelText('批量选择 alpha')
    const batchBtn = screen.getByRole('button', { name: '批量生成草稿' }) as HTMLButtonElement
    expect(batchBtn.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('批量选择 alpha'))
    fireEvent.click(screen.getByLabelText('批量选择 beta'))
    await waitFor(() => expect(batchBtn.textContent).toContain('（2）'))
    fireEvent.click(batchBtn)
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledTimes(2))
    expect(api.commitIngestion).toHaveBeenCalledWith(expect.objectContaining({ draft_only: true, form: 'skill', confirm: true }))
    expect(confirmSpy).toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('批量获取：L2 候选会自动重写；重写仍不过才跳过', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.fetchIngestionSources.mockResolvedValue({
      roots: [], stats: { roots: 0, candidates: 1, previewable: 1 },
      candidates: [
        { root: 'C:/root', ecosystem: 'claude', name: 'gamma', path: 'C:/root/gamma/SKILL.md', kind: 'skill', formats: [], previewable: true, symlink: false, warnings: [] },
      ],
    })
    api.previewIngestion.mockResolvedValue(plan({ requires_rewrite: true }))
    render(<IngestionPage />)
    await screen.findByLabelText('批量选择 gamma')
    fireEvent.click(screen.getByLabelText('批量选择 gamma'))
    fireEvent.click(screen.getByRole('button', { name: '批量生成草稿' }))
    await screen.findByText('跳过')
    // 自动化：先普通预览，再自动带 rewrite 预览一次；仍不过才跳过
    expect(api.previewIngestion).toHaveBeenCalledTimes(2)
    expect(api.previewIngestion).toHaveBeenLastCalledWith({ path: 'C:/root/gamma/SKILL.md' }, true)
    expect(api.commitIngestion).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('批量获取：L2 自动重写通过后照样出草稿（不用回头单条处理）', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.fetchIngestionSources.mockResolvedValue({
      roots: [], stats: { roots: 0, candidates: 1, previewable: 1 },
      candidates: [
        { root: 'C:/root', ecosystem: 'claude', name: 'delta', path: 'C:/root/delta/SKILL.md', kind: 'doc', formats: [], previewable: true, symlink: false, warnings: [] },
      ],
    })
    api.previewIngestion
      .mockResolvedValueOnce(plan({ requires_rewrite: true }))
      .mockResolvedValueOnce(plan({ requires_rewrite: false, rewritten: true, archive_id: 'ir_9' }))
    api.commitIngestion.mockResolvedValue(result({ draft: true, installed: false, queued: false, draft_dir: 'data/ingestion/drafts/delta' }))
    render(<IngestionPage />)
    await screen.findByLabelText('批量选择 delta')
    fireEvent.click(screen.getByLabelText('批量选择 delta'))
    fireEvent.click(screen.getByRole('button', { name: '批量生成草稿' }))
    await waitFor(() => expect(api.commitIngestion).toHaveBeenCalledWith(expect.objectContaining({
      archive_id: 'ir_9', draft_only: true, confirm: true, form: 'skill',
    })))
    expect(await screen.findByText('草稿（自动重写）')).toBeTruthy()
    confirmSpy.mockRestore()
  })

  it('候选惰加载：首屏 50 条 → 加载更多 → 全部展开（不再硬截断 60 条）', async () => {
    const many = Array.from({ length: 120 }, (_, i) => ({
      root: 'C:/root', ecosystem: 'claude', name: 'cand' + i, path: 'C:/root/cand' + i + '/SKILL.md',
      kind: 'skill', formats: [], previewable: true, symlink: false, warnings: [],
    }))
    api.fetchIngestionSources.mockResolvedValue({
      roots: [], stats: { roots: 0, candidates: 120, previewable: 120 }, candidates: many,
    })
    render(<IngestionPage />)
    await screen.findByLabelText('批量选择 cand0')
    const shown = () => screen.queryAllByLabelText(/批量选择/).length
    expect(shown()).toBe(50)
    expect(screen.getByText(/已显示 50 \/ 120 条/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /加载更多/ }))
    await waitFor(() => expect(shown()).toBe(100))
    fireEvent.click(screen.getByRole('button', { name: '全部展开' }))
    await waitFor(() => expect(shown()).toBe(120))
    expect(screen.getByText('已全部显示')).toBeTruthy()
  })

  it('「探测本机源」在本机可吞噬源卡片右侧（不是页头全局动作）', async () => {
    setup()
    await waitFor(() => expect(api.fetchIngestionSources).toHaveBeenCalledTimes(1))
    const card = screen.getByText('本机可吞噬源').closest('div')?.parentElement
    const btn = screen.getByRole('button', { name: '探测本机源' })
    expect(card?.contains(btn)).toBe(true)
    fireEvent.click(btn)
    await waitFor(() => expect(api.fetchIngestionSources).toHaveBeenCalledTimes(2))
  })

  it('点候选即自动预览（省掉「先填路径再点预览」两次操作）', async () => {
    api.fetchIngestionSources.mockResolvedValue({
      roots: [], stats: { roots: 0, candidates: 1, previewable: 1 },
      candidates: [
        { root: 'C:/root', ecosystem: 'claude', name: 'epsilon', path: 'C:/root/epsilon/SKILL.md', kind: 'skill', formats: [], previewable: true, symlink: false, warnings: [] },
      ],
    })
    api.previewIngestion.mockResolvedValue(plan())
    render(<IngestionPage />)
    fireEvent.click(await screen.findByText('epsilon'))
    await waitFor(() => expect(api.previewIngestion).toHaveBeenCalledWith({ path: 'C:/root/epsilon/SKILL.md' }, false))
    expect((screen.getByLabelText('本机路径') as HTMLInputElement).value).toBe('C:/root/epsilon/SKILL.md')
  })

  it('联机帮助：手册给出 7 站链路、自动/手动步骤清单，并可直达评审', async () => {
    setup()
    fireEvent.click(screen.getByRole('button', { name: '使用手册' }))
    const dlg = await screen.findByRole('dialog', { name: '能力获取使用手册' })
    expect(dlg.querySelectorAll('[data-cp-help-stations="true"] > li').length).toBe(7)
    // 3 站自动（判定 / 重写 / 落草稿）、4 站要人点（入口 / 评审 / 登记 / 启用）
    expect(screen.getAllByText('自动').length).toBe(3)
    expect(screen.getAllByText('需要你点').length).toBe(4)
    expect(screen.getByText(/你要操作的步骤/)).toBeTruthy()
    expect(screen.getByText(/从入口材料到最终能力/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '去草稿评审' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '能力获取使用手册' })).toBeNull())
    expect(screen.getByRole('tab', { name: '草稿评审' }).getAttribute('aria-selected')).toBe('true')
  })
})
