/**
 * 外来草稿评审 / 放行（从技能中心·维护整体迁入）。
 *
 * 安全语义保持：阻断项单条不放行；force 豁免要「勾选 → 勾豁免 → 填原因 → 二次确认」；
 * 批量放行只认显式勾选；驳回 = 删除草稿，先 window.confirm。
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Inbox, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { Badge, ErrorBox, hubDelete, hubGet, hubPost } from '../components/ui'
import { authHeader } from '../../../lib/apiToken'
import {
  INGESTION_DRAFTS,
  ingestionDraft,
  ingestionDraftAutobind,
  ingestionDraftImplement,
  ingestionDraftInstallSkill,
  ingestionDraftRegister,
  ingestionDraftRegisterTool,
  SKILLS_MGMT_QUEUE,
  SKILLS_MGMT_QUEUE_PUBLISH_BATCH,
  skillById,
  skillPublish,
  skillReview,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

interface QueueRow {
  id?: string
  name?: string
  source?: string
  review?: { blocked?: boolean; review_verdict?: string } | null
}
interface QueueResult { ok?: boolean; error?: string; total?: number; items?: QueueRow[] }
/** 吞噬骨架草稿（GET /api/ingestion/drafts）：隔离区，不注册、不进技能队列。 */
interface DraftRow {
  draft_id: string
  form?: string
  status?: string
  enabled?: boolean
  skill_id?: string
  skill_name?: string
  created_at?: string
  warnings?: string[]
  draft_dir?: string
  source_ref?: string
  bound_steps?: number
  total_steps?: number
  implemented?: boolean
}

interface BindReport {
  bound: number
  total: number
  model?: string
  unbound?: { step_id?: string; reason?: string }[]
}
interface BatchResult { ok?: boolean; error?: string; published?: string[]; failed?: { id?: string; error?: string }[] }

export default function ReviewPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [queue, setQueue] = useState<QueueResult | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [draftErr, setDraftErr] = useState('')
  const [bind, setBind] = useState<{ id: string; report: BindReport } | null>(null)
  const [impl, setImpl] = useState<{
    id: string
    validation: { ok?: boolean; errors?: string[]; warnings?: string[] }
    code: string
  } | null>(null)
  const [sel, setSel] = useState<string[]>([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchForce, setBatchForce] = useState(false)
  const [batchReason, setBatchReason] = useState('')
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadQueue = useCallback(async () => {
    setBusy('queue'); setErr('')
    try {
      const r = await hubGet<QueueResult>(SKILLS_MGMT_QUEUE + '?limit=100')
      if (r?.ok === false) { bad('读取待放行队列失败', r); setQueue(null); setSel([]) } else {
        setQueue(r); setSel((prev) => prev.filter((id) => (r?.items ?? []).some((q) => q.id === id)))
      }
    } catch (e) { fail('读取待放行队列失败', e) } finally { setBusy('') }
  }, [])

  /** 骨架草稿不走技能队列（那只是技能草稿），必须单独列 —— 否则生成完看起来像"没生成"。 */
  const loadDrafts = useCallback(async () => {
    setBusy('drafts'); setDraftErr('')
    try {
      const r = await hubGet<{ data?: { items?: DraftRow[] }; items?: DraftRow[] }>(INGESTION_DRAFTS)
      const items = r?.data?.items ?? r?.items ?? []
      setDrafts(items.filter((d) => d && d.draft_id))
    } catch (e) {
      setDraftErr('读取骨架草稿失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadQueue() }, [loadQueue])
  useEffect(() => { void loadDrafts() }, [loadDrafts])

  const removeDraft = async (id: string) => {
    if (!window.confirm('确定删除骨架草稿「' + id + '」？整个目录会删除，不可恢复。')) return
    setBusy('draft-del:' + id); setDraftErr(''); setMsg('')
    try {
      await hubDelete(ingestionDraft(id))
      setMsg('已删除骨架草稿 ' + id)
      await loadDrafts()
    } catch (e) {
      setDraftErr('删除骨架草稿失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  /** 后端 RFC9457 的 detail 比 hubPost 的 "HTTP 400" 有用得多（会列出未绑定步骤）。 */
  const postJson = async <T,>(url: string, body: unknown): Promise<T> => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeader() },
      body: JSON.stringify(body),
    })
    const json = await res.json().catch(() => null)
    if (!res.ok) {
      const detail = json?.detail || json?.error || json?.title || ('HTTP ' + res.status)
      throw new Error(String(detail))
    }
    return (json?.data ?? json) as T
  }

  /** 按技能形态重装：技能才有审核/放行链路（骨架草稿没有评审语义）。 */
  const installSkill = async (d: DraftRow) => {
    const label = d.skill_name || d.skill_id || d.draft_id
    if (!window.confirm(`按技能形态重新安装「${label}」？\n来源：${d.source_ref || '（未知）'}\n若已存在同 id 技能会覆盖（会再确认一次）。`)) return
    setBusy('skill:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      let r: { result?: { skill_id?: string; warnings?: string[] } }
      try {
        r = await postJson(ingestionDraftInstallSkill(d.draft_id), { confirm: true })
      } catch (e) {
        const m = e instanceof Error ? e.message : String(e)
        if (!m.includes('已存在')) throw e
        if (!window.confirm('技能已存在：' + m + '\n确定覆盖？')) return
        r = await postJson(ingestionDraftInstallSkill(d.draft_id), { confirm: true, overwrite: true })
      }
      setMsg('已按技能安装：' + (r?.result?.skill_id || '') + '（之后走上面的技能队列审核/放行）')
      await loadQueue(); await loadDrafts()
    } catch (e) {
      setDraftErr('按技能安装失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  /** AI 自动绑定（先 dry-run 给人看；确认后才写回 + 登记）。 */
  const bindDraft = async (d: DraftRow) => {
    setBusy('bind:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ report: BindReport }>(ingestionDraftAutobind(d.draft_id), { apply: false })
      setBind({ id: d.draft_id, report: r.report })
    } catch (e) {
      setDraftErr('AI 绑定失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const applyBind = async (d: DraftRow) => {
    if (!bind || bind.id !== d.draft_id) return
    const { bound, total } = bind.report
    const full = total > 0 && bound >= total
    if (!window.confirm(full
      ? `将把 ${bound}/${total} 步写回草稿并登记为工作流（默认停用）。继续？`
      : `只绑定 ${bound}/${total} 步（仍有未绑定）；先写回草稿，不会登记。继续？`)) return
    setBusy('apply:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ report: BindReport }>(ingestionDraftAutobind(d.draft_id), { apply: true, confirm: true })
      const rep = r.report
      if (rep.total > 0 && rep.bound >= rep.total) {
        const reg = await postJson<{ workflow_id: string }>(ingestionDraftRegister(d.draft_id), { confirm: true })
        setMsg('已登记为工作流 ' + reg.workflow_id + '（默认停用；到「工作流」页启用）')
      } else {
        setMsg('已写回绑定 ' + rep.bound + '/' + rep.total + '；未绑定：' +
          (rep.unbound || []).map((u) => u.step_id).join('、'))
      }
      setBind(null); await loadDrafts()
    } catch (e) {
      setDraftErr('应用绑定失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  /** tool 草稿：AI 生成实现（先 dry-run 看校验，再写回）。 */
  const implementDraft = async (d: DraftRow) => {
    setBusy('impl:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{
        validation: { ok?: boolean; errors?: string[]; warnings?: string[] }
        code: string
      }>(ingestionDraftImplement(d.draft_id), { apply: false })
      setImpl({ id: d.draft_id, validation: r.validation, code: r.code })
    } catch (e) {
      setDraftErr('AI 实现失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const applyImpl = async (d: DraftRow) => {
    if (!impl || impl.id !== d.draft_id) return
    if (!impl.validation.ok) {
      setDraftErr('校验未通过，不能写回：' + (impl.validation.errors || []).join('；'))
      return
    }
    if (!window.confirm('把 AI 实现写回草稿（工具文件 / schema / 测试），不登记、不启用。继续？')) return
    setBusy('impl-apply:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      await postJson(ingestionDraftImplement(d.draft_id), { apply: true, confirm: true })
      setMsg('已写回实现草稿；复核后点「登记为工具」才会进入注册表')
      setImpl(null); await loadDrafts()
    } catch (e) {
      setDraftErr('写回实现失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const registerTool = async (d: DraftRow) => {
    if (!window.confirm('登记为动态工具？会写入 agent/tools/custom/ 与治理 YAML；风险默认 critical。')) return
    setBusy('tool-reg:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ tool_name: string }>(ingestionDraftRegisterTool(d.draft_id), { confirm: true })
      setMsg('已登记为工具 ' + r.tool_name + '（工具调用页可见；重启后仍在）')
      await loadDrafts()
    } catch (e) {
      setDraftErr('登记工具失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const registerDraft = async (d: DraftRow) => {
    if (!window.confirm('登记为工作流（默认停用，之后在「工作流」页启用）？')) return
    setBusy('register:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ workflow_id: string }>(ingestionDraftRegister(d.draft_id), { confirm: true })
      setMsg('已登记为工作流 ' + r.workflow_id + '（默认停用）')
      setBind(null); await loadDrafts()
    } catch (e) {
      setDraftErr('登记失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const review = async (id: string) => {
    setBusy('review:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillReview(id), {})
      if (r?.ok === false) { bad('审核失败', r); return }
      setMsg('已审核 ' + id); await loadQueue()
    } catch (e) { fail('审核失败', e) } finally { setBusy('') }
  }

  const publish = async (id: string) => {
    setBusy('publish:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillPublish(id))
      if (r?.ok === false) { bad('放行失败', r); return }
      setMsg('已放行 ' + id); await loadQueue()
    } catch (e) { fail('放行失败', e) } finally { setBusy('') }
  }

  const reject = async (id: string) => {
    if (!window.confirm('确定驳回并删除草稿「' + id + '」？删除后不可恢复。')) return
    setBusy('reject:' + id); setErr(''); setMsg('')
    try {
      const res = await fetch(skillById(id), { method: 'DELETE', headers: authHeader() })
      const body = await res.json().catch(() => null)
      if (!res.ok) { setErr('驳回失败：' + (body?.error ?? ('HTTP ' + res.status))); return }
      setMsg('已驳回 ' + id); setSel((prev) => prev.filter((x) => x !== id)); await loadQueue()
    } catch (e) { fail('驳回失败', e) } finally { setBusy('') }
  }

  const rows = queue?.items ?? []
  const selected = rows.filter((q) => q.id && sel.includes(q.id))
  const blocked = selected.filter((q) => q.review?.blocked === true)
  const hasBlocked = blocked.length > 0
  const canBatch = selected.length > 0 && (!hasBlocked || (batchForce && batchReason.trim().length >= 4))
  const loading = busy !== ''

  const submitBatch = async () => {
    setBusy('batch'); setErr(''); setMsg(''); setBatchResult(null)
    try {
      const body: Record<string, unknown> = { ids: selected.map((r) => r.id as string) }
      if (hasBlocked) { body.force = true; body.reason = batchReason.trim() }
      const r = await hubPost<BatchResult>(SKILLS_MGMT_QUEUE_PUBLISH_BATCH, body)
      setBatchResult(r)
      const okN = (r?.published ?? []).length
      const badN = (r?.failed ?? []).length
      setMsg(badN === 0 ? '已放行 ' + okN + ' 条' : '已放行 ' + okN + ' 条，失败 ' + badN + ' 条')
      setSel([]); setBatchOpen(false); setBatchForce(false); setBatchReason(''); await loadQueue()
    } catch (e) { fail('批量放行失败', e) } finally { setBusy('') }
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-slate-200">外来草稿 · 待评审</h3>
        <span className="text-[10px] text-slate-500">
          外来来源先落 draft；人工审核通过后才能发布
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} disabled={loading || sel.length === 0}
            aria-label="批量放行所选技能"
            onClick={() => { setBatchResult(null); setBatchForce(false); setBatchReason(''); setBatchOpen(true) }}>
            批量放行（{sel.length}）
          </button>
          <button type="button" className={BTN} onClick={() => void loadQueue()} disabled={loading} aria-label="刷新待放行队列">
            {busy === 'queue' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <div className="mb-2"><ErrorBox message={err} /></div>}
      {msg && <div className="mb-2 flex items-center gap-1.5 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300"><CheckCircle2 size={12} /> {msg}</div>}
      {!queue && busy !== 'queue' && <div className="text-[11px] text-slate-500">未取到队列。</div>}
      {queue && rows.length === 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-slate-500"><Inbox size={11} /> 队列为空 —— 没有待评审/放行的外来技能。</div>
      )}

      {batchOpen && (
        <div className="mb-2 rounded-sm border border-amber-800/60 bg-amber-950/20 p-2 text-[11px] text-slate-300">
          <div>将放行 {selected.length} 条：<span className="font-mono text-[10px] text-cyan-300">{selected.map((r) => r.id).join('、')}</span></div>
          {hasBlocked && (
            <>
              <div className="mt-1 text-red-300">
                其中 {blocked.length} 条被评审阻断（{blocked.map((r) => r.id).join('、')}）：force 会绕过强制审核链并写审计。
              </div>
              <label className="mt-1 flex items-center gap-1 text-[10px] text-slate-300">
                <input type="checkbox" checked={batchForce} onChange={(e) => setBatchForce(e.target.checked)}
                  aria-label="确认对阻断项使用 force 豁免" />
                我确认使用 force 豁免（写审计，可追溯）
              </label>
              <label className="mt-1 flex flex-col gap-1 text-[10px] text-slate-400">
                force 豁免原因（至少 4 字）
                <textarea className={INPUT} rows={2} value={batchReason}
                  onChange={(e) => setBatchReason(e.target.value)} aria-label="force 豁免原因" />
              </label>
            </>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <button type="button" className={BTN_EM} onClick={() => void submitBatch()}
              disabled={loading || !canBatch} aria-label="确认批量放行">
              {busy === 'batch' ? <Loader2 size={11} className="animate-spin" /> : null} 确认放行
            </button>
            <button type="button" className={BTN} onClick={() => setBatchOpen(false)} disabled={loading}>取消</button>
            {hasBlocked && !canBatch && <span className="text-[10px] text-amber-300">先勾选豁免并填写原因</span>}
          </div>
        </div>
      )}

      {batchResult && (batchResult.failed ?? []).length > 0 && (
        <div className="mb-2 flex flex-col gap-0.5 text-[10px] text-red-300">
          {(batchResult.failed ?? []).map((f) => <span key={'f-' + (f.id ?? '')}>{(f.id ?? '?') + '：' + (f.error ?? '未知错误')}</span>)}
        </div>
      )}
      {batchResult && (batchResult.failed ?? []).length === 0 && (
        <div className="mb-2 text-[10px] text-emerald-300">已放行：{(batchResult.published ?? []).join('、') || '(无)'}</div>
      )}

      {rows.length > 0 && (
        <div className="flex flex-col gap-1">
          {rows.map((q) => {
            const id = q.id ?? ''
            const isBlocked = q.review?.blocked === true
            const verdict = q.review?.review_verdict || ''
            return (
              <div key={'q-' + id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <input type="checkbox" checked={sel.includes(id)} onChange={() => setSel((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])}
                  disabled={loading} aria-label={'选择外来技能 ' + id} />
                <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                <span className="text-slate-300">{q.name ?? ''}</span>
                <span className="truncate text-[10px] text-slate-500">{q.source ?? ''}</span>
                <span className={'text-[10px] ' + (isBlocked ? 'text-red-300' : verdict ? 'text-emerald-300' : 'text-slate-400')}>
                  {isBlocked ? '评审阻断' : verdict ? '评审：' + verdict : '未审核'}
                </span>
                <span className="ml-auto flex items-center gap-1.5">
                  <button type="button" className={BTN_EM} onClick={() => void review(id)} disabled={loading} aria-label={'审核外来技能 ' + id}>
                    {busy === 'review:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 审核
                  </button>
                  <button type="button" className={BTN} onClick={() => void publish(id)} disabled={loading || isBlocked}
                    aria-label={'放行外来技能 ' + id}
                    title={isBlocked ? '阻断项单条不放行；豁免走批量放行并填原因' : '走强制审核链发布'}>
                    {busy === 'publish:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 放行
                  </button>
                  <button type="button" className={BTN_RED} onClick={() => void reject(id)} disabled={loading} aria-label={'驳回外来技能 ' + id}>
                    {busy === 'reject:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 驳回
                  </button>
                </span>
              </div>
            )
          })}
          {rows.some((q) => q.review?.blocked) && (
            <div className="text-[10px] text-red-300/80">阻断项单条不放行：先处理评审问题；豁免只能「勾选 → 勾豁免 → 填原因 → 二次确认」。</div>
          )}
        </div>
      )}

      {/* 吞噬骨架草稿：tool/workflow 形态的产物不进上面的技能队列，单独列出来 */}
      <div className="mt-4 border-t border-slate-800 pt-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-slate-200">吞噬生成的骨架草稿</h3>
          <span className="text-[10px] text-slate-500">
            工具 / 工作流形态：写到隔离目录，不注册、不启用，所以不会出现在上面的技能队列里
          </span>
          <button type="button" className={BTN + ' ml-auto'} onClick={() => void loadDrafts()} disabled={loading}
            aria-label="刷新骨架草稿">
            {busy === 'drafts' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
        {draftErr && <div className="mb-2"><ErrorBox message={draftErr} /></div>}
        {drafts.length === 0 ? (
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <Inbox size={11} /> 还没有骨架草稿。在「吞噬」里选「工具 / 工作流」形态生成后会出现在这里；选「技能」则直接进上面的技能队列。
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {drafts.map((d) => {
              const bound = d.bound_steps ?? 0
              const total = d.total_steps ?? 0
              const isWorkflow = d.form === 'workflow'
              const ready = total > 0 && bound >= total
              const canInstall = String(d.source_ref || '').startsWith('path:')
              return (
                <div key={'d-' + d.draft_id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="font-mono text-[10px] text-cyan-300">{d.draft_id}</span>
                  <Badge color="cyan">{isWorkflow ? '工作流' : '工具'}</Badge>
                  <span className="text-slate-300">{d.skill_name || d.skill_id || '—'}</span>
                  <span className="text-[10px] text-slate-500">{d.created_at || ''}</span>
                  {isWorkflow && (
                    <span className={'text-[10px] ' + (ready ? 'text-emerald-300' : 'text-amber-300')}>
                      绑定 {bound}/{total}
                    </span>
                  )}
                  {(d.warnings ?? []).length > 0 && (
                    <span className="text-[10px] text-amber-300" title={(d.warnings ?? []).join('\n')}>
                      警告 {(d.warnings ?? []).length} 条
                    </span>
                  )}
                  <span className="ml-auto flex flex-wrap items-center gap-1.5">
                    <span className="max-w-[300px] truncate font-mono text-[10px] text-slate-500" title={d.draft_dir}>{d.draft_dir}</span>
                    <button type="button" className={BTN} disabled={loading || !canInstall}
                      onClick={() => void installSkill(d)}
                      title={canInstall ? '把来源按技能形态重新安装（技能才有审核/放行链路）' : '文本来源没有保留原文，无法重装'}
                      aria-label={'按技能安装 ' + d.draft_id}>
                      按技能安装
                    </button>
                    {isWorkflow && (
                      <button type="button" className={BTN_EM} disabled={loading} onClick={() => void bindDraft(d)}
                        title="用 LLM 把步骤映射到真实工具与参数（先预览，不写盘）"
                        aria-label={'AI 绑定 ' + d.draft_id}>
                        {busy === 'bind:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} AI 绑定
                      </button>
                    )}
                    {isWorkflow && (
                      <button type="button" className={BTN_EM} disabled={loading || !ready}
                        onClick={() => void registerDraft(d)}
                        title={ready ? '登记为工作流（默认停用，需在「工作流」页启用）' : '先把全部步骤绑定到工具才能登记'}
                        aria-label={'登记为工作流 ' + d.draft_id}>
                        {busy === 'register:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} 登记
                      </button>
                    )}
                    {!isWorkflow && (
                      <button type="button" className={BTN_EM} disabled={loading}
                        onClick={() => void implementDraft(d)}
                        title="用 LLM 读取来源 SKILL.md/脚本并生成实现（先校验，不写盘）"
                        aria-label={'AI 实现 ' + d.draft_id}>
                        {busy === 'impl:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} AI 实现
                      </button>
                    )}
                    {!isWorkflow && (
                      <button type="button" className={BTN_EM} disabled={loading || !d.implemented}
                        onClick={() => void registerTool(d)}
                        title={d.implemented ? '登记为动态工具（写 custom/ + 治理 YAML，风险 critical）' : '先写回实现才能登记'}
                        aria-label={'登记为工具 ' + d.draft_id}>
                        {busy === 'tool-reg:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} 登记为工具
                      </button>
                    )}
                    <button type="button" className={BTN_RED} disabled={loading}
                      onClick={() => void removeDraft(d.draft_id)}
                      aria-label={'删除骨架草稿 ' + d.draft_id}>
                      {busy === 'draft-del:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} 删除
                    </button>
                  </span>
                  {bind?.id === d.draft_id && (
                    <div className="mt-1 flex w-full flex-col gap-1 rounded-sm border border-cyan-900/60 bg-cyan-950/20 p-2 text-[10px] text-slate-300">
                      <div>
                        AI 建议：绑定 <b className="text-cyan-300">{bind.report.bound}/{bind.report.total}</b> 步
                        {bind.report.model ? '（模型 ' + bind.report.model + '）' : ''}
                      </div>
                      {(bind.report.unbound ?? []).length > 0 && (
                        <div className="text-amber-300">
                          未绑定：{(bind.report.unbound ?? []).slice(0, 6)
                            .map((u) => u.step_id + (u.reason ? '（' + u.reason + '）' : '')).join('、')}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-1.5">
                        <button type="button" className={BTN_EM} disabled={loading}
                          onClick={() => void applyBind(d)} aria-label={'应用绑定 ' + d.draft_id}>
                          {busy === 'apply:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null}
                          {bind.report.total > 0 && bind.report.bound >= bind.report.total ? '应用并登记' : '仅写回'}
                        </button>
                        <button type="button" className={BTN} disabled={loading} onClick={() => setBind(null)}>取消</button>
                      </div>
                    </div>
                  )}
                  {impl?.id === d.draft_id && (
                    <div className="mt-1 flex w-full flex-col gap-1 rounded-sm border border-cyan-900/60 bg-cyan-950/20 p-2 text-[10px] text-slate-300">
                      <div>
                        AI 实现：
                        <b className={impl.validation.ok ? 'text-emerald-300' : 'text-red-300'}>
                          {impl.validation.ok ? '静态校验通过' : '校验未通过'}
                        </b>
                        {impl.validation.ok ? '（写回后仍需点「登记为工具」）' : ''}
                      </div>
                      {(impl.validation.errors ?? []).length > 0 && (
                        <div className="text-red-300">
                          {(impl.validation.errors ?? []).slice(0, 5).join('；')}
                        </div>
                      )}
                      {(impl.validation.warnings ?? []).length > 0 && (
                        <div className="text-amber-300">{(impl.validation.warnings ?? []).slice(0, 3).join('；')}</div>
                      )}
                      <div className="flex flex-wrap items-center gap-1.5">
                        <button type="button" className={BTN_EM} disabled={loading || !impl.validation.ok}
                          onClick={() => void applyImpl(d)} aria-label={'应用实现 ' + d.draft_id}>
                          {busy === 'impl-apply:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null}
                          应用实现
                        </button>
                        <button type="button" className={BTN} disabled={loading} onClick={() => setImpl(null)}>取消</button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
