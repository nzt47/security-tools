/**
 * 治理与运营 · 规范守卫页（2026-11-19）
 * 锁：检查报告上屏、量具回归提示、明细过滤、自动修复先 dry_run 再 confirm。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({ hubGet: vi.fn(), hubPost: vi.fn() }))
vi.mock('../components/ui', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  hubGet: api.hubGet,
  hubPost: api.hubPost,
}))

import UiGuardPage from './ui-guard'

afterEach(() => {
  cleanup()
  api.hubGet.mockReset()
  api.hubPost.mockReset()
})

const REPORT = {
  schema_version: 1, mode: 'fast', generated_at: '2026-11-19T00:00:00Z', commit: 'abc1234',
  counts: { total: 3, error: 2, warning: 1, info: 0 },
  by_rule: { 'button.alignment': 1, 'heading.scale': 1, 'audit.css_hardcoded_color': 1 },
  scanned_files: 461, duration_ms: 120.5,
  baseline: { path: 'data/ui_primitive_baseline.json', current: { raw_button: 8, arbitrary_text: 0 }, regressions: [], passed: true },
  errors: [],
  findings: [
    { rule: 'button.alignment', severity: 'error', file: 'pages/hub/x.tsx', line: 42, message: '满宽 Button 未显式声明对齐', fix_hint: '加 justify-start', fixable: true },
    { rule: 'heading.scale', severity: 'error', file: 'pages/hub/y.tsx', line: 7, message: '标题字号没有写在标签上', fix_hint: '加 text-ui', fixable: true },
    { rule: 'audit.css_hardcoded_color', severity: 'warning', file: 'components/Z.css', line: 3, message: 'CSS 硬编码颜色', fix_hint: '用 token', fixable: false },
  ],
  findings_total: 3,
}

function gets() {
  api.hubGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/api/ui-guard/check')) return { ok: true, report: REPORT }
    if (url.startsWith('/api/ui-guard/rules')) return { ok: true, rules: [
      { id: 'button.alignment', label: 'Button 对齐', fixable: true, why: '' },
      { id: 'heading.scale', label: '标题字号 token', fixable: true, why: '' },
    ] }
    if (url.startsWith('/api/ui-guard/history')) return { ok: true, limit: 12, items: [
      { ts: '2026-11-20T01:00:00Z', source: 'api', mode: 'fast', error: 0, warning: 380, total: 380, regressions: 0, scanned_files: 455, duration_ms: 900 },
      { ts: '2026-11-20T00:30:00Z', source: 'tool', mode: 'fast', error: 0, warning: 378, total: 378, regressions: 0, scanned_files: 455, duration_ms: 880 },
    ] }
    return { ok: false }
  })
}

describe('规范守卫页', () => {
  it('检查结果上屏：错误/扫描文件/基线通过', async () => {
    gets()
    render(<UiGuardPage />)
    await waitFor(() => expect(api.hubGet).toHaveBeenCalledWith('/api/ui-guard/check?limit=500&record=1'))
    expect(await screen.findByRole('heading', { level: 1, name: '规范守卫' })).toBeTruthy()
    expect(await screen.findByText('扫描文件')).toBeTruthy()
    expect(screen.getAllByText('2').length).toBeGreaterThan(0)
  })

  it('违规明细：file:line 与修复建议上屏，可按规则过滤', async () => {
    gets()
    render(<UiGuardPage />)
    await screen.findByRole('heading', { level: 1, name: '规范守卫' })
    fireEvent.click(screen.getByRole('tab', { name: /违规明细/ }))
    expect(await screen.findByText('pages/hub/x.tsx:42')).toBeTruthy()
    expect(screen.getByText('加 justify-start')).toBeTruthy()
  })

  it('自动修复：先 dry_run，预览后可确认落盘（confirm=true）', async () => {
    gets()
    api.hubPost.mockResolvedValue({ ok: true, result: {
      applied: false, dry_run: true, change_count: 1, files: ['pages/hub/x.tsx'], errors: [],
      changes: [{ rule: 'button.alignment', file: 'pages/hub/x.tsx', line: 42, before: '<Button className="w-full">', after: '<Button className="w-full justify-start">' }],
    } })
    render(<UiGuardPage />)
    await screen.findByRole('heading', { level: 1, name: '规范守卫' })
    fireEvent.click(screen.getByRole('tab', { name: /违规明细/ }))
    fireEvent.click(await screen.findByRole('button', { name: '自动修复预览' }))
    await waitFor(() => expect(api.hubPost).toHaveBeenCalledWith('/api/ui-guard/fix', expect.objectContaining({ dry_run: true })))
    expect(api.hubPost.mock.calls[0][1]).not.toHaveProperty('confirm', true)
    expect(await screen.findByText('<Button className="w-full justify-start">')).toBeTruthy()
    const confirm = await screen.findByRole('button', { name: '确认落盘' }) as HTMLButtonElement
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(api.hubPost).toHaveBeenLastCalledWith('/api/ui-guard/fix', expect.objectContaining({ dry_run: false, confirm: true })))
  })

  it('趋势行：近 N 次检查上屏，并给出与上次的差值', async () => {
    gets()
    render(<UiGuardPage />)
    expect(await screen.findByText(/检查趋势（近 2 次）/)).toBeTruthy()
    expect(await screen.findByRole('img', { name: /检查趋势：近 2 次/ })).toBeTruthy()
    expect(await screen.findByText(/较上次 错误 0 · 警告 \+2/)).toBeTruthy()
    expect(api.hubGet).toHaveBeenCalledWith('/api/ui-guard/history?limit=12')
  })

  it('量具回归非空时，概览给出红条', async () => {
    api.hubGet.mockResolvedValue({ ok: true, report: {
      ...REPORT,
      baseline: { ...REPORT.baseline, passed: false, regressions: [{ metric: 'raw_button', baseline: 8, current: 9, delta: 1 }] },
    } })
    render(<UiGuardPage />)
    expect(await screen.findByText(/量具升高/)).toBeTruthy()
  })
})
