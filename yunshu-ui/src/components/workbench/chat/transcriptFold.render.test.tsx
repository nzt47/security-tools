/**
 * MessageItem · 工作细节折叠（2026-11-28）
 * ------------------------------------------------------------------
 * 钉住：紧凑档把完成轮的过程收成一行；点开还原；冗长档保持原样。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { MessageItem } from './MessageItem'
import { DEFAULT_STYLE, useChatPrefsStore } from '../../../stores/useChatPrefsStore'
import type { ChatMessage } from '../../../stores/useLayoutStore'

const message: ChatMessage = {
  id: 'asst-test',
  role: 'assistant',
  content: '这是最终回复。',
  createdAt: Date.now(),
  status: 'done',
  steps: [
    { id: 's1', title: '阶段：分析', detail: '先看上下文', status: 'done', at: 1 },
    { id: 's2', title: '工具调用：ppt_read', detail: 'path: a.pptx', status: 'done', at: 2 },
  ],
}

beforeEach(() => {
  useChatPrefsStore.setState({ ...DEFAULT_STYLE, showThinking: true, showToolCalls: true })
})

afterEach(() => {
  cleanup()
})

describe('MessageItem · 工作细节', () => {
  it('紧凑档：完成轮折叠成「过程 · N 步」，点开还原', () => {
    useChatPrefsStore.setState({ transcriptView: 'compact' })
    render(<MessageItem message={message} turnMeta={{ turnId: 'u1', turnClosed: false, isLastAssistantInTurn: true }} />)

    expect(screen.getByText('过程 · 2 步')).toBeTruthy()
    expect(screen.queryByText('思考过程')).toBeNull()

    fireEvent.click(screen.getByText('过程 · 2 步'))
    expect(screen.getByText('思考过程')).toBeTruthy()
    expect(screen.getByText('收起过程')).toBeTruthy()
    // 回复本体始终可见
    expect(screen.getByText('这是最终回复。')).toBeTruthy()
  })

  it('详细档：当前轮不折叠（与改版前一致）', () => {
    useChatPrefsStore.setState({ transcriptView: 'detailed' })
    render(<MessageItem message={message} turnMeta={{ turnId: 'u1', turnClosed: false, isLastAssistantInTurn: true }} />)
    expect(screen.queryByText('过程 · 2 步')).toBeNull()
    expect(screen.getByText('思考过程')).toBeTruthy()
  })

  it('冗长档：永不折叠', () => {
    useChatPrefsStore.setState({ transcriptView: 'verbose' })
    render(<MessageItem message={message} turnMeta={{ turnId: 'u1', turnClosed: true, isLastAssistantInTurn: true }} />)
    expect(screen.queryByText('过程 · 2 步')).toBeNull()
    expect(screen.getByText('思考过程')).toBeTruthy()
  })
})
