/** 导入外部记忆 —— 预览只读；勾选批次 + 勾确认后才能真写（判据仍在后端） */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  fetchSources: vi.fn(),
  preview: vi.fn(),
  commit: vi.fn(),
}))

vi.mock('@/lib/memoryImportApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchMemoryImportSources: api.fetchSources,
  previewMemoryImport: api.preview,
  commitMemoryImport: api.commit,
}))

import MemoryImportPanel from './MemoryImportPanel'

const PLAN = {
  plan_id: 'mp_test',
  generated_at: '2026-10-24T00:00:00+08:00',
  stats: { scanned: 10, denoised: 1, duplicates: 2, kept: 7 },
  redaction: { hits: { openai_sk: 1 }, masked_records: 1, blocked: 0 },
  batches: [
    {
      batch_id: 'mb_card', carrier: 'knowledge_card', carrier_label: '知识卡片',
      title: 'claude 偏好与规则', source: 'claude', chars: 300, records: 4,
      digest: '# 规则\n一律中文',
    },
    {
      batch_id: 'mb_mem', carrier: 'memory_note', carrier_label: '长期记忆',
      title: 'trae 提问轨迹 2026-06', source: 'trae', chars: 800, records: 20,
      digest: '# 提问\n- 09:00 帮我修脚本',
    },
  ],
  requires_confirm: true,
}

afterEach(() => {
  cleanup()
  api.fetchSources.mockReset()
  api.preview.mockReset()
  api.commit.mockReset()
})

describe('MemoryImportPanel', () => {
  it('加载来源后生成预览，默认不勾选任何批次', async () => {
    api.fetchSources.mockResolvedValue({
      sources: [
        { id: 'claude', label: 'Claude Code', available: true, detail: 'C:/u/.claude', has_transcripts: true },
        { id: 'trae', label: 'Trae', available: true, detail: 'C:/u/Trae CN', has_transcripts: false },
      ],
      note: 'Trae 的会话正文在云端',
    })
    api.preview.mockResolvedValue(PLAN)
    render(<MemoryImportPanel />)

    fireEvent.click(await screen.findByRole('button', { name: '生成预览' }))
    await waitFor(() => expect(api.preview).toHaveBeenCalledWith({ sources: ['claude', 'trae'], include_transcripts: true }))
    expect(await screen.findByText('mb_card')).toBeTruthy()
    expect(screen.getByText(/已选 0 \/ 2/)).toBeTruthy()
    // 未勾选批次 + 未确认 ⇒ 真写按钮禁用
    expect((screen.getByRole('button', { name: '写入云枢' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('勾选批次与确认后才真写（confirm=true, dry_run=false）', async () => {
    api.fetchSources.mockResolvedValue({ sources: [{ id: 'trae', label: 'Trae', available: true, detail: 'x', has_transcripts: false }] })
    api.preview.mockResolvedValue(PLAN)
    api.commit.mockResolvedValue({
      dry_run: false,
      plan_id: 'mp_test',
      committed: [{ batch_id: 'mb_mem', carrier: 'memory_note', records: 20, chars: 800, ref: 'memory/manual', audit: { seq: 900 } }],
    })
    render(<MemoryImportPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '生成预览' }))
    await screen.findByText('mb_mem')

    fireEvent.click(screen.getByLabelText('选择批次 mb_mem'))
    const writeBtn = screen.getByRole('button', { name: '写入云枢' }) as HTMLButtonElement
    expect(writeBtn.disabled).toBe(true)  // 还没勾确认

    fireEvent.click(screen.getByLabelText('我确认已核对预览内容'))
    await waitFor(() => expect(writeBtn.disabled).toBe(false))
    fireEvent.click(writeBtn)

    await waitFor(() => expect(api.commit).toHaveBeenCalledWith({
      plan_id: 'mp_test', batch_ids: ['mb_mem'], confirm: true, dry_run: false,
    }))
    expect(await screen.findByText(/写入完成：成功 1 个批次/)).toBeTruthy()
    // 摘要行与批次明细都会带上 seq（两处都算通过）
    expect(screen.getAllByText(/审计 seq 900/).length).toBeGreaterThanOrEqual(1)
  })

  it('只回放走 dry-run，不要求勾确认', async () => {
    api.fetchSources.mockResolvedValue({ sources: [{ id: 'trae', label: 'Trae', available: true, detail: 'x', has_transcripts: false }] })
    api.preview.mockResolvedValue(PLAN)
    api.commit.mockResolvedValue({
      dry_run: true, plan_id: 'mp_test',
      would: [{ batch_id: 'mb_card', carrier: 'knowledge_card', records: 4, chars: 300 }],
    })
    render(<MemoryImportPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '生成预览' }))
    await screen.findByText('mb_card')

    fireEvent.click(screen.getByLabelText('选择批次 mb_card'))
    fireEvent.click(screen.getByRole('button', { name: '只回放' }))
    await waitFor(() => expect(api.commit).toHaveBeenCalledWith({
      plan_id: 'mp_test', batch_ids: ['mb_card'], confirm: false, dry_run: true,
    }))
    expect(await screen.findByText(/回放完成：将写入 1 个批次/)).toBeTruthy()
  })
})
