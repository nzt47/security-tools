/** 外部会话导入卡片：dry-run 只统计；执行按钮必须勾确认才可点（真写判据仍在后端） */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  fetchSources: vi.fn(),
  runImport: vi.fn(),
  fetchLatest: vi.fn(),
}))

vi.mock('@/lib/memoryImportApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchMemorySessionSources: api.fetchSources,
  importMemorySessions: api.runImport,
  fetchMemorySessionLatest: api.fetchLatest,
}))

import MemorySessionsImportCard from './MemorySessionsImportCard'

const REPORT = {
  source: 'dsh',
  dry_run: true,
  duration_ms: 1234,
  sessions: { seen: 418, scanned: 3, skipped_unchanged: 0, imported: 3, empty: 0, failed: 0 },
  messages: { imported: 12, skipped_existing: 0, skipped_redacted: 0, truncated: 0 },
  chars: { imported: 2048 },
  redactions: {},
  report_path: 'data/memory/import_sessions/reports/dsh-x-dry.json',
}

beforeEach(() => {
  api.fetchSources.mockResolvedValue({
    sources: [
      { id: 'dsh', label: 'DeepSeek Harness', available: true, detail: 'C:/u/.dsh', sessions: 418 },
      { id: 'claude', label: 'Claude Code', available: true, detail: 'C:/u/.claude', sessions: 72 },
      { id: 'trae', label: 'Trae', available: false, detail: '', sessions: 0 },
    ],
  })
  api.fetchLatest.mockResolvedValue({ source: 'dsh', report: null })
})

afterEach(() => {
  cleanup()
  api.fetchSources.mockReset()
  api.runImport.mockReset()
  api.fetchLatest.mockReset()
})

describe('MemorySessionsImportCard', () => {
  it('dry-run 只统计、并展示计数与报告路径', async () => {
    api.runImport.mockResolvedValue(REPORT)
    render(<MemorySessionsImportCard />)
    fireEvent.click(await screen.findByRole('button', { name: '只跑 dry-run' }))
    await waitFor(() => expect(api.runImport).toHaveBeenCalledWith({
      source: 'dsh', dry_run: true, confirm: false,
      limit_sessions: undefined, include_subagents: true,
    }))
    expect(await screen.findByText(/dry-run 完成/)).toBeTruthy()
    expect(screen.getByText(/dsh-x-dry.json/)).toBeTruthy()
  })

  it('执行导入必须勾确认；勾上后 confirm=true', async () => {
    api.runImport.mockResolvedValue({ ...REPORT, dry_run: false })
    render(<MemorySessionsImportCard />)
    const exec = await screen.findByRole('button', { name: '执行导入' }) as HTMLButtonElement
    expect(exec.disabled).toBe(true)
    fireEvent.click(screen.getByLabelText('确认只追加不删除'))
    await waitFor(() => expect(exec.disabled).toBe(false))
    fireEvent.click(exec)
    await waitFor(() => expect(api.runImport).toHaveBeenCalledWith({
      source: 'dsh', dry_run: false, confirm: true,
      limit_sessions: undefined, include_subagents: true,
    }))
  })

  it('小样本：填 3 时把 limit_sessions 传给后端', async () => {
    api.runImport.mockResolvedValue(REPORT)
    render(<MemorySessionsImportCard />)
    fireEvent.change(await screen.findByLabelText('最多会话数'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: '只跑 dry-run' }))
    await waitFor(() => expect(api.runImport).toHaveBeenCalledWith({
      source: 'dsh', dry_run: true, confirm: false,
      limit_sessions: 3, include_subagents: true,
    }))
  })
})
