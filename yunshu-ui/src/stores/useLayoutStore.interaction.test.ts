/**
 * useLayoutStore × 交互事件（E1-3a，2026-11-25）
 * ------------------------------------------------------------------
 * ask 事件写 pendingAsk；present 事件写 presented；新一轮发送清掉上一轮交付卡；
 * 切会话清空两槽。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const streamMock = vi.fn()

vi.mock('../lib/sse', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/sse')>()
  return { ...actual, createChatStream: (...args: unknown[]) => streamMock(...args) }
})

import { useLayoutStore } from './useLayoutStore'
import type { AskState } from '../lib/sse'

const ASK: AskState = {
  question_id: 'q-1',
  question: '选哪个？',
  options: [{ id: 'o1', label: 'A' }],
  allow_multiple: false,
  allow_custom: true,
}

async function* stream(events: unknown[]) {
  for (const e of events) yield e
}

afterEach(() => {
  vi.clearAllMocks()
  useLayoutStore.setState({
    pendingAsk: null, presented: null, messages: [], streaming: false, activeSessionId: null,
  })
})

describe('useLayoutStore · 交互事件', () => {
  it('ask 事件写入 pendingAsk', async () => {
    streamMock.mockReturnValueOnce(stream([{ type: 'ask', ask: ASK }, { type: 'done' }]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('做个选择')
    expect(useLayoutStore.getState().pendingAsk?.question_id).toBe('q-1')
  })

  it('present 事件写入 presented', async () => {
    streamMock.mockReturnValueOnce(stream([
      { type: 'present', files: [{ path: 'a.md' }], note: '交付' },
      { type: 'done' },
    ]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    await useLayoutStore.getState().sendMessage('交付')
    expect(useLayoutStore.getState().presented?.files[0].path).toBe('a.md')
  })

  it('新一轮发送清掉上一轮交付卡（提问卡保留到被回答）', async () => {
    useLayoutStore.setState({ activeSessionId: 's1', presented: { files: [{ path: 'a.md' }], note: '' }, pendingAsk: ASK })
    streamMock.mockReturnValueOnce(stream([{ type: 'done' }]))
    await useLayoutStore.getState().sendMessage('下一轮')
    expect(useLayoutStore.getState().presented).toBeNull()
    expect(useLayoutStore.getState().pendingAsk?.question_id).toBe('q-1')
  })

  it('切换会话清空 pendingAsk / presented', () => {
    useLayoutStore.setState({
      activeSessionId: 's1', pendingAsk: ASK, presented: { files: [{ path: 'a' }], note: '' },
    })
    useLayoutStore.getState().setActiveSession('s2')
    expect(useLayoutStore.getState().pendingAsk).toBeNull()
    expect(useLayoutStore.getState().presented).toBeNull()
  })
})
