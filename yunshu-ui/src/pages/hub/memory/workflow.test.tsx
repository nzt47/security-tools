/** 工作流技能列表 —— 说明/步骤/类型/执行统计 + 「查看」详情弹窗 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

const env = vi.hoisted(() => ({ getEnvelope: vi.fn() }))
vi.mock('@/api/envelope', () => ({ getEnvelope: env.getEnvelope }))

import { MemoryWorkflowTable, workflowChain, workflowSummary } from './workflow'

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
})
