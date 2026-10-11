/**
 * CognitiveLoopPanel 测试（§7 第三批接入：cognitive/status + cognitive/reject）
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
}))

import CognitiveLoopPanel from './CognitiveLoopPanel'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
})

describe('CognitiveLoopPanel', () => {
  it('JSON 状态 + 接受裁决', async () => {
    api.hubGet.mockImplementation((url: string) => {
      if (url === '/api/cognitive/status') return Promise.resolve({ summary: '一切正常', mode: 'normal' })
      return Promise.resolve({ rejected: false, reason: '模式允许' })
    })
    render(<CognitiveLoopPanel />)
    expect(await screen.findByTestId('cognitive-status')).toHaveTextContent('一切正常')
    expect(screen.getByTestId('cognitive-reject')).toHaveTextContent('接受')
    expect(screen.getByTestId('cognitive-reject')).toHaveTextContent('模式允许')
  })

  it('纯文本状态也能显示，且拒绝裁决上屏', async () => {
    api.hubGet.mockImplementation((url: string) => {
      if (url === '/api/cognitive/status') return Promise.resolve('纯文本状态摘要')
      return Promise.resolve({ rejected: true, reason: '资源不足' })
    })
    render(<CognitiveLoopPanel />)
    expect(await screen.findByTestId('cognitive-status')).toHaveTextContent('纯文本状态摘要')
    expect(screen.getByTestId('cognitive-reject')).toHaveTextContent('拒绝')
    expect(screen.getByTestId('cognitive-reject')).toHaveTextContent('资源不足')
  })

  it('两个接口都失败：上屏错误框', async () => {
    api.hubGet.mockRejectedValue(new Error('HTTP 500'))
    render(<CognitiveLoopPanel />)
    expect(await screen.findByText('认知闭环接口均不可用')).toBeTruthy()
  })
})
