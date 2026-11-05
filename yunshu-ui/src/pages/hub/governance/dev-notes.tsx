/**
 * 开发随记（问题与想法）—— 挂在「反馈中心」里的一个 Tab
 * ------------------------------------------------------------------
 * 【为什么放在反馈中心】这里已经有 submit / list / resolve 三条端点与受理界面；
 * 「用着用着发现的问题、突然想到的做法」本质上也是**待解决的一条记录**，
 * 没必要为它再造一套存储与路由（云枢的老毛病：同一件事开第二个入口）。
 *
 * 【约定】category = 'dev-note'：
 *   - feedback_type = 'suggestion'（沿用枚举，不改后端契约）
 *   - trace_id = 'dev-note:<时间戳>'（后端必填，仅作标识，不校验存在性）
 *   - context.source = 'workbench'（区分人写的与 agent 记的，后者可传 'agent'）
 *
 * 【为什么要它】OpenAI 式的「用完就忘」：发现的问题只在当轮对话里说一句，
 * 下一轮没人记得。落成一条记录 + 一个 UI 列表，才有"留待解决"。
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Lightbulb, Loader2, RefreshCw, Save } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import { FEEDBACK_LIST, FEEDBACK_SUBMIT, feedbackResolve } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

/** 约定的分类键：只认这一个，避免和用户反馈混在一起 */
export const DEV_NOTE_CATEGORY = 'dev-note'

interface NoteRow {
  feedback_id?: string
  id?: string
  comment?: string
  status?: string
  created_at?: string
  context?: Record<string, unknown>
}

export default function DevNotesPanel() {
  const [rows, setRows] = useState<NoteRow[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [showResolved, setShowResolved] = useState(false)

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const r = await hubGet<unknown>(
        FEEDBACK_LIST + '?category=' + DEV_NOTE_CATEGORY + '&limit=100',
      )
      const rec = r as { data?: NoteRow[]; success?: boolean; error?: string } | null
      if (rec?.success === false) { setErr('读取失败：' + (rec.error || 'success=false')); return }
      const arr = Array.isArray(rec?.data) ? rec.data : (Array.isArray(r) ? (r as NoteRow[]) : [])
      setRows(arr)
    } catch (e) {
      setErr('读取开发随记失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async () => {
    const body = text.trim()
    if (!body) { setErr('先写点什么'); return }
    setBusy('save'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; success?: boolean; error?: string }>(FEEDBACK_SUBMIT, {
        trace_id: 'dev-note:' + Date.now(),
        feedback_type: 'suggestion',
        category: DEV_NOTE_CATEGORY,
        rating: 0,
        comment: body,
        context: { source: 'workbench' },
      })
      if (r?.ok === false || r?.success === false) {
        setErr('记录失败：' + (r?.error || '后端拒绝'))
        return
      }
      setText('')
      setMsg('已记下（反馈中心 → 开发随记）')
      await load()
    } catch (e) {
      setErr('记录失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const resolve = async (id: string) => {
    setBusy('resolve:' + id); setErr(''); setMsg('')
    try {
      await hubPost(feedbackResolve(id), { resolution: '已处理（开发随记）' })
      setMsg('已标记为已解决：' + id)
      await load()
    } catch (e) {
      setErr('标记失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const visible = rows.filter((r) => showResolved || (r.status || 'open') !== 'resolved')
  const openCount = rows.filter((r) => (r.status || 'open') !== 'resolved').length

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Lightbulb size={13} className="text-cyan-400" />
        <span className="text-xs font-medium text-slate-200">问题与想法（开发随记）</span>
        <span className="text-[10px] text-slate-500">
          待解决 {openCount} · 共 {rows.length}（复用反馈存储，category=dev-note）
        </span>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} aria-label="显示已解决" />
            显示已解决
          </label>
          <button type="button" className={BTN} onClick={() => void load()} disabled={busy !== ''} aria-label="刷新随记">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 flex items-start gap-2">
        <textarea
          className={INPUT + ' min-h-[64px]'}
          aria-label="记录问题或想法"
          placeholder="用着用着发现的问题、突然想到的做法……记在这里，留待解决（会在本页列表里出现）"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="button" className={BTN_EM + ' shrink-0'} onClick={() => void save()}
          disabled={busy !== '' || !text.trim()} aria-label="记下来">
          {busy === 'save' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 记下来
        </button>
      </div>

      {visible.length === 0 ? (
        <div className="flex items-center gap-2 py-4 text-[11px] text-slate-500">
          <CheckCircle2 size={12} /> 没有待解决的条目{showResolved ? '' : '（勾「显示已解决」看历史）'}
        </div>
      ) : (
        <ul className="space-y-1.5">
          {visible.map((row) => {
            const id = String(row.feedback_id || row.id || '')
            const resolved = (row.status || 'open') === 'resolved'
            return (
              <li key={id} className="flex items-start gap-2 rounded-sm border border-slate-800 bg-slate-950/50 px-2 py-1.5">
                <AlertTriangle size={11} className={resolved ? 'mt-0.5 shrink-0 text-slate-600' : 'mt-0.5 shrink-0 text-amber-400'} />
                <div className="min-w-0 flex-1">
                  <div className={'whitespace-pre-wrap wrap-break-word text-[11px] ' + (resolved ? 'text-slate-500 line-through' : 'text-slate-200')}>
                    {row.comment || '（空）'}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-600">
                    <code>{id}</code>
                    {row.created_at && <span>{String(row.created_at).slice(0, 19)}</span>}
                    <span>{row.status || 'open'}</span>
                    {row.context && row.context.source ? <span>来源 {String(row.context.source)}</span> : null}
                  </div>
                </div>
                {!resolved && (
                  <button type="button" className={BTN + ' shrink-0'} aria-label={'标记已解决 ' + id}
                    onClick={() => void resolve(id)} disabled={busy !== ''}>
                    {busy === 'resolve:' + id ? <Loader2 size={10} className="animate-spin" /> : <CheckCircle2 size={10} />} 已解决
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
