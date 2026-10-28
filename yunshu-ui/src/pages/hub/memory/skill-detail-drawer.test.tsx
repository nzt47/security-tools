/**
 * 技能详情抽屉 —— 5 个面板 × 7 个端点
 * ------------------------------------------------------------------
 * 锁死的是**接线本身**（哪个按钮打哪个端点、带什么参数），不是样式：
 * 这 7 条端点此前一条都没接线，只靠"代码里出现了常量"是证明不了的
 * （扫描器要的是「导入 + 调用点」，调用点错了照样红）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import SkillDetailDrawer from './skill-detail-drawer'

const SKILL = { id: 's1', name: 'email-helper' }

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

const openTab = (name: RegExp) => fireEvent.click(screen.getByRole('button', { name }))

describe('SkillDetailDrawer', () => {
  it('使用说明：GET instruction，正文与 token 估算上屏', async () => {
    api.hubGet.mockResolvedValue({ ok: true, instruction: '这是 skill.md 正文', estimated_tokens: 42, layer: 2 })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/instruction'))
    expect(await screen.findByText('这是 skill.md 正文')).toBeTruthy()
    expect(screen.getByText(/42 tokens/)).toBeTruthy()
  })

  it('脚本：列出清单；选中后执行带 script_name；成功结果上屏', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 2, scripts: [{ name: 'run.py', size: 120 }, { name: 'audit.py', size: 80 }] })
    api.hubPost.mockResolvedValue({ ok: true, success: true, exit_code: 0, duration_ms: 15, result: { rows: 3 } })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/脚本 \/ 执行/)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/scripts'))
    await screen.findByText('audit.py')

    fireEvent.click(screen.getByText('audit.py'))
    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/s1/execute', { script_name: 'audit.py' }))
    expect(await screen.findByText(/成功/)).toBeTruthy()
    expect(screen.getByText(/15ms/)).toBeTruthy()
  })

  it('脚本：参数不是合法 JSON 时不发请求（不把坏参数送进沙箱）', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 1, scripts: [{ name: 'main.py' }] })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/脚本 \/ 执行/)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/scripts'))
    fireEvent.change(screen.getByLabelText('执行参数 JSON'), { target: { value: '{oops' } })
    fireEvent.click(screen.getByRole('button', { name: '执行脚本' }))

    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('参数统计：param_stats 与 avoid_params 上屏；调参按钮带 days', async () => {
    api.hubGet.mockResolvedValue({
      ok: true,
      param_stats: { limit: { count: 3, success: 2 } },
      avoid_params: ['timeout_ms'],
      current_default_params: { limit: 10 },
      current_default_hash: 'abc123',
    })
    api.hubPost.mockResolvedValue({ ok: true, recommendations: { limit: 5 } })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/参数统计/)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/param-stats'))
    expect(await screen.findByText('limit')).toBeTruthy()
    expect(screen.getByText(/建议回避的参数/)).toBeTruthy()
    expect(screen.getByText(/提案|abc123|null/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '按反馈调参' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/s1/optimize-with-feedback?days=30', {},
    ))
  })

  it('反馈：聚合上屏；trace_id 为空时禁止提交；填了则 POST', async () => {
    api.hubGet.mockResolvedValue({ ok: true, summary: { total_feedback: 7, like_count: 5, dislike_count: 2, satisfaction_rate_percent: 71 } })
    api.hubPost.mockResolvedValue({ ok: true })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/^反馈$/)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/feedback?days=30'))
    expect(await screen.findByText('71')).toBeTruthy()

    const submit = screen.getByRole('button', { name: '提交反馈' }) as HTMLButtonElement
    expect(submit.disabled, '没有 trace_id 时不该让提交发出去（后端必 400）').toBe(true)

    fireEvent.change(screen.getByLabelText('反馈 trace_id'), { target: { value: 't-1' } })
    expect(submit.disabled).toBe(false)
    fireEvent.click(submit)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/skills-mgmt/s1/feedback', {
      trace_id: 't-1', feedback_type: 'like', rating: 5, comment: '', category: 'other',
    }))
  })

  it('重复：按阈值查重；改阈值后带新值重查', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 1, duplicates: [{ skill_id: 'dup1', name: 'email-helper-2', jaccard: 0.93 }] })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/^重复$/)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/duplicates?min_jaccard=0.7'))
    expect(await screen.findByText('dup1')).toBeTruthy()
    expect(screen.getByText('0.930')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('重复阈值'), { target: { value: '0.9' } })
    fireEvent.click(screen.getByRole('button', { name: '刷新重复技能' }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/skills-mgmt/s1/duplicates?min_jaccard=0.9'))
  })

  it('重复：可就地合并到本技能（带备份），并可用 merge_id 撤销', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 1, duplicates: [{ skill_id: 'dup1', name: 'email-helper-2', jaccard: 0.93 }] })
    api.hubPost.mockImplementation(async (url: string) => {
      if (url === '/api/skills-mgmt/assess/merge-safe') return { ok: true, merge_id: 'mg_1' }
      if (url === '/api/skills-mgmt/assess/merge-undo') return { ok: true }
      return { ok: true }
    })
    render(<SkillDetailDrawer skill={SKILL} onClose={() => {}} />)

    openTab(/^重复$/)
    await screen.findByText('dup1')
    fireEvent.click(screen.getByRole('button', { name: '合并到本技能' }))
    fireEvent.click(screen.getByRole('button', { name: '确认合并' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/assess/merge-safe', { src_id: 'dup1', dst_id: 's1', strategy: 'auto' },
    ))
    expect(await screen.findByText(/合并进本技能/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /撤销合并/ }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/skills-mgmt/assess/merge-undo', { merge_id: 'mg_1' },
    ))
    expect(await screen.findByText(/已撤销合并/)).toBeTruthy()
  })
})
