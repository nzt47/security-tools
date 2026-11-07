/**
 * 吞噬（ingestion）前端 API 契约。
 *
 * 后端是统一信封（X-Envelope: v2）⇒ 这里只走 getEnvelope/postEnvelope，
 * 不做 pickObj 式猜测；计划/结果字段与 agent/ingestion/models.py 一一对应。
 */
import { getEnvelope, postEnvelope } from '@/api/envelope'
import { INGESTION_COMMIT, INGESTION_DIRECT, INGESTION_PREVIEW, INGESTION_SOURCES } from '@/api/endpoints'

export type IngestForm = 'skill' | 'tool' | 'workflow'

export interface IngestSourceInput {
  path?: string
  text?: string
  label?: string
  ecosystem?: string
}

export interface DedupeItem {
  kind: 'existing' | 'native' | string
  id: string
  name: string
  similarity: number
  action: string
  strong: boolean
  matched: string[]
  match: string
  reason: string
}

export interface MergeSuggestion {
  target_id: string
  target_name: string
  jaccard: number
  action: string
  reason: string
}

export interface NormalizedSkill {
  id: string
  name: string
  description: string
  version: string
  author: string
  category: string
  source: string
  tags: string[]
  content_type: string
  scripts: Record<string, string>
  body: string
}

export interface IngestPlan {
  plan_id: string
  source: Record<string, unknown>
  source_ref: string
  adapter: string
  adapter_scores: Record<string, number>
  conformance: { level: string; reasons: string[] }
  recommendation: { form: IngestForm | string; reason: string; confidence: number }
  skill: NormalizedSkill | null
  actions: string[]
  warnings: string[]
  requires_rewrite: boolean
  requires_confirm: boolean
  rewritten: boolean
  archive_id: string
  rewrite_errors: string[]
  dedupe: DedupeItem[]
  merge_suggestion: MergeSuggestion | null
  raw: Record<string, unknown>
}

export interface IngestResult {
  plan_id: string
  skill_id: string
  name: string
  version: string
  scripts_count: number
  adapter: string
  conformance: string
  warnings: string[]
  installed: boolean
  rewritten: boolean
  archive_id: string
  merged: boolean
  merged_into: string
  removed_id: string
  queued: boolean
  form: IngestForm | string
  draft: boolean
  draft_dir: string
  stage: string
  stage_verdict: string
}

export interface SourceCandidate {
  root: string
  ecosystem: string
  name: string
  path: string
  kind: string
  formats: string[]
  previewable: boolean
  symlink: boolean
  warnings: string[]
  /** 用过没有：空 = 没用过；draft = 生成过草稿；registered = 已入轨技能（同名）*/
  usage?: '' | 'draft' | 'registered'
  usage_draft_id?: string
  usage_at?: string
}

export interface SourceRoot {
  ecosystem: string
  path: string
  source: string
  exists: boolean
  error?: string
  candidates?: number
}

export interface SourcesView {
  roots: SourceRoot[]
  candidates: SourceCandidate[]
  stats: { roots: number; candidates: number; previewable: number }
}

export async function fetchIngestionSources(): Promise<SourcesView> {
  const data = await getEnvelope<{ sources: SourcesView }>(INGESTION_SOURCES)
  return data.sources
}

export async function previewIngestion(source: IngestSourceInput, rewrite = false): Promise<IngestPlan> {
  const data = await postEnvelope<{ plan: IngestPlan }>(INGESTION_PREVIEW, { source, rewrite })
  return data.plan
}

export interface DirectResult {
  result: IngestResult
  plan: {
    plan_id: string
    level: string
    adapter: string
    rewritten: boolean
    dedupe: DedupeItem[]
    warnings: string[]
    merge_suggestion: MergeSuggestion | null
  }
}

/** 直通：文本 → 草稿（一次调用；文本默认 L2，显式 allow_rewrite 才调 LLM 重写）*/
export async function directIngestion(body: {
  text: string
  form: IngestForm
  label?: string
  allow_rewrite?: boolean
  confirm: true
}): Promise<DirectResult> {
  return postEnvelope<DirectResult>(INGESTION_DIRECT, body)
}

export async function commitIngestion(body: {
  source: IngestSourceInput
  plan_id: string
  confirm: true
  form: IngestForm
  archive_id?: string
  merge_into?: string
  /** S5：只产出草稿（不安装/不注册/不启用） */
  draft_only?: boolean
}): Promise<IngestResult> {
  const data = await postEnvelope<{ result: IngestResult }>(INGESTION_COMMIT, body)
  return data.result
}
