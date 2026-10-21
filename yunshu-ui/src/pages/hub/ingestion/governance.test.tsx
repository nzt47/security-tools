import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  scanGovernance: vi.fn(),
  planGovernance: vi.fn(),
  applyGovernance: vi.fn(),
}))
vi.mock('./governanceApi', () => api)

import GovernancePanel from './governance'

const scan = {
  ok: true, generated_at: 't', total: 4, items: [], duplicates: [], orphans: [{ id: 'orphan' }], unused: [],
  summary: { duplicate: 2, archive: 1, split: 1, refactor: 1, orphan: 1 },
}

const plan = {
  ok: true, plan_id: 'gp_1', generated_at: 't',
  actions: [
    { action: 'merge', target: 'beta', title: '合并 beta → alpha', reason: '重复',
      evidence: { jaccard: 1 }, payload: {}, destructive: false },
    { action: 'delete_orphans', target: 'orphans', title: '清理孤儿（1 条）', reason: '孤儿',
      evidence: {}, payload: {}, destructive: true },
  ],
  summary: { actions: 2, by_action: { merge: 1, delete_orphans: 1 } },
  scan_summary: {}, requires_confirm: true,
}

afterEach(() => {
  cleanup()
  api.scanGovernance.mockReset()
  api.planGovernance.mockReset()
  api.applyGovernance.mockReset()
  vi.unstubAllGlobals()
})

function setup() {
  api.scanGovernance.mockResolvedValue(scan)
  api.planGovernance.mockResolvedValue(plan)
  render(<GovernancePanel />)
}

async function loadPlan() {
  setup()
  fireEvent.click(screen.getByRole('button', { name: '盘点并生成计划' }))
  await screen.findByText('合并 beta → alpha')
}

describe('GovernancePanel', () => {
  it('盘点后展示动作与证据，未勾确认不能执行', async () => {
    await loadPlan()
    expect(api.scanGovernance).toHaveBeenCalled()
    expect(api.planGovernance).toHaveBeenCalled()
    expect(screen.getByText('合并 beta → alpha')).toBeTruthy()
    expect(screen.getByText('清理孤儿（1 条）')).toBeTruthy()
    expect((screen.getByRole('button', { name: '执行所选' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('dry-run 只预览，不改数据', async () => {
    await loadPlan()
    api.applyGovernance.mockResolvedValue({
      ok: true, dry_run: true, applied: false, plan_id: 'gp_1',
      action: 'merge', target: 'beta', would: { src_id: 'beta', dst_id: 'alpha' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'dry-run 合并 beta → alpha' }))
    await waitFor(() => expect(api.applyGovernance).toHaveBeenCalledWith({
      plan_id: 'gp_1', action: 'merge', target: 'beta', confirm: false, dry_run: true,
    }))
    expect(await screen.findByText('dry-run 影响（未执行）')).toBeTruthy()
  })

  it('勾确认后执行 merge，带 confirm=true/dry_run=false', async () => {
    await loadPlan()
    api.applyGovernance.mockResolvedValue({
      ok: true, dry_run: false, applied: true, plan_id: 'gp_1',
      action: 'merge', target: 'beta', result: { merged_id: 'alpha' },
      audit: { seq: 9, self_hash: 'h' },
    })
    fireEvent.click(screen.getByLabelText('选择 合并 beta → alpha'))
    fireEvent.click(screen.getByLabelText('我已核对治理计划'))
    fireEvent.click(screen.getByRole('button', { name: '执行所选' }))
    await waitFor(() => expect(api.applyGovernance).toHaveBeenCalledWith({
      plan_id: 'gp_1', action: 'merge', target: 'beta', confirm: true, dry_run: false,
    }))
    expect(await screen.findByText('audit seq 9')).toBeTruthy()
  })

  it('破坏性动作要二次 confirm', async () => {
    await loadPlan()
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    fireEvent.click(screen.getByLabelText('选择 清理孤儿（1 条）'))
    fireEvent.click(screen.getByLabelText('我已核对治理计划'))
    fireEvent.click(screen.getByRole('button', { name: '执行所选' }))
    expect(confirmSpy).toHaveBeenCalled()
    expect(api.applyGovernance).not.toHaveBeenCalled()
  })
})
