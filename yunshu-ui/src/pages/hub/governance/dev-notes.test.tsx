/**
 * 开发随记（问题与想法）—— UI 用例
 *
 * 锁三件事：
 * 1. 空输入时「记下来」不可点（避免产生空记录）；
 * 2. 提交时带 category=dev-note + feedback_type=suggestion（与用户反馈区分）；
 * 3. 列表里未解决项可「已解决」；已解决默认不显示，勾上才显示。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  hubGet: vi.fn(),
  hubPost: vi.fn(),
}))

vi.mock('../components/ui', () => ({
  ErrorBox: ({ message }: { message: string }) => <div role="alert">{message}</div>,
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

vi.mock('@/api/endpoints', () => ({
  FEEDBACK_LIST: '/api/feedback/list',
  FEEDBACK_SUBMIT: '/api/feedback/submit',
  feedbackResolve: (id: string) => '/api/feedback/' + encodeURIComponent(id) + '/resolve',
}))

import DevNotesPanel from './dev-notes'

function rows(payload: unknown) {
  api.hubGet.mockResolvedValue(payload)
}

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

describe('DevNotesPanel', () => {
  it('空输入不可提交；提交带 dev-note 约定', async () => {
    rows({ success: true, data: [] })
    render(<DevNotesPanel />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalled())

    const save = screen.getByRole('button', { name: '记下来' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('记录问题或想法'), { target: { value: '压缩太频繁' } })
    api.hubPost.mockResolvedValue({ success: true, data: { feedback_id: 'fb_1' } })
    fireEvent.click(save)

    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/feedback/submit',
      expect.objectContaining({
        category: 'dev-note',
        feedback_type: 'suggestion',
        comment: '压缩太频繁',
      }),
    ))
  })

  it('未解决项可标记已解决；已解决默认隐藏', async () => {
    rows({
      success: true,
      data: [
        { feedback_id: 'fb_open', comment: '待办 A', status: 'open' },
        { feedback_id: 'fb_done', comment: '已完成 B', status: 'resolved' },
      ],
    })
    api.hubPost.mockResolvedValue({ success: true })
    render(<DevNotesPanel />)

    await screen.findByText('待办 A')
    expect(screen.queryByText('已完成 B')).toBeNull()

    fireEvent.click(screen.getByLabelText('显示已解决'))
    await screen.findByText('已完成 B')

    fireEvent.click(screen.getByLabelText('标记已解决 fb_open'))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith(
      '/api/feedback/fb_open/resolve',
      expect.objectContaining({ resolution: expect.stringContaining('开发随记') }),
    ))
  })
})
