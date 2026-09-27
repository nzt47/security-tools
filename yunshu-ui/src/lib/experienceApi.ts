/**
 * 经验库 API —— /api/experience/*（方案 P3 前端）
 *
 * 复用 lib/apiClient.request（fetch + 自动附带 Authorization: Bearer，见 lib/apiToken.ts），
 * 不新建 HTTP 层。后端契约：{ ok: true, ... } / { ok: false, error }
 */
import { request } from './apiClient'

export interface ExperienceStack {
  lang?: string
  frameworks?: string[]
  files_changed?: number
}

export interface ExperienceDiff {
  path?: string
  op?: string
  diff?: string
  bytes?: number
  truncated?: boolean
}

export interface ExperiencePitfall {
  symptom?: string
  cause?: string
  fix?: string
  verified_by?: string
}

export interface ExperienceItem {
  id: string
  task?: string
  task_type?: string
  stack?: ExperienceStack
  diffs?: ExperienceDiff[]
  pitfalls?: ExperiencePitfall[]
  verified?: string
  created_at?: string
  review_status?: string
  decision?: string
}

export interface ExperienceStats {
  corpus_size: number
  by_verified: Record<string, number>
  by_task_type: Record<string, number>
  by_lang: Record<string, number>
  pitfalls_total: number
  review: Record<string, number>
  batches: number
}

export interface ExperienceHit {
  id: string
  score: number
  legs: string[]
  task: string
  task_type?: string
  lang?: string
  verified?: string
}

export interface IngestResult {
  ok: boolean
  batch_id?: string | null
  accepted?: string[]
  rejected?: { id?: string; reason?: string; pattern?: string }[]
  error?: string
}

export function fetchStats() {
  return request<{ ok: boolean; stats: ExperienceStats }>('/api/experience/stats')
}

export function fetchList(params: {
  lang?: string
  status?: string
  task_type?: string
  limit?: number
  offset?: number
} = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v) !== '') qs.set(k, String(v))
  })
  const q = qs.toString()
  return request<{ ok: boolean; total: number; items: ExperienceItem[] }>(
    `/api/experience/list${q ? '?' + q : ''}`,
  )
}

export function fetchDetail(id: string) {
  return request<{ ok: boolean; item: ExperienceItem }>(`/api/experience/${encodeURIComponent(id)}`)
}

/** 预检预览：纯本地 BM25，不走 DeepSeek */
export function searchPreview(q: string, topK = 5, lang?: string) {
  const qs = new URLSearchParams({ q, top_k: String(topK) })
  if (lang) qs.set('lang', lang)
  return request<{ ok: boolean; hits: ExperienceHit[]; error?: string }>(
    `/api/experience/search?${qs.toString()}`,
  )
}

/** 审阅：L2 逐次确认 —— confirmed 必须显式为 true（后端强制） */
export function reviewItem(id: string, action: 'accept' | 'reject' | 'deprecate', reason = '') {
  return request<{ ok: boolean; error?: string }>(
    `/api/experience/${encodeURIComponent(id)}/review`,
    { method: 'POST', body: { action, reason, confirmed: true } },
  )
}

/** 按 batch_id 回滚（后端写审计链） */
export function rollbackBatch(batchId: string, reason = '') {
  return request<{ ok: boolean; removed?: number; remaining?: number; error?: string }>(
    `/api/experience/batch/${encodeURIComponent(batchId)}/rollback`,
    { method: 'POST', body: { confirmed: true, reason } },
  )
}

export function ingestSamples(samples: ExperienceItem[]) {
  return request<IngestResult>('/api/experience/ingest', {
    method: 'POST',
    body: { samples },
  })
}
