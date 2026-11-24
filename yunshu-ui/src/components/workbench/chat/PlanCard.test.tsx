/**
 * PlanCard 测试 —— 计划模式计划卡（E1-1，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住四件事：
 *   ① 待批准 ⇒ 只显示计划与版本，不把"批准"做成永远可点的假按钮；
 *   ② 批准 ⇒ 走 decision（approve）并把 next_message 作为用户消息发出（开始执行）；
 *   ③ 反馈为空 ⇒ 按钮禁用（不发请求、不发消息）；有反馈 ⇒ 走 feedback 并发送；
 *   ④ 回滚 ⇒ 走 decision（rollback, version）且**不**发送任何消息。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PlanCard } from './PlanCard'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { decidePlan, setPlanMode } from '../../../lib/planApi'
import type { PlanState } from '../../../lib/sse'

vi.mock('../../../lib/planApi', () => ({
  decidePlan: vi.fn(),
  setPlanMode: vi.fn(),
}))

function makePlan(over: Partial<PlanState> = {}): PlanState {
  return {
    session_id: 's1',
    plan_mode: true,
    status: 'pending_approval',
    version: 2,
    plan: '# 计划\n\n1. 读代码\n2. 改配置',
    revisions: [
      { version: 1, at: '2026-11-25T01:00:00Z', status: 'rejected' },
      { version: 2, at: '2026-11-25T02:00:00Z', status: 'pending_approval' },
    ],
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  useLayoutStore.setState({ plan: null })
})

describe('PlanCard · 计划模式', () => {
  it('展示计划全文与待批准状态', () => {
    render(<PlanCard plan={makePlan()} />)
    expect(screen.getByTestId('plan-card')).toBeTruthy()
    expect(screen.getByTestId('plan-status').textContent).toContain('待批准')
    expect(screen.getByText(/读代码/)).toBeTruthy()
  })

  it('批准 ⇒ 调用 decision(approve) 并发送 next_message', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(decidePlan).mockResolvedValue({
      ...makePlan(), ok: true, status: 'approved', plan_mode: false,
      next_message: '计划已批准，开始按计划执行。',
    })
    render(<PlanCard plan={makePlan()} />)
    fireEvent.click(screen.getByTestId('plan-approve'))

    await waitFor(() => expect(vi.mocked(decidePlan)).toHaveBeenCalledWith(
      's1', 'approve', { feedback: undefined, version: undefined }))
    await waitFor(() => expect(send).toHaveBeenCalledWith('计划已批准，开始按计划执行。'))
    expect(useLayoutStore.getState().plan?.status).toBe('approved')
  })

  it('没有待批准计划时批准按钮禁用', () => {
    render(<PlanCard plan={makePlan({ status: 'planning' })} />)
    expect((screen.getByTestId('plan-approve') as HTMLButtonElement).disabled).toBe(true)
  })

  it('反馈为空 ⇒ 按钮禁用且不发请求', () => {
    render(<PlanCard plan={makePlan()} />)
    expect((screen.getByTestId('plan-feedback-submit') as HTMLButtonElement).disabled).toBe(true)
    expect(vi.mocked(decidePlan)).not.toHaveBeenCalled()
  })

  it('填写反馈后 ⇒ decision(feedback) 并发送反馈文本', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(decidePlan).mockResolvedValue({
      ...makePlan(), ok: true, status: 'planning', next_message: '先补测试',
    })
    render(<PlanCard plan={makePlan()} />)
    fireEvent.change(screen.getByTestId('plan-feedback'), { target: { value: '先补测试' } })
    fireEvent.click(screen.getByTestId('plan-feedback-submit'))

    await waitFor(() => expect(vi.mocked(decidePlan)).toHaveBeenCalledWith(
      's1', 'feedback', { feedback: '先补测试', version: undefined }))
    await waitFor(() => expect(send).toHaveBeenCalledWith('先补测试'))
  })

  it('回滚 ⇒ decision(rollback, version) 且不发送消息', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(decidePlan).mockResolvedValue({ ...makePlan(), ok: true, version: 3, next_message: null })
    render(<PlanCard plan={makePlan()} />)
    fireEvent.click(screen.getByTestId('plan-rollback-1'))

    await waitFor(() => expect(vi.mocked(decidePlan)).toHaveBeenCalledWith(
      's1', 'rollback', { feedback: undefined, version: 1 }))
    expect(send).not.toHaveBeenCalled()
  })

  it('退出计划模式 ⇒ 调用 setPlanMode(false)', async () => {
    vi.mocked(setPlanMode).mockResolvedValue({ ...makePlan(), plan_mode: false, status: 'idle' })
    render(<PlanCard plan={makePlan()} />)
    // 退出按钮以 title 定位（ghost 小按钮，文案「退出」）
    fireEvent.click(screen.getByTitle('退出计划模式（计划文件与历史保留）'))
    await waitFor(() => expect(vi.mocked(setPlanMode)).toHaveBeenCalledWith('s1', false))
  })

  it('请求失败 ⇒ 显示可读错误且不发送消息', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(decidePlan).mockRejectedValue(new Error('需要 API 令牌'))
    render(<PlanCard plan={makePlan()} />)
    fireEvent.click(screen.getByTestId('plan-approve'))
    await waitFor(() => expect(screen.getByTestId('plan-error').textContent).toContain('需要 API 令牌'))
    expect(send).not.toHaveBeenCalled()
  })
})
