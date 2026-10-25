/** 导入外部记忆 —— 预览只读；勾选批次 + 勾确认后才能真写（判据仍在后端） */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const api = vi.hoisted(() => ({
  fetchSources: vi.fn(),
  preview: vi.fn(),
  commit: vi.fn(),
  fetchBatches: vi.fn(),
  rollback: vi.fn(),
  fetchVectors: vi.fn(),
}))

vi.mock('@/lib/memoryImportApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchMemoryImportSources: api.fetchSources,
  previewMemoryImport: api.preview,
  commitMemoryImport: api.commit,
  fetchMemoryImportBatches: api.fetchBatches,
  rollbackMemoryImport: api.rollback,
  fetchImportedVectorDocs: api.fetchVectors,
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
      batch_id: 'mb_mem', carrier: 'memory_note', carrier_label: '向量记忆',
      title: 'trae 提问轨迹 2026-06', source: 'trae', chars: 800, records: 20,
      digest: '# 提问\n- 09:00 帮我修脚本',
    },
  ],
  requires_confirm: true,
}

beforeEach(() => {
  api.fetchBatches.mockResolvedValue({ batches: [] })
  api.fetchVectors.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  api.fetchSources.mockReset()
  api.preview.mockReset()
  api.commit.mockReset()
  api.fetchBatches.mockReset()
  api.rollback.mockReset()
  api.fetchVectors.mockReset()
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
    await waitFor(() => expect(api.preview).toHaveBeenCalledWith({
      sources: ['claude', 'trae'], include_transcripts: true, use_llm: false,
    }))
    expect(await screen.findByText('mb_card')).toBeTruthy()
    expect(screen.getByText(/已选 0 \/ 2/)).toBeTruthy()
    // 未勾选批次 + 未确认 ⇒ 真写按钮禁用
    expect((screen.getByRole('button', { name: '写入云枢' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('未配置 LLM 时开关不可勾选，且不把 use_llm 传成 true', async () => {
    api.fetchSources.mockResolvedValue({
      sources: [{ id: 'trae', label: 'Trae', available: true, detail: 'x', has_transcripts: false }],
      digest_llm: { available: false, reason: 'llm_unconfigured' },
    })
    api.preview.mockResolvedValue(PLAN)
    render(<MemoryImportPanel />)

    const toggle = (await screen.findByLabelText('用 LLM 摘要')) as HTMLInputElement
    expect(toggle.disabled).toBe(true)
    expect(await screen.findByText(/LLM 摘要不可用/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '生成预览' }))
    await waitFor(() => expect(api.preview).toHaveBeenCalledWith({
      sources: ['trae'], include_transcripts: true, use_llm: false,
    }))
  })

  it('配置了 LLM 时勾选开关会传 use_llm=true，并按批次标出来源', async () => {
    api.fetchSources.mockResolvedValue({
      sources: [{ id: 'trae', label: 'Trae', available: true, detail: 'x', has_transcripts: false }],
      digest_llm: { available: true, reason: '' },
    })
    api.preview.mockResolvedValue({
      ...PLAN,
      batches: [
        { ...PLAN.batches[0], digest_source: 'rule' },
        { ...PLAN.batches[1], digest_source: 'llm', digest_meta: { model: 'deepseek-v4-flash' } },
      ],
      digest: {
        requested: true, mode: 'llm', llm_available: true,
        llm_batches: 1, rule_batches: 1,
        budget: { max_batches: 3, max_input_chars: 12000, calls: 1, chars_used: 800 },
      },
    })
    render(<MemoryImportPanel />)

    const toggle = (await screen.findByLabelText('用 LLM 摘要')) as HTMLInputElement
    expect(toggle.disabled).toBe(false)
    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: '生成预览' }))

    await waitFor(() => expect(api.preview).toHaveBeenCalledWith({
      sources: ['trae'], include_transcripts: true, use_llm: true,
    }))
    expect(await screen.findByText(/LLM 摘要 1 批 \/ 规则 1 批/)).toBeTruthy()
    // 徽章：LLM 那一批与规则那一批各一个
    expect(screen.getAllByText('LLM').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('规则').length).toBeGreaterThanOrEqual(1)
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

  it('已写入批次可回滚：先二次确认，再真删；没有 vector_ids/msg_id 的批次标"需人工"', async () => {
    api.fetchSources.mockResolvedValue({ sources: [{ id: 'trae', label: 'Trae', available: true, detail: 'x', has_transcripts: false }] })
    api.fetchBatches.mockResolvedValue({
      batches: [
        {
          batch_id: 'mb_card', carrier: 'knowledge_card', carrier_label: '知识卡片',
          title: 'claude 偏好', records: 4, chars: 300, ref: 'slug-1',
          plan_id: 'mp_test', committed_at: '2026-10-24T10:40:35+08:00',
          audit_seq: 79470, rollback: 'auto',
        },
        {
          batch_id: 'mb_mem', carrier: 'memory_note', carrier_label: '向量记忆',
          vector_ids: [], origin: 'trae', period: '2026-06',
          title: 'trae 提问', records: 46, chars: 2005, ref: 'memory/manual',
          plan_id: 'mp_test', committed_at: '2026-10-24T10:44:24+08:00',
          audit_seq: 79474, rollback: 'manual',
        },
      ],
    })
    api.rollback.mockImplementation(({ dry_run }: { dry_run?: boolean }) => Promise.resolve(
      dry_run
        ? { ok: true, dry_run: true, batch_id: 'mb_card', would_delete: 'slug-1' }
        : { ok: true, dry_run: false, batch_id: 'mb_card', deleted: 'slug-1', audit: { seq: 901 } },
    ))

    render(<MemoryImportPanel />)

    // 长期记忆那行不给回滚按钮，而是"需人工"
    expect(await screen.findByText('需人工')).toBeTruthy()
    const rollbackBtn = screen.getByRole('button', { name: '回滚 mb_card' }) as HTMLButtonElement
    expect(rollbackBtn.disabled).toBe(false)

    // 第一下 = 拉 dry-run 预览（只读，不删）：必须能看到"将删除什么"
    fireEvent.click(rollbackBtn)
    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith({
      batch_id: 'mb_card', confirm: false, dry_run: true,
    }))
    expect(await screen.findByText(/即将物理删除批次 mb_card/)).toBeTruthy()
    // slug-1 会同时出现在表格 ref 列与预览条里
    expect(screen.getAllByText(/slug-1/).length).toBeGreaterThanOrEqual(1)
    fireEvent.click(screen.getByRole('button', { name: '确认回滚 mb_card' }))

    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith({
      batch_id: 'mb_card', confirm: true, dry_run: false,
    }))
    expect(await screen.findByText(/已回滚 mb_card/)).toBeTruthy()
    // 回滚后会刷新批次清单（写入前的空列表 + 回滚后的刷新 = 至少 2 次）
    await waitFor(() => expect(api.fetchBatches.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('向量记忆批次回滚：按 vector_ids 真删，missing 如实提示已不存在', async () => {
    api.fetchSources.mockResolvedValue({ sources: [] })
    api.fetchBatches.mockResolvedValue({
      batches: [{
        batch_id: 'mb_vec', carrier: 'memory_note', carrier_label: '向量记忆',
        title: 'claude 会话摘要 2026-07', records: 12, chars: 900, ref: 'vector/mem_1',
        plan_id: 'mp_test', committed_at: '2026-10-25T10:00:00+08:00', audit_seq: 9,
        rollback: 'auto', vector_ids: ['mem_1', 'mem_2'], origin: 'claude', period: '2026-07',
      }],
    })
    api.rollback.mockResolvedValue({
      ok: true, dry_run: false, batch_id: 'mb_vec', deleted: 'vector:mem_1',
      missing: ['mem_2'], audit: { seq: 902 },
    })
    render(<MemoryImportPanel />)
    fireEvent.click(await screen.findByRole('button', { name: '回滚 mb_vec' }))
    // 预览条是异步出现的（先 dry-run 再渲染确认按钮）
    fireEvent.click(await screen.findByRole('button', { name: '确认回滚 mb_vec' }))
    // 404 不算失败，也不能冒充“刚删掉”：单独说明有一条在删除前已不存在
    expect(await screen.findByText(/1 条在删除前已不存在/)).toBeTruthy()
    expect(screen.getByText(/审计 seq 902/)).toBeTruthy()
  })

  it('向量文档列表带来源徽章，且能按批次回滚', async () => {
    api.fetchSources.mockResolvedValue({ sources: [] })
    api.fetchVectors.mockResolvedValue([
      {
        id: 'mem_1', char_count: 900, origin: 'claude', batch_id: 'mb_vec',
        period: '2026-07', digest_source: 'rule', title: 'claude 会话摘要',
        migrated_from: 'messages.jsonl',
      },
      {
        id: 'mem_2', char_count: 120, origin: 'trae', batch_id: 'mb_vec2',
        period: '2026-05', digest_source: 'llm', title: 'trae 提问轨迹',
      },
    ])
    api.rollback.mockImplementation(({ dry_run }: { dry_run?: boolean }) => Promise.resolve(
      dry_run
        ? {
          ok: true, dry_run: true, batch_id: 'mb_vec', would_delete: 'vector:mem_1',
          items_preview: [{
            id: 'mem_1', chars: 12, preview: '这是即将被删掉的正文',
            metadata: { origin: 'claude', batch_id: 'mb_vec' },
          }],
        }
        : { ok: true, dry_run: false, batch_id: 'mb_vec', deleted: 'vector:mem_1' },
    ))
    render(<MemoryImportPanel />)
    // 来源徽章：claude / trae 各一条，迁移来的额外带「迁移」
    expect(await screen.findByText('claude')).toBeTruthy()
    expect(screen.getByText('trae')).toBeTruthy()
    expect(screen.getByText('迁移')).toBeTruthy()
    expect(screen.getByText('mem_1')).toBeTruthy()
    // 【2026-10-25】这里是"物理删除"，必须两步：第一下只进入确认态，不发请求
    const first = screen.getByRole('button', { name: '回滚向量批次 mb_vec' })
    expect(first.textContent).toContain('先看内容')
    fireEvent.click(first)
    // 第一下只拉预览：既不发 confirm，也必须把正文摆出来
    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith({
      batch_id: 'mb_vec', confirm: false, dry_run: true,
    }))
    expect(await screen.findByText('这是即将被删掉的正文')).toBeTruthy()
    expect(screen.getByText(/将删除/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '确认回滚 mb_vec' }))
    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith({
      batch_id: 'mb_vec', confirm: true, dry_run: false,
    }))
    // 回滚后要刷新向量列表（否则界面上还留着已删的那条）
    await waitFor(() => expect(api.fetchVectors.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

})
