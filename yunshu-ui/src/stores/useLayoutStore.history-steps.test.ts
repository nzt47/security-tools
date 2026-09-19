/**
 * useLayoutStore · 历史消息步骤恢复（刷新/切会话后思考与工具不消失）
 * ------------------------------------------------------------------
 * 线上反馈「会话里面的思考与工具一会就没了」的最后一环：步骤此前只活在内存里，
 * 刷新页面或切换会话后历史消息不含步骤 ⇒ 内联区块消失。
 * 现在后端随 assistant 消息落盘 steps，前端 loadSessionHistory 需原样恢复。
 *
 * 本测试覆盖：
 *   1. restoreSteps：宽容解析（脏数据丢弃、状态收敛、缺 title 回落 id）；
 *   2. loadSessionHistory：把后端 steps 映射进 ChatMessage.steps（含未知字段忽略）；
 *   3. 无 steps 的历史消息不产生空数组（渲染侧据此判断"无步骤"）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.stubGlobal('localStorage', {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
  key: vi.fn(() => null),
  length: 0,
})

const { useLayoutStore, restoreSteps } = await import('./useLayoutStore')

const HISTORY = [
  { role: 'user', content: '你好', timestamp: '2026-09-19T10:00:00Z' },
  {
    role: 'assistant',
    content: '我是云枢。',
    timestamp: '2026-09-19T10:00:01Z',
    steps: [
      { id: 'intent', title: '意图识别', detail: '解析输入：你好', status: 'done', at: 1 },
      { id: 'tool-real-search', title: '工具调用：search', detail: '结果: ok', status: 'done', at: 2 },
    ],
  },
  { role: 'assistant', content: '（旧消息，无步骤）', timestamp: '2026-09-19T10:00:02Z' },
]

describe('restoreSteps · 宽容解析', () => {
  it('原样恢复 id/title/detail/status', () => {
    const out = restoreSteps([
      { id: 'a', title: '意图识别', detail: 'x', status: 'done', at: 5 },
      { id: 'b', title: '工具调用：search', detail: '结果: ok', status: 'running' },
    ])
    expect(out.map((s) => s.id)).toEqual(['a', 'b'])
    expect(out[0]).toMatchObject({ title: '意图识别', detail: 'x', status: 'done', at: 5 })
    expect(out[1].status).toBe('running')
  })

  it('丢弃无 id / 非对象条目，未知 status 收敛为 done，缺 title 回落 id', () => {
    const out = restoreSteps([
      null,
      'nope',
      { title: '无 id' },
      { id: 'c', status: 'weird' },
    ])
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ id: 'c', title: 'c', status: 'done' })
  })

  it('非数组输入返回空数组（脏 LocalStorage/接口不白屏）', () => {
    expect(restoreSteps(undefined)).toEqual([])
    expect(restoreSteps('x')).toEqual([])
    expect(restoreSteps({})).toEqual([])
  })
})

describe('loadSessionHistory · 步骤恢复', () => {
  beforeEach(() => {
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false, activeSessionId: 's1' })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    useLayoutStore.setState({ messages: [], activeSessionId: null })
  })

  it('把后端 steps 映射进消息，刷新后思考/工具块可继续渲染', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => HISTORY })),
    )
    await useLayoutStore.getState().loadSessionHistory('s1')

    const msgs = useLayoutStore.getState().messages
    expect(msgs).toHaveLength(3)
    expect(msgs[1].steps?.map((s) => s.id)).toEqual(['intent', 'tool-real-search'])
    expect(msgs[1].steps?.[0].detail).toBe('解析输入：你好')
    // 无步骤的历史消息不挂空数组（渲染侧据此判定"无步骤"）
    expect(msgs[2].steps).toBeUndefined()
  })

  it('接口返回脏 steps 时丢弃而不抛错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => [{ role: 'assistant', content: 'hi', steps: 'not-a-list' }],
      })),
    )
    await useLayoutStore.getState().loadSessionHistory('s1')
    expect(useLayoutStore.getState().messages[0].steps).toBeUndefined()
  })
})
