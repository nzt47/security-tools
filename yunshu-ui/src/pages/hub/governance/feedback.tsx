/**
 * 反馈中心 —— 用户反馈的受理、汇总、分析报告与优质案例（7 条端点）
 * ------------------------------------------------------------------
 *   GET  /api/feedback/list            列表（按类型/状态/分类/用户/trace 过滤 + 分页）
 *   GET  /api/feedback/summary         聚合统计（时间窗 days）
 *   GET  /api/feedback/report          分析报告（负面反馈 + 优化建议 + 优质案例）
 *   GET  /api/feedback/quality-cases    优质案例（tags / 分页）
 *   GET  /api/feedback/<id>             单条详情
 *   POST /api/feedback/<id>/resolve     标记已解决
 *   POST /api/feedback/submit           提交一条反馈
 *
 * 【为什么这页存在】反馈此前只有后端：/api/feedback 一族在前端零引用，
 * 用户提的问题进了一个没人打开的表。这里把它变成可处理的工作台面：
 * 看汇总 → 看报告 → 逐条查看 → 标记已解决。
 *
 * 【注意信封】这一族返回 {data, success}（不是 {ok, data}），与 /api/skills-mgmt
 * 等返回 {ok:true,...} 的端点不同。此处按真实契约读 data/success，不做"猜键"。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  BookOpen, CheckCircle2, FileText, Loader2, MessageSquare, RefreshCw, Send, Star,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  FEEDBACK_LIST,
  FEEDBACK_QUALITY_CASES,
  FEEDBACK_REPORT,
  FEEDBACK_SUBMIT,
  FEEDBACK_SUMMARY,
  feedbackById,
  feedbackResolve,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface FeedbackRow {
  feedback_id?: string
  id?: string
  feedback_type?: string
  category?: string
  status?: string
  rating?: number
  comment?: string
  created_at_iso?: string
  trace_id?: string
  user_id?: string
}

interface Summary {
  total_feedback?: number
  like_count?: number
  dislike_count?: number
  satisfaction_rate_percent?: number
  quality_cases_count?: number
  by_type?: Record<string, number>
  by_status?: Record<string, number>
  by_category?: Record<string, number>
  time_range_days?: number
}

type Tab = 'list' | 'report' | 'cases' | 'submit'

export default function FeedbackCenter() {
  const [tab, setTab] = useState<Tab>('list')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [summary, setSummary] = useState<Summary | null>(null)
  const [rows, setRows] = useState<FeedbackRow[]>([])
  const [total, setTotal] = useState<number>(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)

  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [cases, setCases] = useState<Record<string, unknown>[]>([])

  const [subTrace, setSubTrace] = useState('')
  const [subType, setSubType] = useState('suggestion')
  const [subRating, setSubRating] = useState('5')
  const [subComment, setSubComment] = useState('')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  /** 这一族用 {data, success} 信封：读 data，遇 success=false 视为失败。 */
  const pickData = <T,>(r: unknown): T | null => {
    const rec = r as { data?: T; success?: boolean; error?: string } | null
    if (!rec) return null
    if (rec.success === false) {
      setErr('后端返回失败：' + (rec.error || 'success=false'))
      return null
    }
    return (rec.data ?? (r as T)) as T
  }

  const loadSummary = useCallback(async () => {
    setBusy('summary'); setErr('')
    try {
      const r = await hubGet<unknown>(FEEDBACK_SUMMARY + '?days=7')
      const d = pickData<Summary>(r)
      if (d) setSummary(d)
    } catch (e) { fail('读取反馈汇总失败', e) } finally { setBusy('') }
  }, [])

  const loadList = useCallback(async (status = statusFilter, type = typeFilter) => {
    setBusy('list'); setErr('')
    try {
      const params = '?limit=50&offset=0'
        + (status ? '&status=' + encodeURIComponent(status) : '')
        + (type ? '&feedback_type=' + encodeURIComponent(type) : '')
      const r = await hubGet<unknown>(FEEDBACK_LIST + params)
      const d = pickData<{ items?: FeedbackRow[]; total?: number } | FeedbackRow[]>(r)
      const arr = Array.isArray(d) ? d : (d?.items ?? [])
      setRows(arr)
      setTotal(Array.isArray(d) ? arr.length : (d?.total ?? arr.length))
    } catch (e) { fail('读取反馈列表失败', e) } finally { setBusy('') }
  }, [statusFilter, typeFilter])

  const loadReport = useCallback(async () => {
    setBusy('report'); setErr('')
    try {
      const r = await hubGet<unknown>(FEEDBACK_REPORT + '?days=7')
      const d = pickData<Record<string, unknown>>(r)
      setReport(d)
    } catch (e) { fail('读取反馈报告失败', e) } finally { setBusy('') }
  }, [])

  const loadCases = useCallback(async () => {
    setBusy('cases'); setErr('')
    try {
      const r = await hubGet<unknown>(FEEDBACK_QUALITY_CASES + '?limit=20')
      const d = pickData<Record<string, unknown>[]>(r)
      setCases(Array.isArray(d) ? d : [])
    } catch (e) { fail('读取优质案例失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => {
    void loadSummary()
    void loadList()
  }, [loadSummary, loadList])

  useEffect(() => {
    if (tab === 'report' && !report) void loadReport()
    if (tab === 'cases' && cases.length === 0) void loadCases()
  }, [tab, report, cases.length, loadReport, loadCases])

  const openDetail = async (id: string) => {
    setBusy('detail'); setErr(''); setDetail(null)
    try {
      const r = await hubGet<unknown>(feedbackById(id))
      const d = pickData<Record<string, unknown>>(r)
      setDetail((d ?? r) as Record<string, unknown>)
    } catch (e) { fail('读取反馈详情失败', e) } finally { setBusy('') }
  }

  const resolve = async (id: string) => {
    setBusy('resolve:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; data?: unknown }>(feedbackResolve(id), { resolution: '已在反馈中心处理' })
      if (r?.ok === false) { setErr('标记失败：' + (r.error || 'ok=false')); return }
      setMsg('已标记为已解决：' + id)
      await loadList()
    } catch (e) { fail('标记失败', e) } finally { setBusy('') }
  }

  const submit = async () => {
    setBusy('submit'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; success?: boolean }>(FEEDBACK_SUBMIT, {
        trace_id: subTrace.trim(),
        feedback_type: subType,
        rating: Number(subRating || 0),
        comment: subComment.trim(),
      })
      if (r?.ok === false || r?.success === false) { setErr('提交失败：' + (r?.error || '后端拒绝')); return }
      setMsg('反馈已提交')
      setSubComment('')
      await Promise.all([loadSummary(), loadList()])
    } catch (e) { fail('提交失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <MessageSquare size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">反馈中心</h2>
        <span className="text-[11px] text-slate-500">汇总 · 报告 · 优质案例 · 逐条受理 · 提交</span>
        <div className="ml-auto">
          <button
            type="button"
            className={BTN}
            aria-label="刷新反馈"
            disabled={loading}
            onClick={() => { void loadSummary(); void loadList(); if (tab === 'report') void loadReport(); if (tab === 'cases') void loadCases() }}
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-5">
        {[
          ['总反馈', summary?.total_feedback],
          ['好评', summary?.like_count],
          ['差评', summary?.dislike_count],
          ['满意度', (summary?.satisfaction_rate_percent ?? 0) + '%'],
          ['优质案例', summary?.quality_cases_count],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="font-mono text-[13px] text-cyan-300">{String(v ?? 0)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 flex items-center gap-1">
        {([['list', '反馈列表'], ['report', '分析报告'], ['cases', '优质案例'], ['submit', '提交反馈']] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-selected={tab === id}
            className={'rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
              tab === id ? 'bg-cyan-500/15 font-medium text-cyan-300' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'list' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <select className={INPUT} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} aria-label="反馈状态">
              <option value="">全部状态</option>
              <option value="open">open</option>
              <option value="resolved">resolved</option>
              <option value="ignored">ignored</option>
            </select>
            <select className={INPUT} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="反馈类型">
              <option value="">全部类型</option>
              <option value="like">like</option>
              <option value="dislike">dislike</option>
              <option value="report">report</option>
              <option value="suggestion">suggestion</option>
            </select>
            <button type="button" className={BTN_EM} onClick={() => void loadList()} disabled={loading} aria-label="按条件查询反馈">
              {busy === 'list' ? <Loader2 size={11} className="animate-spin" /> : null} 查询
            </button>
            <span className="text-[10px] text-slate-600">共 {total} 条（最多显示 50）</span>
          </div>

          <div className="flex flex-col gap-1">
            {rows.map((r, i) => {
              const id = r.feedback_id ?? r.id ?? ''
              return (
                <div key={id + i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-slate-800 px-1.5 text-[9px] text-slate-400">{r.feedback_type ?? '?'}</span>
                    <span className="text-slate-300">{r.category ?? ''}</span>
                    <span className={'text-[10px] ' + (r.status === 'resolved' ? 'text-emerald-400' : 'text-amber-400')}>{r.status ?? ''}</span>
                    <span className="font-mono text-[10px] text-slate-600">{r.created_at_iso ?? ''}</span>
                    <span className="ml-auto flex items-center gap-1">
                      <button type="button" className={BTN} onClick={() => void openDetail(id)} disabled={loading} aria-label={'查看反馈 ' + id}>详情</button>
                      <button type="button" className={BTN_EM} onClick={() => void resolve(id)} disabled={loading || r.status === 'resolved'} aria-label={'解决反馈 ' + id}>
                        {busy === 'resolve:' + id ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />} 标记已解决
                      </button>
                    </span>
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-slate-400">{r.comment ?? ''}</div>
                </div>
              )
            })}
            {rows.length === 0 && <div className="text-[11px] text-slate-500">没有符合条件的反馈。</div>}
          </div>

          {detail && (
            <div className="mt-2">
              <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
                <FileText size={11} className="text-cyan-400" /> 详情
                <button type="button" className={BTN} onClick={() => setDetail(null)}>关闭</button>
              </div>
              <pre className={PRE}>{JSON.stringify(detail, null, 2)}</pre>
            </div>
          )}
        </div>
      )}

      {tab === 'report' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
            <FileText size={11} className="text-cyan-400" /> 近 7 天分析报告
          </div>
          {report ? <pre className={PRE + ' max-h-[28rem]'}>{JSON.stringify(report, null, 2)}</pre>
            : <div className="text-[11px] text-slate-500">加载中…</div>}
        </div>
      )}

      {tab === 'cases' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
            <Star size={11} className="text-amber-400" /> 优质案例（{cases.length}）
          </div>
          <div className="flex flex-col gap-1">
            {cases.map((c, i) => (
              <div key={i} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-300">{String(c.title ?? c.case_id ?? '案例')}</span>
                  <span className="ml-auto font-mono text-[10px] text-amber-300">score={String(c.quality_score ?? '-')}</span>
                </div>
                <div className="line-clamp-2 text-slate-400">{String(c.content_summary ?? '')}</div>
              </div>
            ))}
            {cases.length === 0 && <div className="text-[11px] text-slate-500">还没有优质案例（正反馈达到阈值后自动沉淀）。</div>}
          </div>
        </div>
      )}

      {tab === 'submit' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
            <Send size={11} className="text-cyan-400" /> 提交一条反馈（需要 trace_id 对回具体一次调用）
          </div>
          <label className="mb-2 block text-[11px] text-slate-400">
            trace_id
            <input className={INPUT + ' mt-1 w-full'} value={subTrace} onChange={(e) => setSubTrace(e.target.value)} aria-label="反馈 trace_id" />
          </label>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-[11px] text-slate-400">
              类型
              <select className={INPUT} value={subType} onChange={(e) => setSubType(e.target.value)} aria-label="反馈类型选择">
                <option value="like">like</option>
                <option value="dislike">dislike</option>
                <option value="report">report</option>
                <option value="suggestion">suggestion</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-[11px] text-slate-400">
              评分(0-5)
              <input className={INPUT + ' w-16'} value={subRating} onChange={(e) => setSubRating(e.target.value)} aria-label="反馈评分" />
            </label>
          </div>
          <label className="mb-2 block text-[11px] text-slate-400">
            备注
            <textarea className={INPUT + ' mt-1 h-20 w-full'} value={subComment} onChange={(e) => setSubComment(e.target.value)} aria-label="反馈备注" />
          </label>
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void submit()}
            disabled={loading || subTrace.trim() === ''}
            title={subTrace.trim() === '' ? '先填 trace_id' : '提交'}
            aria-label="提交新反馈"
          >
            {busy === 'submit' ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />} 提交
          </button>
          <p className="mt-2 flex items-center gap-1 text-[10px] text-slate-600">
            <BookOpen size={10} /> 后端要求 trace_id 非空；空值会被 400 拒绝，故此处先校验。
          </p>
        </div>
      )}
    </div>
  )
}
