/**
 * 回放中心 —— 会话回放的列表 / 详情 / 数据 / 清理 / 关联统计（5 条）
 * ------------------------------------------------------------------
 *   GET  /api/replay/list            查询回放列表（分页/过滤）
 *   GET  /api/replay/<id>            单条回放元数据
 *   GET  /api/replay/<id>/data       解码后的回放数据（JSON 字符串）
 *   GET  /api/replay/stats           与错误/追踪/会话的关联统计
 *   POST /api/replay/cleanup         清理过期回放（可先 dry_run）
 *
 * 【为什么这里强调"先预览再清理"】清理是按保留期**物理删除**回放数据；
 * 而回放正是排障时唯一的现场。默认 dry_run=true，看清会删多少再决定。
 *
 * 【为什么详情与数据分两步拉】列表只带元数据（体积小、翻页快）；
 * 回放数据可能很大，必须点开某一条才拉，且要能单独失败而不影响列表。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Film, Loader2, RefreshCw, Search, Trash2,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  REPLAY_CLEANUP,
  REPLAY_LIST,
  REPLAY_STATS,
  replayById,
  replayData,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-64 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface ReplayRow {
  replay_id?: string
  id?: string
  created_at?: string
  size_bytes?: number
  url?: string
  user_session_id?: string
  error_id?: string
  trace_id?: string
  [k: string]: unknown
}

export default function ReplayCenter() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [rows, setRows] = useState<ReplayRow[]>([])
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState('20')
  const [stats, setStats] = useState<Record<string, unknown> | null>(null)

  const [meta, setMeta] = useState<Record<string, unknown> | null>(null)
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [activeId, setActiveId] = useState('')

  const [confirmCleanup, setConfirmCleanup] = useState(false)
  const [cleanupResult, setCleanupResult] = useState<Record<string, unknown> | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async (lim = limit) => {
    setBusy('load'); setErr('')
    try {
      const [l, s] = await Promise.allSettled([
        hubGet<{ records?: ReplayRow[]; total?: number }>(REPLAY_LIST + '?limit=' + encodeURIComponent(lim)),
        hubGet<{ stats?: Record<string, unknown> }>(REPLAY_STATS),
      ])
      if (l.status === 'fulfilled') {
        setRows(l.value?.records ?? [])
        setTotal(l.value?.total ?? (l.value?.records ?? []).length)
      }
      if (s.status === 'fulfilled') setStats(s.value?.stats ?? null)
    } catch (e) { fail('读取回放列表失败', e) } finally { setBusy('') }
  }, [limit])

  useEffect(() => { void load() }, [load])

  const openMeta = async (id: string) => {
    setBusy('meta'); setErr(''); setMeta(null); setData(null); setActiveId(id)
    try {
      const r = await hubGet<Record<string, unknown>>(replayById(id))
      setMeta(r)
    } catch (e) { fail('读取回放元数据失败', e) } finally { setBusy('') }
  }

  const openData = async (id: string) => {
    setBusy('data'); setErr(''); setData(null); setActiveId(id)
    try {
      const r = await hubGet<Record<string, unknown>>(replayData(id))
      setData(r)
    } catch (e) { fail('读取回放数据失败', e) } finally { setBusy('') }
  }

  const cleanup = async (dryRun: boolean) => {
    setBusy('cleanup'); setErr(''); setMsg(''); setCleanupResult(null)
    try {
      const r = await hubPost<Record<string, unknown>>(REPLAY_CLEANUP, { dry_run: dryRun })
      setCleanupResult(r)
      const removed = (r?.deleted ?? r?.removed ?? r?.cleaned) as unknown
      setMsg(dryRun ? '预览完成（未删除）' : '已清理过期回放' + (Array.isArray(removed) ? '：' + removed.length + ' 条' : ''))
      if (!dryRun) { setConfirmCleanup(false); await load() }
    } catch (e) { fail('清理失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Film size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">回放中心</h2>
        <span className="text-[11px] text-slate-500">会话回放：排障时唯一的现场 —— 清理前必须先看会删多少</span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <select className={INPUT} value={limit} onChange={(e) => { setLimit(e.target.value); void load(e.target.value) }} aria-label="回放条数">
            <option value="20">20 条</option>
            <option value="50">50 条</option>
            <option value="100">100 条</option>
          </select>
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新回放列表">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['回放总数', total],
          ['窗口时长', (stats?.window_hours as number) ?? 24],
          ['完全关联', (stats?.fully_correlated as number) ?? 0],
          ['带 trace', (stats?.with_trace_id as number) ?? 0],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="font-mono text-[13px] text-cyan-300">{String(v ?? 0)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          <Search size={11} className="text-cyan-400" /> 回放列表（{rows.length}/{total}）
        </div>
        <div className="flex flex-col gap-1">
          {rows.map((r) => {
            const id = (r.replay_id ?? r.id ?? '') as string
            return (
              <div key={id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                <span className="text-slate-300">{String(r.url ?? '')}</span>
                <span className="font-mono text-[10px] text-slate-600">{String(r.created_at ?? '')}</span>
                {r.size_bytes != null && <span className="text-[10px] text-slate-500">{Math.round(Number(r.size_bytes) / 1024)} KB</span>}
                <span className="ml-auto flex items-center gap-1">
                  <button type="button" className={BTN} onClick={() => void openMeta(id)} disabled={loading} aria-label={'回放元数据 ' + id}>元数据</button>
                  <button type="button" className={BTN} onClick={() => void openData(id)} disabled={loading} aria-label={'回放数据 ' + id}>数据</button>
                </span>
              </div>
            )
          })}
          {rows.length === 0 && <div className="text-[11px] text-slate-500">没有回放记录（采样率 VITE_REPLAY_SAMPLE_RATE 默认 1\%）。</div>}
        </div>
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-1 text-[11px] text-slate-400">关联统计（错误 / 追踪 / 会话）</div>
        <pre className={PRE}>{JSON.stringify(stats ?? {}, null, 2)}</pre>
      </div>

      <div className="mb-2 rounded-lg border border-red-900/40 bg-red-500/5 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-red-300">
          <Trash2 size={11} /> 清理过期回放（按保留期物理删除）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={BTN} onClick={() => void cleanup(true)} disabled={loading} aria-label="预览清理回放">
            {busy === 'cleanup' ? <Loader2 size={11} className="animate-spin" /> : null} 预览（dry_run）
          </button>
          <label className="flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmCleanup} onChange={(e) => setConfirmCleanup(e.target.checked)} aria-label="确认清理回放" />
            我确认删除过期回放
          </label>
          <button type="button" className={BTN_RED} onClick={() => void cleanup(false)} disabled={loading || !confirmCleanup} aria-label="清理回放">
            执行清理
          </button>
        </div>
        {cleanupResult && <pre className={PRE + ' mt-2'}>{JSON.stringify(cleanupResult, null, 2)}</pre>}
      </div>

      {(meta || data) && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            <Film size={11} className="text-cyan-400" /> {activeId}
            <button type="button" className={BTN + ' ml-auto'} onClick={() => { setMeta(null); setData(null); setActiveId('') }}>关闭</button>
          </div>
          {meta && (
            <>
              <div className="mb-1 text-[10px] text-slate-500">元数据</div>
              <pre className={PRE}>{JSON.stringify(meta, null, 2)}</pre>
            </>
          )}
          {data && (
            <>
              <div className="mt-2 mb-1 text-[10px] text-slate-500">回放数据</div>
              <pre className={PRE + ' max-h-80'}>{JSON.stringify(data, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}
