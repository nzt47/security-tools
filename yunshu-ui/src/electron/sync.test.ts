/**
 * 跨窗口同步 · 防信息倒退测试
 * ------------------------------------------------------------------
 * 场景（Electron 主窗口 + 分离窗口）：分离窗口广播的快照可能早于主窗口刚生成的回复，
 * 若直接覆盖，主窗口里刚出现的「思考过程 / 工具调用」（挂在消息 steps 上）会被整条抹掉
 * —— 线上表现即"思考/工具先出现、过一会就没了，显示开关也随之失效"。
 *
 * 不变量：
 *   - 本窗口流式中 → 忽略对方快照（不清空本轮内容）；
 *   - 对方快照条数更少（疑似旧快照）→ 忽略；
 *   - 对方快照不更旧 → 正常应用（保持跨窗口一致）；
 *   - "清空会话"（0 条）在本地非流式时仍然传播。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StateSyncPayload } from './ipc'

type SyncCb = (payload: StateSyncPayload) => void

/** 最小 electronAPI 桩：只需 onStateSync / broadcastState */
function installElectronStub() {
  let cb: SyncCb | null = null
  const broadcastState = vi.fn()
  ;(window as unknown as { electronAPI: unknown }).electronAPI = {
    onStateSync: (fn: SyncCb) => {
      cb = fn
      return () => {
        cb = null
      }
    },
    broadcastState,
  }
  return { emit: (p: StateSyncPayload) => cb?.(p), broadcastState }
}

const msg = (id: string, withSteps = true) => ({
  id,
  role: 'assistant' as const,
  content: `内容 ${id}`,
  createdAt: 1,
  status: 'done' as const,
  ...(withSteps
    ? { steps: [{ id: 'reasoning', title: '思考过程', detail: '推理内容', status: 'done' as const, at: 1 }] }
    : {}),
})

describe('startCrossWindowSync · 防信息倒退', () => {
  let stub: ReturnType<typeof installElectronStub>
  let stop: () => void

  beforeEach(async () => {
    stub = installElectronStub()
    const [{ startCrossWindowSync }, { useLayoutStore }] = await Promise.all([
      import('./sync'),
      import('../stores/useLayoutStore'),
    ])
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false })
    stop = startCrossWindowSync()
  })

  afterEach(async () => {
    stop?.()
    delete (window as unknown as { electronAPI?: unknown }).electronAPI
    const { useLayoutStore } = await import('../stores/useLayoutStore')
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false })
    vi.restoreAllMocks()
  })

  it('流式生成中拒绝对方快照（本轮思考/工具步骤不被抹掉）', async () => {
    const { useLayoutStore } = await import('../stores/useLayoutStore')
    useLayoutStore.setState({ messages: [msg('a1'), msg('a2')], streaming: true })
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    stub.emit({ type: 'snapshot', messages: [msg('a1', false)], thinking: [] })

    expect(useLayoutStore.getState().messages).toHaveLength(2)
    expect(useLayoutStore.getState().messages[1].steps).toHaveLength(1)
  })

  it('非流式但对方快照更旧（条数更少）时同样拒绝', async () => {
    const { useLayoutStore } = await import('../stores/useLayoutStore')
    useLayoutStore.setState({ messages: [msg('a1'), msg('a2'), msg('a3')] })
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    stub.emit({ type: 'snapshot', messages: [msg('a1')], thinking: [] })

    expect(useLayoutStore.getState().messages).toHaveLength(3)
  })

  it('对方快照不更旧时正常应用（跨窗口一致）', async () => {
    const { useLayoutStore } = await import('../stores/useLayoutStore')
    useLayoutStore.setState({ messages: [msg('a1')] })

    stub.emit({ type: 'snapshot', messages: [msg('a1'), msg('a2')], thinking: [] })

    expect(useLayoutStore.getState().messages.map((m) => m.id)).toEqual(['a1', 'a2'])
  })

  it('「清空会话」（0 条）在非流式时仍然生效', async () => {
    const { useLayoutStore } = await import('../stores/useLayoutStore')
    useLayoutStore.setState({ messages: [msg('a1')] })

    stub.emit({ type: 'snapshot', messages: [], thinking: [] })

    expect(useLayoutStore.getState().messages).toHaveLength(0)
  })
})
