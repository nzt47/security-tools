/**
 * useLayoutStore × plan 事件（E1-1，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：SSE 的 plan 事件写进 store.plan（计划卡数据源），且切换会话时清空
 * （否则新会话会短暂显示上一个会话的计划卡）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const streamMock = vi.fn()

vi.mock('../lib/sse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/sse')>()
  return { ...actual, createChatStream: (...args: unknown[]) => streamMock(...args) }
})

import { useLayoutStore } from './useLayoutStore'
import type { PlanState } from '../lib/sse'

function planState(over: Partial<PlanState> = {}): PlanState {
  return {
    session_id: 's1',
    plan_mode: true,
    status: 'pending_approval',
    version: 1,
    plan: '# 计划',
    revisions: [{ version: 1, at: '2026-11-25T00:00:00Z', status: 'pending_approval' }],
    ...over,
  }
}

async function* stream(events: unknown[]) {
  for (const e of events) yield e
}

afterEach(() => {
  vi.clearAllMocks()
  useLayoutStore.setState({ plan: null, messages: [], streaming: false, activeSessionId: null })
})

describe('useLayoutStore · plan 事件', () => {
  it('plan 事件写入 store.plan，chunk 仍进消息正文', async () => {
    streamMock.mockReturnValue(stream([
      { type: 'chunk', text: '计划正文' },
      { type: 'plan', plan: planState() },
      { type: 'done' },
    ]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('帮我规划')

    expect(useLayoutStore.getState().plan?.version).toBe(1)
    expect(useLayoutStore.getState().plan?.status).toBe('pending_approval')
    expect(useLayoutStore.getState().messages.some((m) => m.content.includes('计划正文'))).toBe(true)
  })

  it('plan 事件先于 done 到达时不会被 break 丢掉', async () => {
    streamMock.mockReturnValue(stream([
      { type: 'plan', plan: planState({ version: 2 }) },
      { type: 'chunk', text: 'ok' },
      { type: 'done' },
    ]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('继续')

    expect(useLayoutStore.getState().plan?.version).toBe(2)
  })

  it('切换会话清空计划状态', () => {
    useLayoutStore.setState({ activeSessionId: 's1', plan: planState() })
    useLayoutStore.getState().setActiveSession('s2')
    expect(useLayoutStore.getState().plan).toBeNull()
    expect(useLayoutStore.getState().activeSessionId).toBe('s2')
  })

  it('切回同一会话不清空（避免误丢正在看的计划卡）', () => {
    useLayoutStore.setState({ activeSessionId: 's1', plan: planState() })
    useLayoutStore.getState().setActiveSession('s1')
    expect(useLayoutStore.getState().plan?.version).toBe(1)
  })
})
