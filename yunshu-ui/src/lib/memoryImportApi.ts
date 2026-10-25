/**
 * 外部记忆导入 API（记忆管理页「导入外部记忆」）
 * ---------------------------------------------------------------
 * 契约全部来自后端 agent/server_routes/routes_memory_import.py：
 *   GET  /api/memory-import/sources        探测本机来源（只读）
 *   POST /api/memory-import/preview        生成预览（只读，**不写记忆**）
 *   POST /api/memory-import/commit         按批次写入（默认 dry-run；confirm=true 才真写）
 *
 * 【为什么自己发请求而不复用 postEnvelope】错误侧要能读到 RFC 9457 的 detail：
 * 例如 409 plan-not-found / 400 confirmation-required 的文案对用户有用，而
 * postEnvelope 在非 2xx 时只抛 "HTTP 409"（它的 docstring 明确把错误侧留给另一条线）。
 * 这里只解析**已声明信封**的成功体 + 尽力读 problem 的 detail，不猜形态。
 */

import { authHeader } from './apiToken'
import {
  ENVELOPE_HEADER, ENVELOPE_VERSION, unwrapEnvelopeBody,
} from '../api/envelope'
import {
  MEMORY_IMPORT_BATCHES, MEMORY_IMPORT_COMMIT, MEMORY_IMPORT_PREVIEW,
  MEMORY_IMPORT_ROLLBACK, MEMORY_IMPORT_SOURCES, VECTOR_RECENT,
} from '../api/endpoints'

export interface MemoryImportSource {
  id: string
  label: string
  available: boolean
  detail: string
  has_transcripts: boolean
}

export interface MemoryImportBatch {
  batch_id: string
  carrier: string
  carrier_label: string
  title: string
  source: string
  project?: string
  date?: string
  chars: number
  records: number
  digest: string
  /** 摘要来源：rule（规则式原句汇总）/ llm（可选 LLM 改写，可能漏项） */
  digest_source?: 'rule' | 'llm'
  /** 摘要元数据：模型名、输入/输出字符数；降级时带 reason 原因码 */
  digest_meta?: Record<string, unknown>
  items?: Array<{ record_id: string; role: string; ts: string; chars: number; text: string }>
}

/** 本次预览的摘要方式与预算消耗（后端 service.preview 的 digest 字段） */
export interface MemoryImportDigestInfo {
  requested: boolean
  mode: 'rule' | 'llm'
  llm_available: boolean
  reason?: string
  llm_batches?: number
  rule_batches?: number
  budget?: { max_batches?: number; max_input_chars?: number; calls?: number; chars_used?: number }
}

export interface MemoryImportPlan {
  plan_id: string
  generated_at: string
  plan_path?: string
  stats: Record<string, number | Record<string, number>>
  redaction: { hits?: Record<string, number>; masked_records?: number; blocked?: number }
  batches: MemoryImportBatch[]
  digest?: MemoryImportDigestInfo
  requires_confirm: boolean
}

export interface MemoryImportCommitItem {
  batch_id: string
  carrier: string
  carrier_label?: string
  title?: string
  records: number
  chars: number
  ref?: string
  audit?: { seq?: number }
}

export interface MemoryImportCommitResult {
  dry_run: boolean
  plan_id: string
  committed?: MemoryImportCommitItem[]
  failed?: Array<{ batch_id: string; carrier: string; error: string }>
  would?: MemoryImportCommitItem[]
}

export interface MemoryImportBatchEntry {
  batch_id: string
  carrier: string
  carrier_label: string
  title: string
  records: number
  chars: number
  ref: string
  plan_id: string
  committed_at: string
  audit_seq: number
  rollback: 'auto' | 'manual'
  /** 向量记忆的 id 列表（§10 第 2 步起：回滚按它真删） */
  vector_ids?: string[]
  /** 该批次是从消息流迁到向量库的（§10 第 3 步标记） */
  migrated?: boolean
  /** 外部来源（claude / trae）与周期（2026-07 / static） */
  origin?: string
  period?: string
  /** 摘要方式：rule（规则式）/ llm（LLM 改写） */
  digest_source?: string
}

export interface MemoryImportRollbackResult {
  ok: boolean
  dry_run: boolean
  batch_id: string
  carrier?: string
  ref?: string
  deleted?: string
  would_delete?: string
  /** dry-run 时的逐条内容预览（回滚 = 物理删除，先看清再确认） */
  items_preview?: Array<{
    id: string
    chars: number
    preview: string
    metadata?: Record<string, unknown>
  }>
  /** 删除前就已不存在的向量 id（404 不算失败，也不冒充删除成功） */
  missing?: string[]
  /** 真正删除失败的向量 id（附异常类名）；非空时批次清单不会被删 */
  failed?: string[]
  vector_ids?: string[]
  manual?: boolean
  error?: string
  audit?: { seq?: number }
}

/** 业务错误：带上 HTTP 状态与 problem 的 detail，便于界面直接展示。 */
export class MemoryImportApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'MemoryImportApiError'
    this.status = status
  }
}

function headersFor(json: boolean): Record<string, string> {
  const headers: Record<string, string> = {}
  if (json) headers['Content-Type'] = 'application/json'
  Object.assign(headers, authHeader())
  return headers
}

/** 读 problem 体（RFC 9457）：detail > title > 状态码兜底。 */
async function problemMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string; title?: string }
    return body?.detail || body?.title || ('HTTP ' + res.status)
  } catch {
    return 'HTTP ' + res.status
  }
}

async function readSuccess<T>(res: Response, endpoint: string): Promise<T> {
  if (!res.ok) throw new MemoryImportApiError(await problemMessage(res), res.status)
  const version = res.headers.get(ENVELOPE_HEADER)
  if (version !== ENVELOPE_VERSION) {
    throw new MemoryImportApiError(endpoint + ' 未声明统一信封（实测 ' + ENVELOPE_HEADER + '=' + version + '）', res.status)
  }
  return unwrapEnvelopeBody<T>(await res.json(), endpoint)
}

export async function fetchMemoryImportSources(
  signal?: AbortSignal,
): Promise<{ sources: MemoryImportSource[]; digest_llm?: { available: boolean; reason?: string }; note?: string }> {
  const res = await fetch(MEMORY_IMPORT_SOURCES, { headers: headersFor(false), signal })
  return readSuccess<{ sources: MemoryImportSource[]; digest_llm?: { available: boolean; reason?: string }; note?: string }>(
    res, MEMORY_IMPORT_SOURCES,
  )
}

export async function previewMemoryImport(
  input: { sources: string[]; include_transcripts: boolean; use_llm?: boolean },
  signal?: AbortSignal,
): Promise<MemoryImportPlan> {
  const res = await fetch(MEMORY_IMPORT_PREVIEW, {
    method: 'POST',
    headers: headersFor(true),
    body: JSON.stringify(input),
    signal,
  })
  return readSuccess<MemoryImportPlan>(res, MEMORY_IMPORT_PREVIEW)
}

export async function fetchMemoryImportBatches(signal?: AbortSignal): Promise<{ batches: MemoryImportBatchEntry[] }> {
  const res = await fetch(MEMORY_IMPORT_BATCHES, { headers: headersFor(false), signal })
  return readSuccess<{ batches: MemoryImportBatchEntry[] }>(res, MEMORY_IMPORT_BATCHES)
}

export async function rollbackMemoryImport(input: {
  batch_id: string
  confirm: boolean
  dry_run?: boolean
}): Promise<MemoryImportRollbackResult> {
  const res = await fetch(MEMORY_IMPORT_ROLLBACK, {
    method: 'POST',
    headers: headersFor(true),
    body: JSON.stringify({ dry_run: true, ...input }),
  })
  return readSuccess<MemoryImportRollbackResult>(res, MEMORY_IMPORT_ROLLBACK)
}

export async function commitMemoryImport(input: {
  plan_id: string
  batch_ids: string[]
  confirm: boolean
  dry_run?: boolean
}): Promise<MemoryImportCommitResult> {
  const res = await fetch(MEMORY_IMPORT_COMMIT, {
    method: 'POST',
    headers: headersFor(true),
    body: JSON.stringify({ dry_run: true, ...input }),
  })
  return readSuccess<MemoryImportCommitResult>(res, MEMORY_IMPORT_COMMIT)
}

/** 一条外部导入的向量文档（GET /api/vector/recent 里 metadata.source=memory_import 的那些）。 */
export interface ImportedVectorDoc {
  id: string
  char_count: number
  /** 外部来源：claude / trae（取自写入口径的 origin） */
  origin: string
  batch_id: string
  period: string
  digest_source: string
  title: string
  /** 迁移标记（§10 第 3 步：从消息流搬到向量库） */
  migrated_from?: string
  created_at?: string
}

/**
 * 读「外部导入的向量文档」。
 *
 * 【为什么不用 readSuccess】/api/vector/recent 是 legacy 端点（{`items`} 裸体、
 * 不带 X-Envelope）；它不在本次改动范围内，故这里单独解析，并**只挑出**
 * metadata.source === 'memory_import' 的那些 —— 界面要是把所有本机向量都列出来，
 * 就分不清哪条是外部记忆了。
 */
export async function fetchImportedVectorDocs(limit = 100, signal?: AbortSignal): Promise<ImportedVectorDoc[]> {
  // 服务端按 metadata.source 过滤（单次最多 100 条），避免导入条目被本机向量挤出窗口；
  // 前端仍保留一次过滤：老版本后端不认 source 参数时行为不退化。
  const res = await fetch(VECTOR_RECENT + '?source=memory_import&limit=' + limit,
    { headers: headersFor(false), signal })
  if (!res.ok) throw new MemoryImportApiError(await problemMessage(res), res.status)
  const body = (await res.json()) as { items?: Array<Record<string, unknown>> }
  const items = body?.items ?? []
  return items
    .filter((it) => {
      const meta = (it?.metadata ?? {}) as Record<string, unknown>
      return String(meta.source ?? '') === 'memory_import'
    })
    .map((it) => {
      const meta = (it?.metadata ?? {}) as Record<string, unknown>
      return {
        id: String(it.id ?? ''),
        char_count: String(it.content ?? '').length,
        origin: String(meta.origin ?? ''),
        batch_id: String(meta.batch_id ?? ''),
        period: String(meta.period ?? ''),
        digest_source: String(meta.digest_source ?? 'rule'),
        title: String(meta.title ?? ''),
        migrated_from: meta.migrated_from ? String(meta.migrated_from) : undefined,
        created_at: it.timestamp ? String(it.timestamp) : undefined,
      }
    })
}

