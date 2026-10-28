import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
}))

import UnitStatus from './unit-status'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
})

function payload() {
  return {
    ok: true,
    health_score: 72.5,
    generated_at: '2026-10-28T21:00:00+00:00',
    summary: { ok: 1, warn: 1, off: 0, never: 1, error: 0, total: 3 },
    units: [
      { id: 'skills.usage', name: '技能使用（近 7 天）', family: 'skills', status: 'warn',
        summary: '近 7 天：命中 3 次（命中率 75%）· 加载 2 · 执行 1 成功/0 失败',
        metrics: { hit_calls: 3, zero_hit_enabled: 4 }, issues: ['4 个启用技能近 7 天 0 命中（例：a、b）'],
        last_activity: '2026-10-28T20:00:00+00:00', probe_ms: 1.2 },
      { id: 'memory.compress', name: '记忆压缩', family: 'memory', status: 'ok',
        summary: '摘要 v88 · 1024 字节', metrics: { bytes: 1024 }, issues: [], last_activity: null },
      { id: 'workflows.store', name: '工作流', family: 'workflows', status: 'never',
        summary: '0 条工作流（启用 0）', metrics: { total: 0 }, issues: [] },
    ],
  }
}

describe('UnitStatus（功能单位工作状况）', () => {
  it('渲染健康分、状态统计与每个单位的 issues', async () => {
    api.hubGet.mockResolvedValue(payload())
    render(<UnitStatus />)

    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/system/units'))
    expect(await screen.findByText(/健康分 72.5/)).toBeTruthy()
    expect(screen.getByText('技能使用（近 7 天）')).toBeTruthy()
    expect(screen.getByText(/命中 3 次/)).toBeTruthy()
    expect(screen.getByText(/4 个启用技能近 7 天 0 命中/)).toBeTruthy()
    expect(screen.getByText('记忆压缩')).toBeTruthy()
  })

  it('按家族过滤；「重新探测」带 refresh=1', async () => {
    api.hubGet.mockResolvedValue(payload())
    render(<UnitStatus />)
    await screen.findByText('记忆压缩')

    fireEvent.click(screen.getByRole('button', { name: '记忆' }))
    await waitFor(() => expect(screen.queryByText('技能使用（近 7 天）')).toBeNull())
    expect(screen.getByText('记忆压缩')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /重新探测/ }))
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/system/units?refresh=1'))
  })

  it('后端 ok=false 时把错误上屏（不静默吞掉）', async () => {
    api.hubGet.mockResolvedValue({ ok: false, error: '采集失败: boom' })
    render(<UnitStatus />)
    expect(await screen.findByText(/采集失败: boom/)).toBeTruthy()
  })
})
