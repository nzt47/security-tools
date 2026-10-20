/**
 * 技能中心 · 维护 —— 批量运维面（8 条此前未接线的端点）
 * ------------------------------------------------------------------
 *   three-layer/summary (GET)   三层架构摘要（元数据层 token 估算 / 脚本数）
 *   cleanup/report      (GET)   清理总览（孤儿 + 无用候选，只报告）
 *   cleanup/orphans     (POST)  清除孤儿残留（dry_run 默认 true）
 *   cleanup/unused      (POST)  物理删除无用技能（dry_run 默认 true）
 *   auto-merge          (POST)  自动合并高相似对（Jaccard ≥ 阈值）
 *   merge               (POST)  合并指定两个技能（可回滚，走备份）
 *   queue               (GET)   外部导入·待人工放行队列
 *   <id>/review         (POST)  对一条外来草稿跑审核（重复/安全/质量）
 *   <id>/publish        (POST)  放行（强制审核链；阻断需先解除）
 *   queue/publish-batch (POST)  批量放行（显式 ids；阻断项要另勾 force 豁免 + 填原因）
 *   upload              (POST)  上传 zip 技能包（multipart）
 *   import/claude       (GET)   扫描 ~/.claude/skills（只读预览）
 *   import/claude       (POST)  Claude 技能包 → manifest.json + skill.md → 落库
 *   abstract-from-memory(POST)  从记忆里抽象新技能草稿
 *   install/precheck    (GET)   外部来源预检（阻断项/兼容分/能力重叠）
 *   install             (POST)  按来源安装（github: / url: / registry: / local:）
 *
 * 【2026-10-20「导入」区合并】上传 zip / 从 .claude 导入 / 按来源安装 原本是三张并列
 * 卡片，但三者走的是**同一条落库链路**（安装 → 评审-评估 → 草稿 → 下面「待放行」）。
 * 合并为一个「导入技能」区 + 方式切换（默认按来源安装）：入口只剩一个，方式只是参数。
 *
 * 【为什么删除类动作默认 dry_run，且真删要点两下】
 *   这四条里只有 report/summary 是只读。其余一旦真删就是**物理删除**
 *   （cleanup/unused 的 removed、cleanup/orphans 的 cleaned）。
 *   所以：先预览（dry_run=true）→ 看清列表 → 勾「我确认」→ 才允许真删。
 *   一份"点一次就删干净"的维护面板，迟早会在误点上付出代价。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Boxes, CheckCircle2, Download, FileUp, Inbox, Loader2,
  RefreshCw, Sparkles, Trash2, Wand2,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import { authHeader } from '../../../lib/apiToken'
import {
  SKILLS_MGMT_ABSTRACT_FROM_MEMORY,
  SKILLS_MGMT_AUTO_MERGE,
  SKILLS_MGMT_IMPORT_CLAUDE,
  SKILLS_MGMT_INSTALL,
  SKILLS_MGMT_INSTALL_PREPRECHECK,
  SKILLS_MGMT_CLEANUP_ORPHANS,
  SKILLS_MGMT_CLEANUP_REPORT,
  SKILLS_MGMT_CLEANUP_UNUSED,
  SKILLS_MGMT_MERGE,
  SKILLS_MGMT_QUEUE,
  SKILLS_MGMT_QUEUE_PUBLISH_BATCH,
  SKILLS_MGMT_THREE_LAYER_SUMMARY,
  SKILLS_MGMT_UPLOAD,
  skillById,
  skillPublish,
  skillReview,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] leading-relaxed text-slate-400'

interface LayerSummary {
  layer1_metadata?: { skill_count?: number; estimated_tokens_per_skill?: number; estimated_total_tokens?: number }
  layer2_instruction?: { on_demand?: boolean; description?: string }
  layer3_tools?: { total_scripts?: number; total_temp_files?: number; description?: string }
}

interface OrphanRow { id?: string; reason?: string }
interface UnusedRow { id?: string; status?: string; usage_count?: number; idle_days?: number; reason?: string }

interface CleanupReport {
  ok?: boolean
  error?: string
  total_skills?: number
  orphans?: OrphanRow[]
  orphan_count?: number
  unused?: UnusedRow[]
  unused_count?: number
}

interface CleanupResult {
  ok?: boolean
  error?: string
  dry_run?: boolean
  found?: number
  cleaned?: unknown[]
  removed?: unknown[]
}

interface AutoMergeResult {
  ok?: boolean
  error?: string
  scanned_pairs?: number
  merged_pairs?: unknown[]
  skipped?: number
}

interface MergeResult { ok?: boolean; error?: string }

interface DraftRow {
  draft_name?: string
  draft_skill_id?: string
  cluster_size?: number
  success_rate?: number
  quality_gate_passed?: boolean
  registered?: boolean
  duplicate_of?: string
  quality_gate_reasons?: string[]
}

interface AbstractResult {
  ok?: boolean
  error?: string
  total_clusters?: number
  passed_clusters?: number
  registered_count?: number
  drafts?: DraftRow[]
}

/** Claude 技能包候选（GET /api/skills-mgmt/import/claude 的 items[]） */
interface ClaudeSkillRow {
  id?: string
  dir_name?: string
  name?: string
  description?: string
  ready?: boolean
  error?: string
  warnings?: string[]
  body_chars?: number
}

interface ClaudeScan {
  ok?: boolean
  error?: string
  base_dir?: string
  total?: number
  ready?: number
  items?: ClaudeSkillRow[]
}

interface ClaudeImportResult {
  ok?: boolean
  error?: string
  total?: number
  imported?: { id?: string; name?: string; scripts_count?: number }[]
  failed?: { id?: string; error?: string }[]
}

/** 外部导入·待放行队列的一行（GET /api/skills-mgmt/queue） */
interface QueueRow {
  id?: string
  name?: string
  description?: string
  source?: string
  status?: string
  created_at?: string
  review?: {
    auto_assessed?: boolean
    review_verdict?: string
    blocked?: boolean
  } | null
}

/** 外来安装预检结果（GET /api/skills-mgmt/install/precheck） */
interface InstallPrecheck {
  ok?: boolean
  error?: string
  scheme?: string
  source?: string
  skill_id?: string
  skill_name?: string
  blocked?: boolean
  overlap_action?: string
  native_dups?: { id?: string; name?: string }[]
  findings?: { code?: string; severity?: string; message?: string }[]
  compatibility_score?: number
}

/** 外来安装结果（POST /api/skills-mgmt/install） */
interface InstallResult {
  ok?: boolean
  error?: string
  skill?: { id?: string; name?: string; status?: string }
}

interface QueueResult {
  ok?: boolean
  error?: string
  total?: number
  items?: QueueRow[]
}

/** 批量放行的逐条结果（POST /api/skills-mgmt/queue/publish-batch） */
interface BatchResult {
  ok?: boolean
  error?: string
  total?: number
  force?: boolean
  published?: string[]
  failed?: { id?: string; error?: string; code?: string }[]
}

/** 「导入」区的方式切换：三条入口同一条落库链路，方式只是参数 */
const IMPORT_KINDS = [
  { key: 'source', label: '按来源安装', hint: 'github:user/repo · url:https://… · registry:id · local:路径' },
  { key: 'zip', label: '上传 zip', hint: '本地 .zip 技能包（安装到文件仓库）' },
  { key: 'claude', label: '从 .claude 导入', hint: '读 ~/.claude/skills（源目录只读）' },
] as const
type ImportKind = (typeof IMPORT_KINDS)[number]['key']

function Section({ title, desc, children, extra }: {
  title: string; desc?: string; children: React.ReactNode; extra?: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold text-slate-200">{title}</h3>
        {desc && <span className="text-[10px] text-slate-500">{desc}</span>}
        <div className="ml-auto flex items-center gap-1.5">{extra}</div>
      </div>
      {children}
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className="font-mono text-sm text-cyan-300">{value}</div>
      {hint && <div className="text-[9px] text-slate-600">{hint}</div>}
    </div>
  )
}

export default function SkillMaintenance() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [layers, setLayers] = useState<LayerSummary | null>(null)
  const [report, setReport] = useState<CleanupReport | null>(null)

  const [orphanResult, setOrphanResult] = useState<CleanupResult | null>(null)
  const [unusedResult, setUnusedResult] = useState<CleanupResult | null>(null)
  const [autoMergeResult, setAutoMergeResult] = useState<AutoMergeResult | null>(null)
  const [mergeResult, setMergeResult] = useState<MergeResult | null>(null)
  const [abstractResult, setAbstractResult] = useState<AbstractResult | null>(null)
  // 「导入技能」区当前方式：默认按来源安装（最常用）；三条入口共用同一个落库链路
  const [importKind, setImportKind] = useState<ImportKind>('source')
  const [uploadInfo, setUploadInfo] = useState('')
  const [claudeScan, setClaudeScan] = useState<ClaudeScan | null>(null)
  const [claudeResult, setClaudeResult] = useState<ClaudeImportResult | null>(null)
  const [queue, setQueue] = useState<QueueResult | null>(null)
  // 批量放行：只认逐条勾选（不提供全选），阻断项要另勾豁免 + 填原因
  // 按来源安装（从「扩展市场」收敛过来的技能安装入口）
  const [installSource, setInstallSource] = useState('')
  const [installPrecheck, setInstallPrecheck] = useState<InstallPrecheck | null>(null)
  const [installResult, setInstallResult] = useState<InstallResult | null>(null)
  const [queueSel, setQueueSel] = useState<string[]>([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchForce, setBatchForce] = useState(false)
  const [batchReason, setBatchReason] = useState('')
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null)

  // 真删确认（两条独立的勾选：孤儿与无用是两把不同的扫帚）
  const [confirmOrphans, setConfirmOrphans] = useState(false)
  const [confirmUnused, setConfirmUnused] = useState(false)

  const [unusedDays, setUnusedDays] = useState('')
  const [archivedDays, setArchivedDays] = useState('')
  const [minJaccard, setMinJaccard] = useState('0.85')
  const [maxMerges, setMaxMerges] = useState('10')
  const [srcId, setSrcId] = useState('')
  const [dstId, setDstId] = useState('')
  const [strategy, setStrategy] = useState('auto')
  const [absDays, setAbsDays] = useState('30')
  const [absMax, setAbsMax] = useState('5')
  const [absRegister, setAbsRegister] = useState(false)

  const fileRef = useRef<HTMLInputElement | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadLayers = useCallback(async () => {
    setBusy('layers'); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; error?: string; summary?: LayerSummary }>(SKILLS_MGMT_THREE_LAYER_SUMMARY)
      if (r?.ok === false) { bad('读取三层摘要失败', r); setLayers(null) } else setLayers(r?.summary ?? null)
    } catch (e) { fail('读取三层摘要失败', e) } finally { setBusy('') }
  }, [])

  const loadReport = useCallback(async () => {
    setBusy('report'); setErr('')
    try {
      const r = await hubGet<CleanupReport>(SKILLS_MGMT_CLEANUP_REPORT)
      if (r?.ok === false) { bad('读取清理总览失败', r); setReport(null) } else setReport(r)
    } catch (e) { fail('读取清理总览失败', e) } finally { setBusy('') }
  }, [])

  const loadQueue = useCallback(async () => {
    setBusy('queue'); setErr('')
    try {
      const r = await hubGet<QueueResult>(SKILLS_MGMT_QUEUE + '?limit=100')
      if (r?.ok === false) {
        bad('读取待放行队列失败', r); setQueue(null); setQueueSel([])
      } else {
        setQueue(r)
        // 刷新后剪掉已不在队列里的勾选（否则「批量放行（n）」的计数会虚高）
        setQueueSel((prev) => prev.filter((id) => (r?.items ?? []).some((q) => q.id === id)))
      }
    } catch (e) { fail('读取待放行队列失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadLayers(); void loadReport(); void loadQueue() }, [loadLayers, loadReport, loadQueue])

  /** 对一条外来草稿跑审核（重复/安全/质量）；结果直接反映在队列行的评审列上 */
  const reviewQueued = async (id: string) => {
    setBusy('queue-review:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillReview(id), {})
      if (r?.ok === false) { bad('审核失败', r); return }
      setMsg('已审核 ' + id + '（评审结果见队列）')
      await loadQueue()
    } catch (e) { fail('审核失败', e) } finally { setBusy('') }
  }

  /** 放行：走强制审核链（阻断的条目按钮不可点，不做"强制豁免"的默认路径） */
  const publishQueued = async (id: string) => {
    setBusy('queue-publish:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillPublish(id))
      if (r?.ok === false) { bad('放行失败', r); return }
      setMsg('已放行 ' + id)
      await loadQueue()
      await loadReport()
    } catch (e) { fail('放行失败', e) } finally { setBusy('') }
  }

  /** 驳回：删除这条外来草稿（不可逆 ⇒ 先确认；与评估 Tab 原先的「驳回」同一语义） */
  const rejectQueued = async (id: string) => {
    if (!window.confirm('确定驳回并删除草稿「' + id + '」？删除后不可恢复。')) return
    setBusy('queue-reject:' + id); setErr(''); setMsg('')
    try {
      const res = await fetch(skillById(id), { method: 'DELETE', headers: authHeader() })
      const body = await res.json().catch(() => null)
      if (!res.ok) { setErr('驳回失败：' + (body?.error ?? ('HTTP ' + res.status))); return }
      setMsg('已驳回 ' + id)
      setQueueSel((prev) => prev.filter((x) => x !== id))
      await loadQueue(); await loadReport()
    } catch (ev) { fail('驳回失败', ev) } finally { setBusy('') }
  }

  /** 勾选/取消一条待放行条目（批量放行只认显式勾选） */
  const toggleQueueSel = (id: string) => setQueueSel((prev) => (
    prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
  ))

  /** 打开批量放行面板 —— 这是第一次确认，面板里的「确认放行」是第二次 */
  const openBatchPanel = () => {
    setBatchResult(null); setBatchForce(false); setBatchReason(''); setBatchOpen(true)
  }

  /**
   * 提交批量放行。
   *
   * 【为什么 force 只在面板里给】阻断项 = 安全审查未过；单条按钮永远不放行阻断项，
   * 豁免只能走「勾选 → 勾豁免 → 填原因 → 确认」这条多步路径，且服务端还会
   * 再校验一次原因长度（不依赖前端把关）。
   */
  const submitBatch = async () => {
    const ids = selectedRows.map((r) => r.id as string)
    setBusy('queue-batch'); setErr(''); setMsg(''); setBatchResult(null)
    try {
      const body: Record<string, unknown> = { ids }
      if (hasBlocked) { body.force = true; body.reason = batchReason.trim() }
      const r = await hubPost<BatchResult>(SKILLS_MGMT_QUEUE_PUBLISH_BATCH, body)
      setBatchResult(r)
      const okN = (r?.published ?? []).length
      const badN = (r?.failed ?? []).length
      setMsg(badN === 0 ? `已放行 ${okN} 条` : `已放行 ${okN} 条，失败 ${badN} 条（见下方明细）`)
      setQueueSel([]); setBatchOpen(false); setBatchForce(false); setBatchReason('')
      await loadQueue(); await loadReport()
    } catch (e) { fail('批量放行失败', e) } finally { setBusy('') }
  }

  /** 预检：只拉取 + 评估，不落库；blocked ⇒ 安装按钮置灰 */
  const precheckInstall = async () => {
    const source = installSource.trim()
    setBusy('precheck'); setErr(''); setMsg('')
    setInstallPrecheck(null); setInstallResult(null)
    try {
      const r = await hubGet<InstallPrecheck>(
        SKILLS_MGMT_INSTALL_PREPRECHECK + '?source=' + encodeURIComponent(source))
      if (r?.ok === false) { bad('预检失败', r); return }
      setInstallPrecheck(r)
      setMsg(r?.blocked ? '预检发现阻断项：先处理再安装' : '预检通过（安装后会先落草稿）')
    } catch (ev) { fail('预检失败', ev) } finally { setBusy('') }
  }

  /** 安装：与 zip 上传 / .claude 导入同一个落库链路；装完进「待放行」队列 */
  const installSourceSkill = async () => {
    const source = installSource.trim()
    setBusy('install'); setErr(''); setMsg(''); setInstallResult(null)
    try {
      const r = await hubPost<InstallResult>(SKILLS_MGMT_INSTALL, { source })
      if (r?.ok === false) { bad('安装失败', r); return }
      setInstallResult(r)
      setMsg('已安装 ' + (r?.skill?.id ?? source) + '（已落草稿，去下面队列放行）')
      setInstallPrecheck(null)
      await loadQueue(); await loadReport()
    } catch (ev) { fail('安装失败', ev) } finally { setBusy('') }
  }

  const runOrphans = async (dryRun: boolean) => {
    setBusy('orphans'); setErr(''); setMsg(''); setOrphanResult(null)
    try {
      const r = await hubPost<CleanupResult>(SKILLS_MGMT_CLEANUP_ORPHANS, { dry_run: dryRun })
      if (r?.ok === false) { bad('清理孤儿失败', r); return }
      setOrphanResult(r)
      setMsg(dryRun ? '孤儿预览完成（未删除任何东西）' : '孤儿清理已执行')
      if (!dryRun) { setConfirmOrphans(false); await loadReport() }
    } catch (e) { fail('清理孤儿失败', e) } finally { setBusy('') }
  }

  const runUnused = async (dryRun: boolean) => {
    setBusy('unused'); setErr(''); setMsg(''); setUnusedResult(null)
    try {
      const body: Record<string, unknown> = { dry_run: dryRun }
      if (unusedDays.trim()) body.unused_days = Number(unusedDays)
      if (archivedDays.trim()) body.archived_days = Number(archivedDays)
      const r = await hubPost<CleanupResult>(SKILLS_MGMT_CLEANUP_UNUSED, body)
      if (r?.ok === false) { bad('淘汰无用技能失败', r); return }
      setUnusedResult(r)
      setMsg(dryRun ? '无用技能预览完成（未删除任何东西）' : '无用技能淘汰已执行')
      if (!dryRun) { setConfirmUnused(false); await loadReport() }
    } catch (e) { fail('淘汰无用技能失败', e) } finally { setBusy('') }
  }

  const runAutoMerge = async () => {
    setBusy('auto-merge'); setErr(''); setMsg(''); setAutoMergeResult(null)
    try {
      const r = await hubPost<AutoMergeResult>(SKILLS_MGMT_AUTO_MERGE, {
        min_jaccard: Number(minJaccard || 0.85),
        max_merges: Number(maxMerges || 10),
      })
      if (r?.ok === false) { bad('自动合并失败', r); return }
      setAutoMergeResult(r)
      setMsg('自动合并完成')
      await loadReport()
    } catch (e) { fail('自动合并失败', e) } finally { setBusy('') }
  }

  const runMerge = async () => {
    setBusy('merge'); setErr(''); setMsg(''); setMergeResult(null)
    try {
      const r = await hubPost<MergeResult>(SKILLS_MGMT_MERGE, {
        src_id: srcId.trim(),
        dst_id: dstId.trim(),
        strategy,
        rebind_feedback: true,
      })
      if (r?.ok === false) { bad('合并失败', r); return }
      setMergeResult(r)
      setMsg('合并完成：' + srcId.trim() + ' → ' + dstId.trim())
      await loadReport()
    } catch (e) { fail('合并失败', e) } finally { setBusy('') }
  }

  const uploadZip = async (file: File) => {
    setBusy('upload'); setErr(''); setMsg(''); setUploadInfo('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      // multipart 不能走 hubPost（它只发 JSON）：这里自备请求头，但令牌仍从同一处取
      const res = await fetch(SKILLS_MGMT_UPLOAD, { method: 'POST', headers: authHeader(), body: fd })
      const data = (await res.json()) as { ok?: boolean; error?: string; skill?: { skill_id?: string; name?: string } }
      if (!res.ok || data?.ok === false) {
        setErr('上传失败：' + (data?.error || 'HTTP ' + res.status))
        return
      }
      setUploadInfo('已安装：' + (data?.skill?.skill_id || data?.skill?.name || '（后端未回 id）'))
      setMsg('技能包上传成功')
      await loadReport()
    } catch (e) { fail('上传失败', e) } finally { setBusy('') }
  }

  /**
   * 扫描 ~/.claude/skills —— **只读预览**：先看清有哪些、哪些不可导入，再决定导什么。
   * Claude 包只有 SKILL.md，落库前由服务端补 manifest.json（见 claude_import.py）。
   */
  const scanClaude = async () => {
    setBusy('claude-scan'); setErr(''); setMsg(''); setClaudeScan(null); setClaudeResult(null)
    try {
      const r = await hubGet<ClaudeScan>(SKILLS_MGMT_IMPORT_CLAUDE)
      if (r?.ok === false) { bad('扫描 .claude/skills 失败', r); return }
      setClaudeScan(r)
      setMsg('扫描完成：' + (r.items?.length ?? 0) + ' 个技能包（可导入 ' + (r.ready ?? 0) + '）')
    } catch (e) { fail('扫描 .claude/skills 失败', e) } finally { setBusy('') }
  }

  const importClaude = async (ids?: string[]) => {
    setBusy('claude-import'); setErr(''); setMsg(''); setClaudeResult(null)
    try {
      const r = await hubPost<ClaudeImportResult>(SKILLS_MGMT_IMPORT_CLAUDE, ids ? { ids } : {})
      // ok=false 但确有导入 ⇒ 是"部分失败"，不能整块当失败吞掉（要能看到哪条坏了）
      if (r?.ok === false && (r.imported?.length ?? 0) === 0) { bad('导入 Claude 技能失败', r); return }
      setClaudeResult(r)
      setMsg('导入完成：成功 ' + (r.imported?.length ?? 0) + ' 个，失败 ' + (r.failed?.length ?? 0) + ' 个')
      await loadReport()
    } catch (e) { fail('导入 Claude 技能失败', e) } finally { setBusy('') }
  }

  const runAbstract = async () => {
    setBusy('abstract'); setErr(''); setMsg(''); setAbstractResult(null)
    try {
      const r = await hubPost<AbstractResult>(SKILLS_MGMT_ABSTRACT_FROM_MEMORY, {
        days: Number(absDays || 30),
        max_skills: Number(absMax || 5),
        auto_register: absRegister,
      })
      if (r?.ok === false) { bad('从记忆抽象失败', r); return }
      setAbstractResult(r)
      setMsg('抽象完成')
      await loadReport()
    } catch (e) { fail('从记忆抽象失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const queueRows = queue?.items ?? []
  const selectedRows = queueRows.filter((q) => q.id && queueSel.includes(q.id))
  const selectedBlocked = selectedRows.filter((q) => q.review?.blocked === true)
  const hasBlocked = selectedBlocked.length > 0
  // 与服务端 WAIVER_MIN_REASON_LEN 同口径（服务端仍会二次校验，前端只负责不给错按钮）
  const reasonReady = batchReason.trim().length >= 4
  const canSubmitBatch = selectedRows.length > 0 && (!hasBlocked || (batchForce && reasonReady))

  return (
    <div className="space-y-3">
      {err && <ErrorBox message={err} />}
      {msg && (
        <div className="flex items-center gap-1.5 rounded-md border border-emerald-800/60 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300">
          <CheckCircle2 size={12} /> {msg}
        </div>
      )}

      {/* ── 三层架构摘要 ── */}
      <Section
        title="三层架构摘要"
        desc="元数据层（常驻上下文）/ 说明层（按需加载）/ 脚本层（执行结果才进上下文）"
        extra={
          <button type="button" className={BTN} onClick={() => void loadLayers()} disabled={loading} aria-label="刷新三层摘要">
            {busy === 'layers' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        }
      >
        {!layers && busy !== 'layers' && <div className="text-[11px] text-slate-500">未取到摘要。</div>}
        {layers && (
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            <Stat label="技能数（L1）" value={layers.layer1_metadata?.skill_count ?? '-'} />
            <Stat
              label="元数据 token 总量"
              value={layers.layer1_metadata?.estimated_total_tokens ?? '-'}
              hint={'约 ' + Math.round(layers.layer1_metadata?.estimated_tokens_per_skill ?? 0) + ' / 技能'}
            />
            <Stat label="脚本数（L3）" value={layers.layer3_tools?.total_scripts ?? '-'} />
            <Stat label="临时文件（L3）" value={layers.layer3_tools?.total_temp_files ?? '-'} />
          </div>
        )}
      </Section>

      {/* ── 清理总览 ── */}
      <Section
        title="清理总览"
        desc="孤儿残留 + 无用技能候选（只报告，不删除）"
        extra={
          <button type="button" className={BTN} onClick={() => void loadReport()} disabled={loading} aria-label="刷新清理总览">
            {busy === 'report' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        }
      >
        {report && (
          <>
            <div className="mb-2 grid grid-cols-3 gap-1.5">
              <Stat label="技能总数" value={report.total_skills ?? '-'} />
              <Stat label="孤儿残留" value={report.orphan_count ?? 0} hint="索引里有、文件已不在" />
              <Stat label="无用候选" value={report.unused_count ?? 0} hint="归档超期 / 零使用且停用" />
            </div>

            {(report.orphan_count ?? 0) > 0 && (
              <div className="mb-2">
                <div className="mb-1 text-[11px] text-amber-300">孤儿残留</div>
                <div className="flex flex-col gap-1">
                  {(report.orphans ?? []).slice(0, 20).map((o, i) => (
                    <div key={(o.id ?? '') + i} className="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                      <span className="font-mono text-cyan-300">{o.id ?? '?'}</span>
                      <span className="truncate text-slate-500">{o.reason ?? ''}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {(report.unused_count ?? 0) > 0 && (
              <div className="mb-2">
                <div className="mb-1 text-[11px] text-amber-300">无用技能候选</div>
                <div className="flex flex-col gap-1">
                  {(report.unused ?? []).slice(0, 20).map((u, i) => (
                    <div key={(u.id ?? '') + i} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                      <span className="font-mono text-cyan-300">{u.id ?? '?'}</span>
                      <span className="text-slate-500">{u.reason ?? ''}</span>
                      <span className="ml-auto font-mono text-[10px] text-slate-600">
                        usage={u.usage_count ?? 0} · idle={u.idle_days ?? '-'}d
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-red-900/40 bg-red-500/5 p-2">
              <span className="text-[10px] text-red-300">
                删除是物理删除：先预览，再勾确认才能真删。
              </span>
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmOrphans} onChange={(e) => setConfirmOrphans(e.target.checked)} aria-label="确认清除孤儿" />
                我确认清除孤儿
              </label>
              <button type="button" className={BTN} onClick={() => void runOrphans(true)} disabled={loading} aria-label="预览清除孤儿">
                {busy === 'orphans' ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} 预览孤儿清理
              </button>
              <button type="button" className={BTN_RED} onClick={() => void runOrphans(false)} disabled={loading || !confirmOrphans} aria-label="清除孤儿">
                清除孤儿
              </button>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2 rounded border border-red-900/40 bg-red-500/5 p-2">
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                闲置天数
                <input className={INPUT + ' w-16'} value={unusedDays} onChange={(e) => setUnusedDays(e.target.value)} aria-label="无用技能闲置天数" />
              </label>
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                归档天数
                <input className={INPUT + ' w-16'} value={archivedDays} onChange={(e) => setArchivedDays(e.target.value)} aria-label="归档天数" />
              </label>
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmUnused} onChange={(e) => setConfirmUnused(e.target.checked)} aria-label="确认淘汰无用技能" />
                我确认淘汰
              </label>
              <button type="button" className={BTN} onClick={() => void runUnused(true)} disabled={loading} aria-label="预览淘汰无用技能">
                {busy === 'unused' ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} 预览无用淘汰
              </button>
              <button type="button" className={BTN_RED} onClick={() => void runUnused(false)} disabled={loading || !confirmUnused} aria-label="淘汰无用技能">
                淘汰无用技能
              </button>
            </div>

            {orphanResult && <pre className={PRE + ' mt-2'}>{JSON.stringify(orphanResult, null, 2)}</pre>}
            {unusedResult && <pre className={PRE + ' mt-2'}>{JSON.stringify(unusedResult, null, 2)}</pre>}
          </>
        )}
        {!report && busy !== 'report' && <div className="text-[11px] text-slate-500">未取到清理总览。</div>}
      </Section>

      {/* ── 重复合并 ── */}
      <Section title="重复合并" desc="自动合并高相似对（Jaccard ≥ 阈值，仅合并 recommend_action=merge 的对）">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            阈值
            <input className={INPUT + ' w-20'} value={minJaccard} onChange={(e) => setMinJaccard(e.target.value)} aria-label="自动合并阈值" />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            最多合并
            <input className={INPUT + ' w-16'} value={maxMerges} onChange={(e) => setMaxMerges(e.target.value)} aria-label="最多合并对数" />
          </label>
          <button type="button" className={BTN_EM} onClick={() => void runAutoMerge()} disabled={loading} aria-label="自动合并重复技能">
            {busy === 'auto-merge' ? <Loader2 size={11} className="animate-spin" /> : <Boxes size={11} />} 自动合并
          </button>
        </div>
        {autoMergeResult && (
          <div className="mb-2 text-[11px] text-slate-400">
            扫描 {autoMergeResult.scanned_pairs ?? 0} 对 · 合并 {(autoMergeResult.merged_pairs ?? []).length} 对 · 跳过 {autoMergeResult.skipped ?? 0}
            {(autoMergeResult.merged_pairs ?? []).length > 0 && (
              <pre className={PRE + ' mt-1'}>{JSON.stringify(autoMergeResult.merged_pairs, null, 2)}</pre>
            )}
          </div>
        )}

        <div className="border-t border-slate-800 pt-2">
          <div className="mb-1 text-[11px] text-slate-400">手工合并指定两个技能（保留 dst，删除 src）</div>
          <div className="flex flex-wrap items-center gap-2">
            <input className={INPUT + ' w-40'} value={srcId} onChange={(e) => setSrcId(e.target.value)} placeholder="src_id（被合并）" aria-label="合并源技能 id" />
            <input className={INPUT + ' w-40'} value={dstId} onChange={(e) => setDstId(e.target.value)} placeholder="dst_id（保留）" aria-label="合并目标技能 id" />
            <select className={INPUT + ' w-28'} value={strategy} onChange={(e) => setStrategy(e.target.value)} aria-label="合并策略">
              <option value="auto">auto</option>
              <option value="keep_dst">keep_dst</option>
              <option value="keep_src">keep_src</option>
            </select>
            <button
              type="button"
              className={BTN_RED}
              onClick={() => void runMerge()}
              disabled={loading || srcId.trim() === '' || dstId.trim() === ''}
              aria-label="合并两个技能"
            >
              {busy === 'merge' ? <Loader2 size={11} className="animate-spin" /> : <AlertTriangle size={11} />} 合并
            </button>
          </div>
          {mergeResult && <pre className={PRE + ' mt-2'}>{JSON.stringify(mergeResult, null, 2)}</pre>}
        </div>
      </Section>

      {/* ── 导入技能：三条入口合成一个区 ──
          上传 zip / 从 .claude/skills / 按来源安装 走的是同一条落库链路（都会走
          评审-评估并落草稿 → 下面「待放行」队列）。三块分开放时，"把一个外部技能
          弄进来"要在页面上找三处；合并为一个区 + 方式切换后，入口只有一个，方式只是参数。 */}
      <Section
        title="导入技能"
        desc="三条入口同一条落库链路：zip 包 / ~/.claude/skills / 外部来源（github: · url: · registry: · local:）—— 装完进「待放行」队列"
      >
        <div className="mb-2 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="技能的导入方式">
          {IMPORT_KINDS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={importKind === t.key}
              title={t.hint}
              onClick={() => setImportKind(t.key)}
              className={'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ' + (
                importKind === t.key
                  ? 'bg-cyan-500/15 font-medium text-cyan-300'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              )}
            >
              {t.label}
            </button>
          ))}
          <span className="ml-auto hidden font-mono text-[10px] text-slate-600 md:inline">
            {IMPORT_KINDS.find((t) => t.key === importKind)?.hint}
          </span>
        </div>

        {importKind === 'zip' && (
          <>
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          className="hidden"
          aria-label="选择技能 zip 包"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void uploadZip(f)
            e.target.value = ''
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={BTN_EM} onClick={() => fileRef.current?.click()} disabled={loading} aria-label="选择并上传 zip">
            {busy === 'upload' ? <Loader2 size={11} className="animate-spin" /> : <FileUp size={11} />} 选择 .zip 并上传
          </button>
          {uploadInfo && <span className="text-[11px] text-emerald-300">{uploadInfo}</span>}
        </div>
          </>
        )}

        {importKind === 'claude' && (
          <>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <button type="button" className={BTN} onClick={() => void scanClaude()} disabled={loading} aria-label="扫描 .claude/skills">
            {busy === 'claude-scan' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 扫描
          </button>
          <span className="text-[10px] text-slate-500">目录：{claudeScan?.base_dir ?? '~/.claude/skills'}</span>
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void importClaude()}
            disabled={loading || (claudeScan?.ready ?? 0) === 0}
            aria-label="导入全部 Claude 技能"
          >
            {busy === 'claude-import' ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 全部导入
          </button>
        </div>
        {!claudeScan && busy !== 'claude-scan' && (
          <div className="text-[11px] text-slate-500">
            先点「扫描」—— 只读，不会写入或删除 ~/.claude/skills。
          </div>
        )}
        {(claudeScan?.items?.length ?? 0) > 0 && (
          <div className="flex flex-col gap-1">
            {(claudeScan?.items ?? []).map((c) => (
              <div key={c.id ?? c.dir_name} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-cyan-300">{c.id}</span>
                <span className="text-slate-300">{c.name ?? ''}</span>
                <span className="truncate text-[10px] text-slate-500">{c.description ?? ''}</span>
                <span className="ml-auto flex items-center gap-1.5">
                  {c.ready
                    ? <span className="text-[10px] text-emerald-400">可导入</span>
                    : <span className="text-[10px] text-amber-400">{c.error ?? '不可导入'}</span>}
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => void importClaude([c.id ?? ''])}
                    disabled={loading || !c.ready}
                    aria-label={'导入 Claude 技能 ' + (c.id ?? '')}
                  >
                    <Download size={10} /> 导入
                  </button>
                </span>
                {(c.warnings?.length ?? 0) > 0 && (
                  <div className="w-full text-[10px] text-amber-400/80">{(c.warnings ?? []).join('；')}</div>
                )}
              </div>
            ))}
          </div>
        )}
        {claudeScan && (claudeScan.items?.length ?? 0) === 0 && (
          <div className="text-[11px] text-slate-500">没有找到技能包（目录不存在或没有 SKILL.md）。</div>
        )}
        {claudeResult && (
          <div className="mt-2 text-[11px] text-slate-400">
            成功 {(claudeResult.imported ?? []).length} · 失败 {(claudeResult.failed ?? []).length}
            {(claudeResult.imported ?? []).length > 0 && (
              <div className="mt-1 text-[10px] text-emerald-300">
                已落库：{(claudeResult.imported ?? []).map((i) => i.id).join('、')}
              </div>
            )}
            {(claudeResult.failed ?? []).length > 0 && (
              <div className="mt-1 flex flex-col gap-0.5 text-[10px] text-red-300">
                {(claudeResult.failed ?? []).map((f) => (
                  <span key={f.id}>{(f.id ?? '?') + '：' + (f.error ?? '未知错误')}</span>
                ))}
              </div>
            )}
          </div>
        )}
          </>
        )}

        {importKind === 'source' && (
          <>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className={INPUT + ' w-96'}
            value={installSource}
            onChange={(ev) => {
              setInstallSource(ev.target.value)
              setInstallPrecheck(null); setInstallResult(null)
            }}
            placeholder="github:user/repo 或 url:https://… 或 registry:id"
            aria-label="外部技能来源"
          />
          <button
            type="button"
            className={BTN}
            onClick={() => void precheckInstall()}
            disabled={loading || !installSource.trim()}
            aria-label="预检外部技能"
          >
            {busy === 'precheck' ? <Loader2 size={11} className="animate-spin" /> : null} 预检
          </button>
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void installSourceSkill()}
            disabled={loading || !installSource.trim() || installPrecheck?.blocked === true}
            aria-label="安装外部技能"
            title={installPrecheck?.blocked
              ? '预检有阻断项：先处理再安装（安装按钮已置灰）'
              : '安装后先落草稿，放行在下面队列'}
          >
            {busy === 'install' ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 安装
          </button>
        </div>
        {installPrecheck && (
          <div className="mt-2 text-[11px] text-slate-400">
            <div>
              预检：<span className="font-mono text-cyan-300">{installPrecheck.skill_id ?? '?'}</span>
              {' · '}
              {installPrecheck.blocked
                ? <span className="text-red-300">有阻断项</span>
                : <span className="text-emerald-300">无阻断</span>}
              {typeof installPrecheck.compatibility_score === 'number' && (
                <> · 兼容分 {installPrecheck.compatibility_score}</>
              )}
            </div>
            {(installPrecheck.native_dups ?? []).length > 0 && (
              <div className="text-[10px] text-amber-300">
                与已有能力重叠（按增量吸收，不阻断）：
                {(installPrecheck.native_dups ?? []).map((d) => d.id).join('、')}
              </div>
            )}
            {(installPrecheck.findings ?? []).length > 0 && (
              <div className="mt-1 flex flex-col gap-0.5 text-[10px] text-slate-500">
                {(installPrecheck.findings ?? []).slice(0, 6).map((f, i) => (
                  <span key={(f.code ?? '') + i}>
                    {'[' + (f.severity ?? '') + '] ' + (f.message ?? '')}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        {installResult?.skill && (
          <div className="mt-1 text-[10px] text-emerald-300">
            已落库：{installResult.skill.id}（{installResult.skill.status ?? 'draft'}）
          </div>
        )}
          </>
        )}
      </Section>

      {/* ── 外部导入 · 待放行 ── */}
      <Section
        title={'外部导入 · 待放行（' + queueRows.length + '）'}
        desc="github / url / local / registry / external_agent 来的技能先落成 draft，人工审核通过后才能发布"
        extra={
          <>
            <button
              type="button"
              className={BTN}
              onClick={openBatchPanel}
              disabled={loading || queueSel.length === 0}
              aria-label="批量放行所选技能"
              title={queueSel.length === 0
                ? '先逐条勾选要放行的条目（不提供全选 / 一键全放）'
                : '打开批量放行确认面板（第二步确认）'}
            >
              批量放行（{queueSel.length}）
            </button>
            <button type="button" className={BTN} onClick={() => void loadQueue()} disabled={loading} aria-label="刷新待放行队列">
              {busy === 'queue' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
            </button>
          </>
        }
      >
        {!queue && busy !== 'queue' && <div className="text-[11px] text-slate-500">未取到队列。</div>}
        {queue && queueRows.length === 0 && (
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <Inbox size={11} /> 队列为空 —— 没有待放行的外来技能。
          </div>
        )}

        {/* 批量放行确认面板：第一次确认=点「批量放行」，这里换到第二次确认 */}
        {batchOpen && (
          <div className="mb-2 rounded border border-amber-800/60 bg-amber-950/20 p-2 text-[11px] text-slate-300">
            <div>
              将放行 {selectedRows.length} 条：
              <span className="font-mono text-[10px] text-cyan-300">
                {selectedRows.map((r) => r.id).join('、')}
              </span>
            </div>
            {hasBlocked && (
              <>
                <div className="mt-1 text-red-300">
                  其中 {selectedBlocked.length} 条被评审阻断（{selectedBlocked.map((r) => r.id).join('、')}）：
                  强行放行会绕过强制审核链，并写入 review_waiver_publish 审计。
                </div>
                <label className="mt-1 flex items-center gap-1 text-[10px] text-slate-300">
                  <input
                    type="checkbox"
                    checked={batchForce}
                    onChange={(e) => setBatchForce(e.target.checked)}
                    aria-label="确认对阻断项使用 force 豁免"
                  />
                  我确认对上述阻断项使用 force 豁免（写审计，可追溯）
                </label>
                <label className="mt-1 flex flex-col gap-1 text-[10px] text-slate-400">
                  force 豁免原因（至少 4 字，写入审计；不是走过场）
                  <textarea
                    className={INPUT}
                    rows={2}
                    value={batchReason}
                    onChange={(e) => setBatchReason(e.target.value)}
                    aria-label="force 豁免原因"
                  />
                </label>
              </>
            )}
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                className={BTN_EM}
                onClick={() => void submitBatch()}
                disabled={loading || !canSubmitBatch}
                aria-label="确认批量放行"
              >
                {busy === 'queue-batch' ? <Loader2 size={11} className="animate-spin" /> : null} 确认放行
              </button>
              <button
                type="button"
                className={BTN}
                onClick={() => setBatchOpen(false)}
                disabled={loading}
                aria-label="取消批量放行"
              >
                取消
              </button>
              {hasBlocked && !canSubmitBatch && (
                <span className="text-[10px] text-amber-300">先勾选豁免并填写原因，而不是直接点确认</span>
              )}
            </div>
          </div>
        )}

        {batchResult && ((batchResult.failed ?? []).length > 0) && (
          <div className="mb-2 flex flex-col gap-0.5 text-[10px] text-red-300">
            {(batchResult.failed ?? []).map((f) => (
              <span key={'batch-fail-' + (f.id ?? '')}>{(f.id ?? '?') + '：' + (f.error ?? '未知错误')}</span>
            ))}
          </div>
        )}
        {batchResult && (batchResult.failed ?? []).length === 0 && (
          <div className="mb-2 text-[10px] text-emerald-300">
            已放行：{(batchResult.published ?? []).join('、') || '(无)'}
          </div>
        )}

        {queueRows.length > 0 && (
          <div className="flex flex-col gap-1">
            {queueRows.map((q) => {
              const id = q.id ?? ''
              const blocked = q.review?.blocked === true
              const verdict = q.review?.review_verdict || ''
              return (
                <div key={'queue-' + id} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <input
                    type="checkbox"
                    checked={queueSel.includes(id)}
                    onChange={() => toggleQueueSel(id)}
                    disabled={loading}
                    aria-label={'选择外来技能 ' + id}
                  />
                  <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                  <span className="text-slate-300">{q.name ?? ''}</span>
                  <span className="truncate text-[10px] text-slate-500">{q.source ?? ''}</span>
                  <span className={'text-[10px] ' + (blocked ? 'text-red-300' : verdict ? 'text-emerald-300' : 'text-slate-400')}>
                    {blocked ? '评审阻断' : verdict ? '评审：' + verdict : '未审核'}
                  </span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <button
                      type="button"
                      className={BTN_EM}
                      onClick={() => void reviewQueued(id)}
                      disabled={loading}
                      aria-label={'审核外来技能 ' + id}
                    >
                      {busy === 'queue-review:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 审核
                    </button>
                    <button
                      type="button"
                      className={BTN}
                      onClick={() => void publishQueued(id)}
                      disabled={loading || blocked}
                      aria-label={'放行外来技能 ' + id}
                      title={blocked
                        ? '评审阻断：单条不放行；如需豁免请勾选后走「批量放行」并填写原因'
                        : '走强制审核链发布'}
                    >
                      {busy === 'queue-publish:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 放行
                    </button>
                    <button
                      type="button"
                      className={BTN_RED}
                      onClick={() => void rejectQueued(id)}
                      disabled={loading}
                      aria-label={'驳回外来技能 ' + id}
                      title="驳回 = 删除这条草稿（不可恢复；与评估 Tab 原先的「驳回」同语义）"
                    >
                      {busy === 'queue-reject:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 驳回
                    </button>
                  </span>
                </div>
              )
            })}
            {queueRows.some((q) => q.review?.blocked) && (
              <div className="text-[10px] text-red-300/80">
                阻断项单条不放行：先在技能详情里处理评审问题；确需豁免只能「勾选 → 勾豁免 → 填原因 → 二次确认」，
                不提供一键全放。
              </div>
            )}
          </div>
        )}
      </Section>

      {/* ── 从记忆抽象 ── */}
      <Section title="从记忆抽象新技能" desc="按最近记忆聚类 → 过质量闸 → 出草稿（默认只出草稿，不自动注册）">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            回溯天数
            <input className={INPUT + ' w-16'} value={absDays} onChange={(e) => setAbsDays(e.target.value)} aria-label="抽象回溯天数" />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            最多草稿
            <input className={INPUT + ' w-16'} value={absMax} onChange={(e) => setAbsMax(e.target.value)} aria-label="最多草稿数" />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            <input type="checkbox" checked={absRegister} onChange={(e) => setAbsRegister(e.target.checked)} aria-label="自动注册草稿" />
            直接注册（不推荐）
          </label>
          <button type="button" className={BTN_EM} onClick={() => void runAbstract()} disabled={loading} aria-label="从记忆抽象技能">
            {busy === 'abstract' ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />} 开始抽象
          </button>
        </div>
        {abstractResult && (
          <div className="text-[11px] text-slate-400">
            聚类 {abstractResult.total_clusters ?? 0} · 过闸 {abstractResult.passed_clusters ?? 0} · 已注册 {abstractResult.registered_count ?? 0}
            {(abstractResult.drafts?.length ?? 0) > 0 && (
              <div className="mt-1 flex flex-col gap-1">
                {(abstractResult.drafts ?? []).map((d, i) => (
                  <div key={(d.draft_skill_id ?? '') + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-cyan-300">{d.draft_name ?? d.draft_skill_id ?? '(草稿)'}</span>
                      <span className="text-[10px] text-slate-500">聚类 {d.cluster_size ?? '-'} · 成功率 {d.success_rate ?? '-'}</span>
                      <span className={'ml-auto text-[10px] ' + (d.quality_gate_passed ? 'text-emerald-400' : 'text-amber-400')}>
                        {d.quality_gate_passed ? '过闸' : '未过闸'}
                      </span>
                    </div>
                    {(d.quality_gate_reasons?.length ?? 0) > 0 && (
                      <div className="text-[10px] text-slate-500">{(d.quality_gate_reasons ?? []).join('；')}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
            {abstractResult.drafts && abstractResult.drafts.length === 0 && (
              <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-600">
                <Wand2 size={10} /> 这次没有够格的聚类 —— 不是失败。
              </div>
            )}
          </div>
        )}
      </Section>
    </div>
  )
}
