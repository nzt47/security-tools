/** 可转换工作流 —— GET convertible；空列表要说成正常态 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
}))

import ConvertibleWorkflows from './ConvertibleWorkflows'

afterEach(() => { cleanup(); api.hubGet.mockReset() })

describe('ConvertibleWorkflows', () => {
  it('空列表说成正常态（转完即消失）', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 0, candidates: [] })
    render(<ConvertibleWorkflows />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/workflow-learning/convertible'))
    expect(await screen.findByText(/空列表是正常态/)).toBeTruthy()
  })

  it('有候选时列出 id 与评分', async () => {
    api.hubGet.mockResolvedValue({ ok: true, total: 1, candidates: [{ id: 'wf_1', title: '周报流程', score: 0.82, runs: 4 }] })
    render(<ConvertibleWorkflows />)
    expect(await screen.findByText('wf_1')).toBeTruthy()
    expect(screen.getByText(/评分 0.82/)).toBeTruthy()
  })
})
