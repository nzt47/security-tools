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
    pendingAsk: null, presented: null, changes: null, messages: [], streaming: false,
    activeSessionId: null,
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

const CHANGES = {
  session_id: 's1',
  scope: 'session' as const,
  checkpoint: { id: 'cp-1', created_at: '', trigger_tool: 'edit' },
  files: [],
  counts: { files: 0, added: 0, removed: 0, truncated: 0 },
}

describe('useLayoutStore · 变更卡（E1-3b）', () => {
  it('setChanges 写入 / 传 null 清空', () => {
    useLayoutStore.getState().setChanges(CHANGES)
    expect(useLayoutStore.getState().changes?.checkpoint?.id).toBe('cp-1')
    useLayoutStore.getState().setChanges(null)
    expect(useLayoutStore.getState().changes).toBeNull()
  })

  it('新一轮发送清掉上一轮变更卡（只反映最近一轮）', async () => {
    useLayoutStore.setState({ activeSessionId: 's1', changes: CHANGES })
    streamMock.mockReturnValueOnce(stream([{ type: 'done' }]))
    await useLayoutStore.getState().sendMessage('下一轮')
    expect(useLayoutStore.getState().changes).toBeNull()
  })

  it('切换会话清空 changes', () => {
    useLayoutStore.setState({ activeSessionId: 's1', changes: CHANGES })
    useLayoutStore.getState().setActiveSession('s2')
    expect(useLayoutStore.getState().changes).toBeNull()
  })
})

describe('useLayoutStore · 流中提交（E1-3a 修补）', () => {
  it('正在生成时收到的消息排队，本轮结束后自动发出（不静默丢弃）', async () => {
    streamMock.mockReturnValue(stream([{ type: 'chunk', text: '本轮回答' }, { type: 'done' }]))
    useLayoutStore.setState({ activeSessionId: 's1' })
    const first = useLayoutStore.getState().sendMessage('第一轮')
    // 模拟：提问卡出现时本轮还没结束，用户提交了答案
    await useLayoutStore.getState().sendMessage('回答（问题「选哪种存储？」）：选项 postgres')
    expect(streamMock).toHaveBeenCalledTimes(1)          // 没并发生成
    await first                                          // 本轮流结束
    await new Promise((r) => setTimeout(r, 0))            // 让排队的 send 起来
    expect(streamMock).toHaveBeenCalledTimes(2)
    expect(String(streamMock.mock.calls[1][0])).toContain('回答（问题')
    expect(
      useLayoutStore.getState().messages.some((m) => m.content.includes('回答（问题')),
    ).toBe(true)
  })
})
