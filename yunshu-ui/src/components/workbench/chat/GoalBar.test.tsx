/**
 * GoalBar 测试（E1-2d，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：无目标不占位；暂停/继续/清除走 API 并回写 store；完成后不能恢复；
 * 展开可看验收条件并追加一条。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { GoalBar } from './GoalBar'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { acceptGoal, setGoalMode } from '../../../lib/goalApi'
import type { GoalState } from '../../../lib/sse'

vi.mock('../../../lib/goalApi', () => ({
  setGoalMode: vi.fn(),
  acceptGoal: vi.fn(),
}))

function makeGoal(over: Partial<GoalState> = {}): GoalState {
  return {
    session_id: 's1',
    goal_id: 'g1',
    title: '把目标系统做完',
    acceptance: ['驱动跑通', 'UI 可见'],
    state: 'active',
    round: 1,
    max_rounds: 5,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  useLayoutStore.setState({ goal: null })
})

describe('GoalBar · 目标进度条', () => {
  it('无目标时不渲染', () => {
    render(<GoalBar goal={makeGoal({ goal_id: '', state: 'idle' })} />)
    expect(screen.queryByTestId('goal-bar')).toBeNull()
  })

  it('展示标题/状态/轮次', () => {
    render(<GoalBar goal={makeGoal()} />)
    expect(screen.getByTestId('goal-bar')).toBeTruthy()
    expect(screen.getByText('把目标系统做完')).toBeTruthy()
    expect(screen.getByTestId('goal-state').textContent).toContain('进行中')
    expect(screen.getByTestId('goal-round').textContent).toContain('1/5')
  })

  it('暂停 ⇒ 调用 API 并回写 store', async () => {
    vi.mocked(setGoalMode).mockResolvedValue(makeGoal({ state: 'paused' }))
    render(<GoalBar goal={makeGoal()} />)
    fireEvent.click(screen.getByTestId('goal-pause'))
    await waitFor(() => expect(vi.mocked(setGoalMode)).toHaveBeenCalledWith('s1', 'pause', '用户暂停'))
    await waitFor(() => expect(useLayoutStore.getState().goal?.state).toBe('paused'))
  })

  it('暂停态显示继续并恢复', async () => {
    vi.mocked(setGoalMode).mockResolvedValue(makeGoal({ state: 'active' }))
    render(<GoalBar goal={makeGoal({ state: 'paused' })} />)
    fireEvent.click(screen.getByTestId('goal-resume'))
    await waitFor(() => expect(vi.mocked(setGoalMode)).toHaveBeenCalledWith('s1', 'resume'))
    await waitFor(() => expect(useLayoutStore.getState().goal?.state).toBe('active'))
  })

  it('已完成：不能恢复且显示已完成', () => {
    render(<GoalBar goal={makeGoal({ state: 'completed' })} />)
    expect(screen.getByTestId('goal-state').textContent).toContain('已完成')
    expect((screen.getByTestId('goal-resume') as HTMLButtonElement).disabled).toBe(true)
  })

  it('阻塞时展示 code 与原因', () => {
    render(<GoalBar goal={makeGoal({ state: 'blocked', blocked: { code: 'need_user', reason: '等确认' } })} />)
    expect(screen.getByTestId('goal-blocked').textContent).toContain('need_user')
    expect(screen.getByTestId('goal-blocked').textContent).toContain('等确认')
  })

  it('清除 ⇒ 调 API 且回写 idle', async () => {
    vi.mocked(setGoalMode).mockResolvedValue(makeGoal({ state: 'idle', goal_id: '' }))
    render(<GoalBar goal={makeGoal()} />)
    fireEvent.click(screen.getByTestId('goal-clear'))
    await waitFor(() => expect(vi.mocked(setGoalMode)).toHaveBeenCalledWith('s1', 'clear'))
    await waitFor(() => expect(useLayoutStore.getState().goal?.state).toBe('idle'))
  })

  it('展开详情显示验收条件并可追加', async () => {
    vi.mocked(acceptGoal).mockResolvedValue(makeGoal({ acceptance: ['驱动跑通', 'UI 可见', '新条件'] }))
    render(<GoalBar goal={makeGoal()} />)
    fireEvent.click(screen.getByTestId('goal-expand'))
    expect(screen.getByText('驱动跑通')).toBeTruthy()
    fireEvent.change(screen.getByTestId('goal-accept-input'), { target: { value: '新条件' } })
    fireEvent.click(screen.getByTestId('goal-accept-submit'))
    await waitFor(() => expect(vi.mocked(acceptGoal)).toHaveBeenCalledWith('s1', '新条件'))
    await waitFor(() => expect(useLayoutStore.getState().goal?.acceptance).toContain('新条件'))
  })

  it('API 失败 ⇒ 显示可读错误', async () => {
    vi.mocked(setGoalMode).mockRejectedValue(new Error('需要 API 令牌'))
    render(<GoalBar goal={makeGoal()} />)
    fireEvent.click(screen.getByTestId('goal-pause'))
    await waitFor(() => expect(screen.getByTestId('goal-error').textContent).toContain('需要 API 令牌'))
  })
})
