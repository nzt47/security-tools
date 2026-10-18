/** 学习有效性度量 —— 窗口内无样本时全 0 是正常态 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
}))

import LearningMetrics from './LearningMetrics'

afterEach(() => { cleanup(); api.hubGet.mockReset() })

describe('LearningMetrics', () => {
  it('无样本时把全 0 说成正常态', async () => {
    api.hubGet.mockResolvedValue({ days: 7, evaluation: { total: 0, passed: 0, failed: 0, failure_rate: 0 }, kpis: {} })
    render(<LearningMetrics />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/learning/metrics'))
    expect(await screen.findByText(/全 0 是正常态/)).toBeTruthy()
  })

  it('有样本时给出失败率百分比', async () => {
    api.hubGet.mockResolvedValue({ days: 7, evaluation: { total: 4, passed: 3, failed: 1, failure_rate: 0.25 }, kpis: { evolution_adoption_rate: { rate: 0.5 } } })
    render(<LearningMetrics />)
    expect(await screen.findByText('25.0%')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(/全 0 是正常态/)).toBeNull())
  })
})
