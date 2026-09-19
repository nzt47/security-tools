/**
 * 思考过程 / 工具调用步骤 —— 数据链路与显示开关测试
 * ------------------------------------------------------------------
 * 对应需求：
 *  - 「恢复工具调用过程和思考过程的显示功能」：SSE 的 thinking 事件（推理内容 + 工具调用）
 *    按序累积到**当前流式回复**的 steps 上（store），并可在对话内联渲染；
 *  - 「重新实现显示/隐藏开关」：useChatPrefsStore.showThinking / showToolCalls 控制
 *    内联渲染（关闭时不渲染 DOM）。
 *
 * 手法：mock lib/sse 产出「推理增量 → 工具调用 running → chunk → 工具调用 done → done」，
 * 走通 sendMessage → store.steps → MessageItem 渲染链路。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

const { mockCreateStream } = vi.hoisted(() => ({ mockCreateStream: vi.fn() }))
vi.mock('../../../lib/sse', () => ({ createChatStream: mockCreateStream }))

// Zustand persist 在模块加载时捕获 localStorage → 必须先打桩再动态 import
vi.stubGlobal('localStorage', {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
  key: vi.fn(() => null),
  length: 0,
})

const [{ useLayoutStore }, { useChatPrefsStore }, { MessageItem }] = await Promise.all([
  import('../../../stores/useLayoutStore'),
  import('../../../stores/useChatPrefsStore'),
  import('./MessageItem'),
])

/** 一份可控流：推理两段 + 一次工具调用 + 正文 */
async function* stepsStream() {
  yield { type: 'thinking', id: 'reasoning', title: '思考过程', detail: '先看用户意图。', status: 'running' }
  yield { type: 'thinking', id: 'reasoning', title: '思考过程', detail: '再决定是否调工具。', status: 'running' }
  yield { type: 'thinking', id: 'tool-real-search', title: '工具调用：search', detail: '参数: {"q":"云枢"}', status: 'running' }
  yield { type: 'chunk', text: '已经查到了。', seq: 1 }
  yield { type: 'thinking', id: 'tool-real-search', title: '工具调用：search', detail: '结果: ok', status: 'done' }
  yield { type: 'done' }
}

/**
 * 真实后端事件序列（plugins/chat.py::_workbench_real_stream 的实际节奏）：
 *   1) 阶段事件 intent/retrieve/plan/tool：先 running（**带 detail**）再 done（**不带 detail**）；
 *   2) 推理流（DeepSeek reasoning_content）：reasoning running 增量若干，随后 done（不带 detail）；
 *   3) 正文 chunk；
 *   4) generate done（不带 detail）。
 * 回归点（线上实测的缺陷）：done 事件不带 detail，若实现把「非 running→running」一律视为
 * 覆盖 detail，则累积的推理文本与阶段说明会被清空 ⇒ 思考/工具块在回复完成后凭空消失，
 * 且两个显示开关点了也没有任何变化（因为已无内容可显隐）。
 */
async function* realSequenceStream() {
  yield { type: 'thinking', id: 'intent', title: '意图识别', detail: '解析输入：你好', status: 'running' }
  yield { type: 'thinking', id: 'intent', title: '意图识别', status: 'done' }
  yield { type: 'thinking', id: 'retrieve', title: '知识检索', detail: '从知识库/记忆召回相关上下文', status: 'running' }
  yield { type: 'thinking', id: 'retrieve', title: '知识检索', status: 'done' }
  yield { type: 'thinking', id: 'plan', title: '规划分解', detail: '拆解任务并确定回答策略', status: 'running' }
  yield { type: 'thinking', id: 'plan', title: '规划分解', status: 'done' }
  yield { type: 'thinking', id: 'tool', title: '工具调用', detail: '按需执行工具（本对话未触发外部工具）', status: 'running' }
  yield { type: 'thinking', id: 'tool', title: '工具调用', status: 'done' }
  yield { type: 'thinking', id: 'reasoning', title: '思考过程', detail: '先寒暄。', status: 'running' }
  yield { type: 'thinking', id: 'reasoning', title: '思考过程', detail: '再自我介绍。', status: 'running' }
  yield { type: 'chunk', text: '你好！我是云枢。', seq: 1 }
  yield { type: 'thinking', id: 'reasoning', title: '思考过程', status: 'done' }
  yield { type: 'thinking', id: 'generate', title: '生成回复', status: 'done' }
  yield { type: 'done' }
}


describe('思考/工具步骤 · 数据链路', () => {
  beforeEach(() => {
    mockCreateStream.mockImplementation(stepsStream)
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false, activeStreamId: null })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('SSE thinking 事件按序累积到流式回复的 steps，并同步到右侧思考面板', async () => {
    await useLayoutStore.getState().sendMessage('查一下云枢')
    const state = useLayoutStore.getState()
    const assistant = state.messages.find((m) => m.role === 'assistant')
    expect(assistant).toBeDefined()
    const steps = assistant?.steps ?? []
    expect(steps.map((s) => s.id)).toEqual(['reasoning', 'tool-real-search'])

    // 推理增量拼接（分片累加而不是互相覆盖）
    const reasoning = steps.find((s) => s.id === 'reasoning')
    expect(reasoning?.detail).toBe('先看用户意图。再决定是否调工具。')

    // 工具步骤：running → done 覆盖状态，保留最后一次详情
    const tool = steps.find((s) => s.id === 'tool-real-search')
    expect(tool?.status).toBe('done')
    expect(tool?.detail).toBe('结果: ok')

    // 右侧「推理链路」面板读的是同一份事件
    expect(state.thinking.map((t) => t.id)).toEqual(['reasoning', 'tool-real-search'])
  })
})

describe('思考/工具步骤 · 真实事件序列（回复完成后不得消失）', () => {
  beforeEach(() => {
    mockCreateStream.mockImplementation(realSequenceStream)
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false, activeStreamId: null })
    useChatPrefsStore.setState({ showThinking: true, showToolCalls: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('done 事件（不带 detail）不清空已累积内容', async () => {
    await useLayoutStore.getState().sendMessage('你好')
    const assistant = useLayoutStore.getState().messages.find((m) => m.role === 'assistant')
    const steps = assistant?.steps ?? []
    expect(steps.map((s) => s.id)).toEqual([
      'intent', 'retrieve', 'plan', 'tool', 'reasoning', 'generate',
    ])

    // 阶段事件：done 之后仍保留 running 阶段给出的说明
    expect(steps.find((s) => s.id === 'intent')?.detail).toBe('解析输入：你好')
    expect(steps.find((s) => s.id === 'tool')?.detail).toBe('按需执行工具（本对话未触发外部工具）')

    // 推理：分片累加 + done 之后不被清空
    const reasoning = steps.find((s) => s.id === 'reasoning')
    expect(reasoning?.status).toBe('done')
    expect(reasoning?.detail).toBe('先寒暄。再自我介绍。')

    // 右侧面板同源
    const rightSide = useLayoutStore.getState().thinking
    expect(rightSide.find((t) => t.id === 'reasoning')?.detail).toBe('先寒暄。再自我介绍。')
  })

  it('流结束后思考过程块仍在，且内容默认可见（可手动折叠）', async () => {
    await useLayoutStore.getState().sendMessage('你好')
    const assistant = useLayoutStore.getState().messages.find((m) => m.role === 'assistant')!
    const { container } = render(<MessageItem message={assistant} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)
    // 默认展开：阶段事件与推理内容直接可见（不再只剩一行标题）
    expect(screen.getByText('意图识别')).toBeInTheDocument()
    expect(screen.getByText(/解析输入：你好/)).toBeInTheDocument()
    expect(screen.getByText(/先寒暄。再自我介绍。/)).toBeInTheDocument()

    // 可折叠：点击块标题（.wb-step-head）后内容收起
    fireEvent.click(container.querySelector('.wb-thought-block .wb-step-head')!)
    expect(screen.queryByText('意图识别')).not.toBeInTheDocument()
  })

  it('显示开关对流结束后的区块依然有效（关→隐，开→显）', async () => {
    await useLayoutStore.getState().sendMessage('你好')
    const assistant = useLayoutStore.getState().messages.find((m) => m.role === 'assistant')!
    const { container, rerender } = render(<MessageItem message={assistant} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)

    // 关闭「思考」→ 区块消失，并给出"已隐藏"提示（开关可见地起作用）
    act(() => useChatPrefsStore.setState({ showThinking: false }))
    rerender(<MessageItem message={assistant} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(0)
    expect(screen.getByText(/思考过程已隐藏/)).toBeInTheDocument()

    // 重新打开 → 区块与内容回来
    act(() => useChatPrefsStore.setState({ showThinking: true }))
    rerender(<MessageItem message={assistant} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)
  })
})

describe('思考/工具步骤 · 显示开关', () => {
  const message = {
    id: 'asst-1',
    role: 'assistant' as const,
    content: '已经查到了。',
    createdAt: Date.now(),
    status: 'done' as const,
    steps: [
      { id: 'reasoning', title: '思考过程', detail: '先看用户意图。', status: 'done' as const, at: 1 },
      // 推理链路的阶段事件（无具体工具名）应归入思考过程，而不是渲染成空工具块
      { id: 'tool', title: '工具调用', detail: '按需执行工具。', status: 'done' as const, at: 2 },
      { id: 'tool-real-search', title: '工具调用：search', detail: '参数: {"q":"云枢"}', status: 'done' as const, at: 3 },
    ],
  }

  beforeEach(() => {
    useChatPrefsStore.setState({ showThinking: true, showToolCalls: true })
  })

  afterEach(cleanup)

  it('默认（两开关都开）同时渲染思考过程与工具调用', () => {
    const { container } = render(<MessageItem message={message} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)
    expect(screen.getByText('🔧 工具调用')).toBeInTheDocument()
    expect(screen.getByText('search')).toBeInTheDocument()
    // 阶段事件「工具调用」（无工具名）归入思考过程，不产生第二个工具块
    expect(container.querySelectorAll('.wb-tool-step')).toHaveLength(1)
  })

  it('关闭思考开关 → 思考过程不渲染（工具调用仍显示，并提示已隐藏步数）', () => {
    useChatPrefsStore.setState({ showThinking: false })
    const { container } = render(<MessageItem message={message} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(0)
    expect(screen.getByText(/思考过程已隐藏（2 步）/)).toBeInTheDocument()
    expect(screen.getByText('🔧 工具调用')).toBeInTheDocument()
  })

  it('关闭工具开关 → 工具调用不渲染（思考过程仍显示）', () => {
    useChatPrefsStore.setState({ showToolCalls: false })
    const { container } = render(<MessageItem message={message} />)
    expect(screen.queryByText('🔧 工具调用')).not.toBeInTheDocument()
    expect(screen.getByText(/工具调用已隐藏（1 步）/)).toBeInTheDocument()
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)
  })

  it('两个开关都关 → 只留正文气泡（两条隐藏提示）', () => {
    useChatPrefsStore.setState({ showThinking: false, showToolCalls: false })
    const { container } = render(<MessageItem message={message} />)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(0)
    expect(screen.queryByText('🔧 工具调用')).not.toBeInTheDocument()
    expect(screen.getByText(/思考过程已隐藏/)).toBeInTheDocument()
    expect(screen.getByText(/工具调用已隐藏/)).toBeInTheDocument()
    expect(screen.getByText('已经查到了。')).toBeInTheDocument()
  })

  it('点击「已隐藏」提示条可一键恢复显示', () => {
    useChatPrefsStore.setState({ showThinking: false, showToolCalls: true })
    const { container } = render(<MessageItem message={message} />)
    fireEvent.click(screen.getByText(/思考过程已隐藏/))
    expect(useChatPrefsStore.getState().showThinking).toBe(true)
    expect(container.querySelectorAll('.wb-thought-block')).toHaveLength(1)
  })

  it('输出格式作为容器类落地（气泡 / 紧凑 / 终端）', () => {
    for (const fmt of ['bubble', 'compact', 'terminal'] as const) {
      useChatPrefsStore.setState({ format: fmt })
      const { container, unmount } = render(<MessageItem message={message} />)
      expect(container.querySelector(`.wb-chat-format-${fmt}`)).not.toBeNull()
      unmount()
    }
  })
})
