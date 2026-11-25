/**
 * useLayoutStore × goal driver（E1-2c，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：goal 事件的 auto_continue 必须在本轮结束后用**同一条** sendMessage 续跑；
 * 没有 auto_continue 绝不自动发；切换会话清空 goal。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const streamMock = vi.fn()

vi.mock('../lib/sse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/sse')>()
  return { ...actual, createChatStream: (...args: unknown[]) => streamMock(...args) }
})

import { useLayoutStore } from './useLayoutStore'
import type { GoalState } from '../lib/sse'

function goalState(over: Partial<GoalState> = {}): GoalState {
  return {
    session_id: 's1',
    goal_id: 'g1',
    title: '目标',
    acceptance: ['a'],
    state: 'active',
    round: 1,
    max_rounds: 5,
    ...over,
  }
}

async function* stream(events: unknown[]) {
  for (const e of events) yield e
}

const tick = () => new Promise((r) => setTimeout(r, 0))

afterEach(() => {
  vi.clearAllMocks()
  useLayoutStore.setState({ goal: null, plan: null, messages: [], streaming: false, activeSessionId: null })
})

describe('useLayoutStore · goal driver', () => {
  it('auto_continue ⇒ 本轮结束后自动用 next_prompt 再发一轮', async () => {
    streamMock
      .mockReturnValueOnce(stream([
        { type: 'goal', goal: goalState(), auto_continue: true, next_prompt: '继续推进目标「目标」（第 2/5 轮）' },
        { type: 'done' },
      ]))
      .mockReturnValueOnce(stream([{ type: 'done' }]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('开始做')
    await tick()
    await tick()
    expect(streamMock).toHaveBeenCalledTimes(2)
    expect(String(streamMock.mock.calls[1][0])).toContain('继续推进目标「目标」')
    expect(useLayoutStore.getState().goal?.round).toBe(1)
  })

  it('没有 auto_continue 不自动续跑', async () => {
    streamMock.mockReturnValueOnce(stream([
      { type: 'goal', goal: goalState({ state: 'completed' }) },
      { type: 'done' },
    ]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('做完了')
    await tick()
    expect(streamMock).toHaveBeenCalledTimes(1)
    expect(useLayoutStore.getState().goal?.state).toBe('completed')
  })

  it('切换会话清空 goal', () => {
    useLayoutStore.setState({ activeSessionId: 's1', goal: goalState() })
    useLayoutStore.getState().setActiveSession('s2')
    expect(useLayoutStore.getState().goal).toBeNull()
  })
})
