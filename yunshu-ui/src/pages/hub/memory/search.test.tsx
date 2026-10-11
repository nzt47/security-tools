/**
 * 全库检索（MemorySearch）前端测试 —— 三个检索面统一入口
 *
 * 策略：mock @/api/envelope 与 @/lib/experienceApi，避免真实 HTTP。
 * 覆盖：向量面（query/top_k）、知识库面（question/top_k）、经验库面（searchPreview + 分面不混排）、
 *       经验库后端失败提示。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const envMock = vi.hoisted(() => ({ postEnvelope: vi.fn() }))
vi.mock('@/api/envelope', () => envMock)

const expMock = vi.hoisted(() => ({ searchPreview: vi.fn() }))
vi.mock('@/lib/experienceApi', () => expMock)

import MemorySearch from './search'

beforeEach(() => {
  vi.clearAllMocks()
})

function typeAndSearch(text: string) {
  fireEvent.change(screen.getByPlaceholderText('输入检索关键词…'), { target: { value: text } })
  fireEvent.click(screen.getByText('搜索'))
}

describe('MemorySearch —— 全库检索三分面', () => {
  it('默认向量面：POST /api/vector/search，键为 query/top_k', async () => {
    envMock.postEnvelope.mockResolvedValue({ results: [{ content: '命中A', score: 0.9 }] })
    render(<MemorySearch />)
    typeAndSearch('甲')
    await waitFor(() => expect(envMock.postEnvelope).toHaveBeenCalledTimes(1))
    const [url, body] = envMock.postEnvelope.mock.calls[0]
    expect(String(url)).toContain('/api/vector/search')
    expect(body).toEqual({ query: '甲', top_k: 10 })
    expect(await screen.findByText('命中A')).toBeTruthy()
  })

  it('知识库面：POST /api/knowledge/query，键为 question/top_k', async () => {
    envMock.postEnvelope.mockResolvedValue({ hits: [{ title: '卡片B', content: '知识B' }] })
    render(<MemorySearch />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'knowledge' } })
    typeAndSearch('乙')
    await waitFor(() => expect(envMock.postEnvelope).toHaveBeenCalledTimes(1))
    const [url, body] = envMock.postEnvelope.mock.calls[0]
    expect(String(url)).toContain('/api/knowledge/query')
    expect(body).toEqual({ question: '乙', top_k: 10 })
    expect(await screen.findByText('知识B')).toBeTruthy()
  })

  it('经验库面：走 searchPreview，且不触碰信封 POST（分面不混排）', async () => {
    expMock.searchPreview.mockResolvedValue({
      ok: true,
      hits: [{ id: 'e1', score: 12.5, legs: ['bm25'], task: '经验任务C', task_type: 'bugfix', verified: 'pass' }],
    })
    render(<MemorySearch />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'experience' } })
    typeAndSearch('丙')
    await waitFor(() => expect(expMock.searchPreview).toHaveBeenCalledWith('丙', 10))
    expect(envMock.postEnvelope).not.toHaveBeenCalled()
    expect(await screen.findByText('经验任务C')).toBeTruthy()
    expect(screen.getByText('pass')).toBeTruthy()
  })

  it('经验库后端失败 ⇒ 显示错误而不是静默空', async () => {
    expMock.searchPreview.mockResolvedValue({ ok: false, hits: [], error: '检索不可用' })
    render(<MemorySearch />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'experience' } })
    typeAndSearch('丁')
    expect(await screen.findByText(/检索不可用/)).toBeTruthy()
  })
})
