/**
 * 技能中心 · 维护 —— 批量运维面（8 条此前未接线的端点）
 * ------------------------------------------------------------------
 *   three-layer/summary (GET)   三层架构摘要（元数据层 token 估算 / 脚本数）
 *   cleanup/report      (GET)   清理总览（孤儿 + 无用候选，只报告）
 *   cleanup/orphans     (POST)  清除孤儿残留（dry_run 默认 true）
 *   cleanup/unused      (POST)  物理删除无用技能（dry_run 默认 true）
 *   auto-merge          (POST)  自动合并高相似对（Jaccard ≥ 阈值）
 *   merge               (POST)  合并指定两个技能（可回滚，走备份）
 *   upload              (POST)  上传 zip 技能包（multipart）
 *   abstract-from-memory(POST)  从记忆里抽象新技能草稿
 *
 * 【为什么删除类动作默认 dry_run，且真删要点两下】
 *   这四条里只有 report/summary 是只读。其余一旦真删就是**物理删除**
 *   （cleanup/unused 的 removed、cleanup/orphans 的 cleaned）。
 *   所以：先预览（dry_run=true）→ 看清列表 → 勾「我确认」→ 才允许真删。
 *   一份"点一次就删干净"的维护面板，迟早会在误点上付出代价。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Boxes, CheckCircle2, FileUp, Loader2, RefreshCw, Sparkles,
  Trash2, Wand2,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import { authHeader } from '../../../lib/apiToken'
import {
  SKILLS_MGMT_ABSTRACT_FROM_MEMORY,
  SKILLS_MGMT_AUTO_MERGE,
  SKILLS_MGMT_CLEANUP_ORPHANS,
  SKILLS_MGMT_CLEANUP_REPORT,
  SKILLS_MGMT_CLEANUP_UNUSED,
  SKILLS_MGMT_MERGE,
  SKILLS_MGMT_THREE_LAYER_SUMMARY,
  SKILLS_MGMT_UPLOAD,
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
  const [uploadInfo, setUploadInfo] = useState('')

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

  useEffect(() => { void loadLayers(); void loadReport() }, [loadLayers, loadReport])

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

      {/* ── 上传 zip ── */}
      <Section title="上传技能包" desc="zip 包 → 安装到三层架构文件仓库（会走评审-评估链）">
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
