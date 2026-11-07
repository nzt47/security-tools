/** 工作流技能列表 —— 说明/步骤/类型/执行统计 + 「查看」详情弹窗 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

const env = vi.hoisted(() => ({ getEnvelope: vi.fn() }))
vi.mock('@/api/envelope', () => ({ getEnvelope: env.getEnvelope }))

import { MemoryWorkflowTable, workflowChain, workflowSummary } from './workflow'
// 三张表共用的按钮常量：用例断言页面真的用了这一份（不各写一套）
import { TABLE_BTN, TABLE_BTN_PRIMARY, TABLE_BTN_INFO, TABLE_BTN_DANGER } from '@/lib/tableActions'

const WF = {
  id: 'weekly-report',
  name: '周报生成',
  description: '每周汇总 git 提交并生成 markdown 周报',
  workflow_type: 'toolchain',
  status: 'active',
  enabled: true,
  confidence: 0.82,
  success_count: 6,
  failure_count: 1,
  observed_count: 3,
  trigger_patterns: ['周报', '汇总提交'],
  source_user_input: '帮我生成本周周报',
  steps: [
    { step_id: 'step_1', tool_name: 'search_files', params_template: { query: '*.py' }, need_llm: false, condition: null },
    { step_id: 'step_2', tool_name: 'write_file', params_template: { path: 'report.md' }, need_llm: false, condition: null },
  ],
}

afterEach(() => { cleanup(); env.getEnvelope.mockReset() })

describe('MemoryWorkflowTable（工作流技能）', () => {
  it('列表展示说明、步骤链、类型与执行统计（不再只有名称/匹配数）', async () => {
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [WF] })
    render(<MemoryWorkflowTable />)
    expect(await screen.findByText('周报生成')).toBeTruthy()
    expect(screen.getByText('每周汇总 git 提交并生成 markdown 周报')).toBeTruthy()
    expect(screen.getByText('2 步')).toBeTruthy()
    expect(screen.getByText('search_files → write_file')).toBeTruthy()
    expect(screen.getByText('免 LLM 工具链')).toBeTruthy()
    expect(screen.getByText(/成功 6 · 失败 1/)).toBeTruthy()
    expect(env.getEnvelope).toHaveBeenCalledWith('/api/workflow-learning/workflows')
  })

  it('点「查看」打开详情：说明、类型、触发词、步骤工具与参数都可读', async () => {
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [WF] })
    render(<MemoryWorkflowTable />)
    await screen.findByText('周报生成')
    fireEvent.click(screen.getByRole('button', { name: /查看/ }))
    const dialog = await screen.findByRole('dialog')
    const q = within(dialog)
    expect(q.getByText('step_1')).toBeTruthy()
    expect(q.getByText('step_2')).toBeTruthy()
    expect(q.getByText('search_files')).toBeTruthy()
    expect(q.getByText('write_file')).toBeTruthy()
    expect(q.getByText(/触发模式/)).toBeTruthy()
    expect(q.getByText('周报')).toBeTruthy()
    expect(q.getByText(/"path":"report.md"/)).toBeTruthy()
    // 关闭后弹窗消失
    fireEvent.click(q.getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('同名工作流标「同名重复」（不自动合并，交给人工删）', async () => {
    env.getEnvelope.mockResolvedValue({
      ok: true,
      total: 2,
      items: [WF, { ...WF, id: 'weekly-report-2', success_count: 1 }],
    })
    render(<MemoryWorkflowTable />)
    await screen.findAllByText('周报生成')
    expect(screen.getAllByText('同名重复')).toHaveLength(2)
  })

  it('没有 description 时用来源任务兜底，不留空列', async () => {
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [{ ...WF, description: '' }] })
    render(<MemoryWorkflowTable />)
    expect(await screen.findByText('帮我生成本周周报')).toBeTruthy()
  })

  it('workflowSummary/workflowChain 纯函数兜底规则', () => {
    expect(workflowSummary({ id: 'a' } as never)).toBe('（无说明）')
    expect(workflowSummary({ id: 'a', source_user_input: '原始任务' } as never)).toBe('原始任务')
    expect(workflowChain([{ step_id: 's1', tool_name: 't1' }, { step_id: 's2', need_llm: true }])).toBe('t1 → LLM')
    expect(workflowChain([])).toBe('—')
  })
it('说明列最多 3 行自动换行（不再是 truncate 的单行截断）', async () => {
    const long = '这是一段很长的工作流说明，'.repeat(12)
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [{ ...WF, description: long }] })
    render(<MemoryWorkflowTable />)
    const cell = await screen.findByTitle(long)
    // 与技能中心说明列同一口径：line-clamp-3 + wrap-break-word；绝不能是 truncate
    expect(cell.className).toContain('line-clamp-3')
    expect(cell.className).toContain('wrap-break-word')
    expect(cell.className).not.toContain('truncate')
    // 文本仍在 DOM 里（CSS 截断只影响视觉），悬浮/详情能拿到全文
    expect(cell.textContent).toBe(long)
  })
})

describe('工作流表 · 列宽可拖 + 按钮与技能中心同口径（2026-11-08）', () => {
  it('表头 7 列都有拖动手柄，表是 table-fixed + colgroup（与技能中心同一实现）', async () => {
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [WF] })
    const { container } = render(<MemoryWorkflowTable />)
    await screen.findByText('周报生成')

    expect(screen.getAllByRole('separator', { name: '拖动调整列宽' })).toHaveLength(7)
    const table = container.querySelector('table')!
    expect(table.className).toContain('table-fixed')
    const cols = table.querySelectorAll('colgroup > col')
    expect(cols.length).toBe(7)
    // 默认宽度来自模块常量（WORKFLOW_COL_WIDTHS.name = 240）
    expect((cols[0] as HTMLElement).style.width).toBe('240px')
  })

  it('行内按钮用 @/lib/tableActions 的常量：查看中性 / 执行主动作 / 删除破坏性 / 导出信息', async () => {
    env.getEnvelope.mockResolvedValue({ ok: true, total: 1, items: [WF] })
    render(<MemoryWorkflowTable />)
    await screen.findByText('周报生成')

    expect(screen.getByRole('button', { name: /查看/ }).className).toBe(TABLE_BTN)
    expect(screen.getByRole('button', { name: /执行/ }).className).toBe(TABLE_BTN_PRIMARY)
    expect(screen.getByRole('button', { name: /删除/ }).className).toBe(TABLE_BTN_DANGER)
    expect(screen.getByRole('button', { name: /导出为 LLM 参考/ }).className).toBe(TABLE_BTN_INFO)
    // 已启用 ⇒ 动作是「停用」⇒ 中性色（与工具集同一判断）
    expect(screen.getByRole('button', { name: '停用' }).className).toBe(TABLE_BTN)
  })
})
