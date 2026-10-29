/**
 * 引擎 · 定时任务 —— 「零 token」三态徽章（2026-10-29）
 * ------------------------------------------------------------------
 * 用户要求：**不允许有「未标注」**。口径：
 *   零 token（绿）      调用链不含 LLM
 *   零 token（条件，琥珀）是否零 token 取决于运行时开关（如 dry_run），当前配置下为零
 *   含 LLM（灰）        调用链含 LLM
 *   未声明（灰）        后端没声明 —— 后端已全部补齐，这里锁"未声明"只在缺声明时出现
 * 判据说明走 title 悬浮，不塞进表格正文（列宽固定）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  getEnvelope: vi.fn(),
  hubGet: vi.fn(),
  hubPost: vi.fn(),
  hubDelete: vi.fn(),
}))

vi.mock('@/api/envelope', () => ({ getEnvelope: mocks.getEnvelope }))
vi.mock('../components/ui', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../components/ui')>()
  return { ...orig, hubGet: mocks.hubGet, hubPost: mocks.hubPost, hubDelete: mocks.hubDelete }
})

const { default: EngineScheduler } = await import('./scheduler')

const TASKS = [
  { name: '维护·健康快照', task_id: 't1', type: 'python_func', enabled: true, schedule_text: '每日 17:00', kind: 'cron', zero_token: true, zero_token_note: '只读巡检', zero_token_conditional: false, owner: 'agent/maintenance/zero_token_jobs.py' },
  { name: '周期进化', task_id: 't2', type: 'python_func', enabled: true, schedule_text: '每 604800 秒', kind: 'interval', zero_token: true, zero_token_note: 'dry_run=true 时只筛候选（只读），不生成变异体', zero_token_conditional: true, owner: 'agent/skills_mgmt/evolution_scheduler.py' },
  { name: '生成周报', task_id: 't3', type: 'python_func', enabled: true, schedule_text: '每日 09:00', kind: 'cron', zero_token: false, zero_token_note: '含 LLM 汇总', zero_token_conditional: false, owner: 'x' },
  { name: '未声明任务', task_id: 't4', type: 'python_func', enabled: false, schedule_text: '每 60 秒', kind: 'interval', zero_token: null, owner: '' },
]

describe('引擎 · 定时任务「零 token」三态', () => {
  afterEach(cleanup)

  it('四种状态都有明确落点，且不再出现「未标注」字样', async () => {
    mocks.getEnvelope.mockImplementation(async (url: string) => {
      if (String(url).includes('scheduler/overview')) return { tasks: TASKS }
      return { tasks: [], total: 0 }
    })
    render(<EngineScheduler />)

    await waitFor(() => expect(screen.getByText('维护·健康快照')).toBeInTheDocument())
    // 按行断言：列头本身就叫「零 token」，全局查询会撞上表头
    const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement
    expect(within(rowOf('维护·健康快照')).getByText('零 token')).toBeInTheDocument()
    expect(within(rowOf('周期进化')).getByText('零 token（条件）')).toBeInTheDocument()
    expect(within(rowOf('生成周报')).getByText('含 LLM')).toBeInTheDocument()
    expect(within(rowOf('未声明任务')).getByText('未声明')).toBeInTheDocument()
    expect(screen.queryByText('未标注')).toBeNull()

    // 条件性的判据说明挂在 title 上（悬浮可见），不是凭空一个「零 token」
    const badge = within(rowOf('周期进化')).getByText('零 token（条件）').closest('span[title]')
    expect(badge?.getAttribute('title')).toContain('dry_run')
  })
})
