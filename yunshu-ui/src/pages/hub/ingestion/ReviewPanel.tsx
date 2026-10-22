/**
 * 外来草稿评审 / 放行（从技能中心·维护整体迁入）。
 *
 * 安全语义保持：阻断项单条不放行；force 豁免要「勾选 → 勾豁免 → 填原因 → 二次确认」；
 * 批量放行只认显式勾选；驳回 = 删除草稿，先 window.confirm。
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Inbox, Loader2, RefreshCw } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import { authHeader } from '../../../lib/apiToken'
import {
  SKILLS_MGMT_QUEUE,
  SKILLS_MGMT_QUEUE_PUBLISH_BATCH,
  skillById,
  skillPublish,
  skillReview,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

interface QueueRow {
  id?: string
  name?: string
  source?: string
  review?: { blocked?: boolean; review_verdict?: string } | null
}
interface QueueResult { ok?: boolean; error?: string; total?: number; items?: QueueRow[] }
interface BatchResult { ok?: boolean; error?: string; published?: string[]; failed?: { id?: string; error?: string }[] }

export default function ReviewPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [queue, setQueue] = useState<QueueResult | null>(null)
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

  useEffect(() => { void loadQueue() }, [loadQueue])

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
      {msg && <div className="mb-2 flex items-center gap-1.5 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300"><CheckCircle2 size={12} /> {msg}</div>}
      {!queue && busy !== 'queue' && <div className="text-[11px] text-slate-500">未取到队列。</div>}
      {queue && rows.length === 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-slate-500"><Inbox size={11} /> 队列为空 —— 没有待评审/放行的外来技能。</div>
      )}

      {batchOpen && (
        <div className="mb-2 rounded border border-amber-800/60 bg-amber-950/20 p-2 text-[11px] text-slate-300">
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
              <div key={'q-' + id} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
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
    </div>
  )
}
