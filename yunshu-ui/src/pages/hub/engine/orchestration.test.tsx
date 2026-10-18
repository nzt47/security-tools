/**
 * 编排与蒸馏 —— 4 条端点
 * 锁：语义层 GET + 覆盖层提交（非法 JSON 不发请求）；规划开关需确认；
 * 蒸馏的 query/paths 至少一个（本地先拦，不让用户点完再看 400）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import EngineOrchestration from './orchestration'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url === '/api/orchestrator/semantic-config') {
      return { ok: true, config: { enabled: true, min_score: 0.45, top_k: 5 }, api_override: { top_k: 3 } }
    }
    if (url === '/api/process-distill/health') return { ok: true, llm_configured: true, artifacts: 2 }
    return {}
  })
}

describe('EngineOrchestration', () => {
  it('语义层配置与蒸馏健康并发拉取', async () => {
    gets()
    render(<EngineOrchestration />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/orchestrator/semantic-config'))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/process-distill/health'))
    expect(await screen.findByText(/"min_score": 0.45/)).toBeTruthy()
  })

  it('覆盖层非法 JSON 不发请求', async () => {
    gets()
    render(<EngineOrchestration />)
    await screen.findByLabelText('语义层覆盖层 JSON')
    fireEvent.change(screen.getByLabelText('语义层覆盖层 JSON'), { target: { value: '{oops' } })
    fireEvent.click(screen.getByRole('button', { name: '提交语义层配置' }))
    expect(await screen.findByText(/不是合法 JSON/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('规划开关需确认后才 POST {enabled}', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, planning_enabled: false })
    render(<EngineOrchestration />)
    await screen.findByLabelText('语义层覆盖层 JSON')

    const off = screen.getByRole('button', { name: '停用规划引擎' }) as HTMLButtonElement
    expect(off.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('确认切换规划引擎'))
    fireEvent.click(off)
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/planning/toggle', { enabled: false }))
  })

  it('蒸馏缺 query 与 paths 时本地拦住并说明原因', async () => {
    gets()
    render(<EngineOrchestration />)
    await screen.findByLabelText('蒸馏 query')
    fireEvent.click(screen.getByRole('button', { name: '执行蒸馏' }))
    expect(await screen.findByText(/至少提供一个/)).toBeTruthy()
    expect(api.hubPost).not.toHaveBeenCalled()
  })

  it('蒸馏带 query 时 POST {query, paths, top_k}', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, artifacts: [] })
    render(<EngineOrchestration />)
    await screen.findByLabelText('蒸馏 query')
    fireEvent.change(screen.getByLabelText('蒸馏 query'), { target: { value: '把排障流程固化成工作流' } })
    fireEvent.change(screen.getByLabelText('蒸馏素材路径'), { target: { value: 'docs/a.md, docs/b.md' } })
    fireEvent.click(screen.getByRole('button', { name: '执行蒸馏' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/process-distill/distill', {
      query: '把排障流程固化成工作流', paths: ['docs/a.md', 'docs/b.md'], top_k: 5,
    }))
  })
})
