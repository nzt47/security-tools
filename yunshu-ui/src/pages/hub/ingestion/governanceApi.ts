/**
 * P6 存量治理 API：scan（只读）/ plan（确定性计划）/ apply（默认 dry-run）。
 */
import { postEnvelope } from '@/api/envelope'
import {
  INGESTION_GOVERNANCE_APPLY,
  INGESTION_GOVERNANCE_PLAN,
  INGESTION_GOVERNANCE_SCAN,
} from '@/api/endpoints'

export interface GovernanceIssueItem {
  id: string
  name: string
  status: string
  enabled: boolean
  version: string
  source: string
  category: string
  usage_count: number
  description_chars: number
  content_chars: number
  headings: string[]
  issues: string[]
  primary_issue: string
  duplicates: {
    other_id: string
    other_name: string
    jaccard: number
    recommend_action: string
  }[]
}

export interface GovernanceScan {
  ok: boolean
  generated_at: string
  total: number
  items: GovernanceIssueItem[]
  duplicates: Record<string, unknown>[]
  orphans: { id?: string; reason?: string }[]
  unused: { id?: string; reason?: string }[]
  summary: Record<string, number>
}

export interface GovernanceAction {
  action: string
  target: string
  title: string
  reason: string
  evidence: Record<string, unknown>
  payload: Record<string, unknown>
  destructive: boolean
}

export interface GovernancePlan {
  ok: boolean
  plan_id: string
  generated_at: string
  actions: GovernanceAction[]
  summary: { actions: number; by_action: Record<string, number> }
  scan_summary: Record<string, number>
  requires_confirm: boolean
}

export interface GovernanceApplyResult {
  ok: boolean
  dry_run: boolean
  applied: boolean
  plan_id: string
  action: string
  target: string
  would?: Record<string, unknown>
  result?: Record<string, unknown>
  audit?: { seq: number; self_hash: string }
}

export async function scanGovernance(limit = 500): Promise<GovernanceScan> {
  const data = await postEnvelope<{ scan: GovernanceScan }>(INGESTION_GOVERNANCE_SCAN, { limit })
  return data.scan
}

export async function planGovernance(limit = 500): Promise<GovernancePlan> {
  const data = await postEnvelope<{ plan: GovernancePlan }>(INGESTION_GOVERNANCE_PLAN, { limit })
  return data.plan
}

export async function applyGovernance(body: {
  plan_id: string
  action: string
  target: string
  confirm: boolean
  dry_run: boolean
}): Promise<GovernanceApplyResult> {
  const data = await postEnvelope<{ result: GovernanceApplyResult }>(INGESTION_GOVERNANCE_APPLY, body)
  return data.result
}
