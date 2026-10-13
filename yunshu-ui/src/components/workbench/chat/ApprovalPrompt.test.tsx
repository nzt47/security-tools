/**
 * ApprovalPrompt 测试 —— 内联审批弹窗（2026-10-13）
 * ------------------------------------------------------------------
 * 钉住三件事：
 *   ① 没有待审批 ⇒ 不渲染；
 *   ② 点「批准并继续」⇒ 走审批链（approveRecord）并**自动**发一句续跑指令；
 *   ③ 点「拒绝」但没填原因 ⇒ 阻断（审计要求），不发请求、不发消息。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApprovalPrompt } from './ApprovalPrompt'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { approveRecord, rejectRecord } from '../../../lib/approvalActions'

vi.mock('../../../lib/approvalActions', () => ({
  approveRecord: vi.fn().mockResolvedValue(undefined),
  rejectRecord: vi.fn().mockResolvedValue(undefined),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  useLayoutStore.setState({ pendingApproval: null })
})

function withPending(send: (text: string) => Promise<void>) {
  useLayoutStore.setState({
    pendingApproval: { tool: 'shell_execute', approvalId: 'AP-1', message: '需要人工审批' },
    sendMessage: send,
  })
}

describe('ApprovalPrompt · 内联审批弹窗', () => {
  it('没有待审批时不渲染', () => {
    render(<ApprovalPrompt />)
    expect(screen.queryByTestId('approval-prompt')).toBeNull()
  })

  it('批准 ⇒ 走审批链并自动发送续跑指令', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    withPending(send)
    render(<ApprovalPrompt />)

    fireEvent.click(screen.getByTestId('approval-approve'))

    await waitFor(() => expect(vi.mocked(approveRecord)).toHaveBeenCalledWith('AP-1', ''))
    await waitFor(() => expect(send).toHaveBeenCalled())
    expect(String(send.mock.calls[0][0])).toContain('已批准审批单 AP-1')
    expect(useLayoutStore.getState().pendingApproval).toBeNull()
  })

  it('拒绝未填原因 ⇒ 阻断且不发请求', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    withPending(send)
    render(<ApprovalPrompt />)

    fireEvent.click(screen.getByTestId('approval-reject'))

    expect(screen.getByText('驳回必须填写原因（审计要求）')).toBeTruthy()
    expect(vi.mocked(rejectRecord)).not.toHaveBeenCalled()
    expect(send).not.toHaveBeenCalled()
  })

  it('拒绝并填原因 ⇒ 走驳回链并自动发送续跑指令', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    withPending(send)
    render(<ApprovalPrompt />)

    fireEvent.change(screen.getByTestId('approval-note'), { target: { value: '不要动生产库' } })
    fireEvent.click(screen.getByTestId('approval-reject'))

    await waitFor(() => expect(vi.mocked(rejectRecord)).toHaveBeenCalledWith('AP-1', '不要动生产库'))
    await waitFor(() => expect(send).toHaveBeenCalled())
    expect(String(send.mock.calls[0][0])).toContain('已拒绝审批单 AP-1')
  })
})
