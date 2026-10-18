/** 会话交接文档 —— 必须带选中会话；POST /api/handoff */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubPost: api.hubPost,
}))

import HandoffPanel from './HandoffPanel'

afterEach(() => { cleanup(); api.hubPost.mockReset() })

describe('HandoffPanel', () => {
  it('没选会话时按钮禁用并说明', () => {
    render(<HandoffPanel sessionId="" />)
    expect((screen.getByRole('button', { name: '生成交接文档' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/未选中/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('带上 session_id 与可选 intent，并显示落盘路径与正文', async () => {
    api.hubPost.mockResolvedValue({ ok: true, path: 'C:/tmp/handoff_abc.md', markdown: '# 交接\n- 已完成 X' })
    render(<HandoffPanel sessionId="s-1" />)
    fireEvent.change(screen.getByLabelText('交接意图'), { target: { value: '继续做接线' } })
    fireEvent.click(screen.getByRole('button', { name: '生成交接文档' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/handoff', {
      session_id: 's-1', intent: '继续做接线',
    }))
    expect(await screen.findByText(/handoff_abc\.md/)).toBeTruthy()
    expect(screen.getByText(/# 交接/)).toBeTruthy()
  })
})
