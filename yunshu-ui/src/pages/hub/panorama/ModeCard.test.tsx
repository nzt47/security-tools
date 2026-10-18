/** 运行模式 —— GET /api/mode；不接受任务时要显式标出 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
}))

import ModeCard from './ModeCard'

afterEach(() => { cleanup(); api.hubGet.mockReset() })

describe('ModeCard', () => {
  it('渲染 label / description 与「可接新任务」', async () => {
    api.hubGet.mockResolvedValue({ mode: 'normal', label: '正常模式', description: '我感觉很好', can_accept_tasks: true })
    render(<ModeCard />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/mode'))
    expect(await screen.findByText('正常模式')).toBeTruthy()
    expect(screen.getByText('可接新任务')).toBeTruthy()
  })

  it('can_accept_tasks=false 时显式标出（这是该端点唯一可执行含义）', async () => {
    api.hubGet.mockResolvedValue({ mode: 'tired', label: '疲劳模式', can_accept_tasks: false })
    render(<ModeCard />)
    expect(await screen.findByText('当前不接受新任务')).toBeTruthy()
  })
})
