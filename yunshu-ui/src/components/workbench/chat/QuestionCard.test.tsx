/**
 * QuestionCard 测试（E1-3a，2026-11-25）
 * ------------------------------------------------------------------
 * 钉住：未选择时不能提交；单选/多选/自定义；提交走 answerAsk 并用 next_message 续跑；
 * 提交后清空 pendingAsk；失败显示可读错误。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QuestionCard } from './QuestionCard'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { answerAsk } from '../../../lib/askApi'
import type { AskState } from '../../../lib/sse'

vi.mock('../../../lib/askApi', () => ({
  answerAsk: vi.fn(),
  getPendingAsk: vi.fn(),
}))

function makeAsk(over: Partial<AskState> = {}): AskState {
  return {
    question_id: 'q-1',
    question: '用哪种存储？',
    options: [
      { id: 'sqlite', label: 'SQLite' },
      { id: 'pg', label: 'PostgreSQL', description: '需要服务端' },
    ],
    allow_multiple: false,
    allow_custom: true,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  useLayoutStore.setState({ pendingAsk: null, activeSessionId: 's1' })
})

describe('QuestionCard · 结构化提问', () => {
  it('渲染问题与选项，未选择时提交禁用', () => {
    render(<QuestionCard ask={makeAsk()} />)
    expect(screen.getByTestId('question-text').textContent).toContain('用哪种存储')
    expect(screen.getByText('SQLite')).toBeTruthy()
    expect((screen.getByTestId('question-submit') as HTMLButtonElement).disabled).toBe(true)
  })

  it('单选 ⇒ 提交 answerAsk 并用 next_message 续跑', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(answerAsk).mockResolvedValue({
      ok: true, question_id: 'q-1', answers: ['pg'], custom: '',
      next_message: '回答（问题「用哪种存储？」）：选项 pg',
    })
    render(<QuestionCard ask={makeAsk()} />)
    fireEvent.click(screen.getByLabelText('PostgreSQL'))
    fireEvent.click(screen.getByTestId('question-submit'))
    await waitFor(() => expect(vi.mocked(answerAsk)).toHaveBeenCalledWith(
      's1', 'q-1', ['pg'], ''))
    await waitFor(() => expect(send).toHaveBeenCalledWith('回答（问题「用哪种存储？」）：选项 pg'))
    expect(useLayoutStore.getState().pendingAsk).toBeNull()
  })

  it('多选允许选两个', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(answerAsk).mockResolvedValue({
      ok: true, question_id: 'q-1', answers: ['sqlite', 'pg'], custom: '', next_message: 'x',
    })
    render(<QuestionCard ask={makeAsk({ allow_multiple: true })} />)
    fireEvent.click(screen.getByLabelText('SQLite'))
    fireEvent.click(screen.getByLabelText('PostgreSQL'))
    fireEvent.click(screen.getByTestId('question-submit'))
    await waitFor(() => expect(vi.mocked(answerAsk)).toHaveBeenCalledWith(
      's1', 'q-1', ['sqlite', 'pg'], ''))
  })

  it('只填自定义也能提交', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    useLayoutStore.setState({ sendMessage: send })
    vi.mocked(answerAsk).mockResolvedValue({
      ok: true, question_id: 'q-1', answers: [], custom: '用 Redis', next_message: 'y',
    })
    render(<QuestionCard ask={makeAsk()} />)
    fireEvent.change(screen.getByTestId('question-custom'), { target: { value: '用 Redis' } })
    fireEvent.click(screen.getByTestId('question-submit'))
    await waitFor(() => expect(vi.mocked(answerAsk)).toHaveBeenCalledWith(
      's1', 'q-1', [], '用 Redis'))
  })

  it('失败显示可读错误且不清卡片', async () => {
    vi.mocked(answerAsk).mockRejectedValue(new Error('需要 API 令牌'))
    const ask = makeAsk()
    useLayoutStore.setState({ pendingAsk: ask })
    render(<QuestionCard ask={ask} />)
    fireEvent.click(screen.getByLabelText('SQLite'))
    fireEvent.click(screen.getByTestId('question-submit'))
    await waitFor(() => expect(screen.getByTestId('question-error').textContent).toContain('需要 API 令牌'))
    expect(useLayoutStore.getState().pendingAsk).toEqual(ask)
  })
})
