/**
 * 记忆运维 —— 审查 / 压缩 / 长期摘要 / 窗口监控 / 向量维护
 * ------------------------------------------------------------------
 * 数据源（全部 /api/memory/* 与 /api/vector/*）：
 *   审查      GET/POST review         体检统计 + 触发一次审查
 *   压缩      POST compress           触发记忆压缩
 *   长期摘要  PUT summary / POST clear-summary
 *   窗口监控  GET/POST windows/config + current|stats|events + POST windows/clear
 *   向量维护  GET recent / GET item/<id> / POST add + batch_add / DELETE clear
 *
 * 【为什么这个面板反复"先预览再确认"】压缩会改写长期摘要的来源数据、
 * clear-summary 与 vector/clear 是**不可撤销**的整体清空。
 * 这些动作在页面上各占一行、彼此相邻，靠"点了再说"迟早会点错。
 *
 * 【长期摘要为什么写之前要勾确认】后端只有 PUT（写）没有 GET（读）——
 * 页面拿不到当前摘要，编辑框里是什么就覆盖什么。这是**盲写**，必须显式告知。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Activity, AlertTriangle, Brain, Eraser, Gauge, Loader2, RefreshCw, Save, ScanLine, Trash2,
} from 'lucide-react'
import { ErrorBox, hubDelete, hubGet, hubPost, hubPut } from '../components/ui'
import {
  MEMORY_CLEAR_SUMMARY,
  MEMORY_COMPRESS,
  MEMORY_REVIEW,
  MEMORY_SUMMARY,
  MEMORY_WINDOWS_CLEAR,
  MEMORY_WINDOWS_CONFIG,
  MEMORY_WINDOWS_CURRENT,
  MEMORY_WINDOWS_EVENTS,
  MEMORY_WINDOWS_STATS,
  VECTOR_ADD,
  VECTOR_BATCH_ADD,
  VECTOR_CLEAR,
  VECTOR_RECENT,
  vectorItem,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface ReviewStats {
  total_entries?: number
  verified_entries?: number
  unverified_entries?: number
  sensitive_entries?: number
  high_importance_entries?: number
  embedding_entries?: number
  vec_knn_enabled?: boolean
  vec_table?: string
}

interface ReviewResp { last_review?: string | number | null; stats?: ReviewStats; ok?: boolean; error?: string }

interface WindowCurrent { process?: string | null; title?: string | null; elapsed_sec?: number; is_idle?: boolean }
interface WindowStat { app?: string; duration_sec?: number; switches?: number; [k: string]: unknown }
interface WindowEvent { ts?: number | string; from_app?: string; to_app?: string; [k: string]: unknown }
interface WindowConfig {
  enabled?: boolean
  idle_timeout_sec?: number
  ignore_processes?: string[]
  max_events?: number
  poll_interval_sec?: number
}

interface VectorItem {
  id?: string
  content?: string
  timestamp?: string
  metadata?: Record<string, unknown>
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
      <span className="min-w-28">{label}</span>
      {children}
    </label>
  )
}

export default function MemoryMaintenance() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [review, setReview] = useState<ReviewResp | null>(null)
  const [summary, setSummary] = useState('')
  const [confirmSummary, setConfirmSummary] = useState(false)
  const [confirmClearSummary, setConfirmClearSummary] = useState(false)

  const [current, setCurrent] = useState<WindowCurrent | null>(null)
  const [winStats, setWinStats] = useState<WindowStat[]>([])
  const [winTotal, setWinTotal] = useState<{ switches?: number; duration?: number }>({})
  const [events, setEvents] = useState<WindowEvent[]>([])
  const [config, setConfig] = useState<WindowConfig>({})
  const [ignoreText, setIgnoreText] = useState('')
  const [confirmClearWindows, setConfirmClearWindows] = useState(false)

  const [recent, setRecent] = useState<VectorItem[]>([])
  const [itemId, setItemId] = useState('')
  const [item, setItem] = useState<VectorItem | null>(null)
  const [addContent, setAddContent] = useState('')
  const [batchText, setBatchText] = useState('')
  const [confirmClearVector, setConfirmClearVector] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadReview = useCallback(async () => {
    setBusy('review'); setErr('')
    try {
      const r = await hubGet<ReviewResp>(MEMORY_REVIEW)
      if (r?.ok === false) { bad('读取记忆审查失败', r); return }
      setReview(r)
    } catch (e) { fail('读取记忆审查失败', e) } finally { setBusy('') }
  }, [])

  const triggerReview = async () => {
    setBusy('review-run'); setErr(''); setMsg('')
    try {
      const r = await hubPost<ReviewResp>(MEMORY_REVIEW, {})
      if (r?.ok === false) { bad('触发审查失败', r); return }
      setMsg('审查已执行')
      await loadReview()
    } catch (e) { fail('触发审查失败', e) } finally { setBusy('') }
  }

  const compress = async () => {
    setBusy('compress'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(MEMORY_COMPRESS, {})
      if (r?.ok === false) { bad('压缩失败', r); return }
      setMsg('已触发记忆压缩')
      await loadReview()
    } catch (e) { fail('压缩失败', e) } finally { setBusy('') }
  }

  const saveSummary = async () => {
    setBusy('summary'); setErr(''); setMsg('')
    try {
      const r = await hubPut<{ ok?: boolean; error?: string }>(MEMORY_SUMMARY, { summary })
      if (r?.ok === false) { bad('保存摘要失败', r); return }
      setMsg('长期摘要已覆盖写入')
      setConfirmSummary(false)
    } catch (e) { fail('保存摘要失败', e) } finally { setBusy('') }
  }

  const clearSummary = async () => {
    setBusy('clear-summary'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(MEMORY_CLEAR_SUMMARY, {})
      if (r?.ok === false) { bad('清空摘要失败', r); return }
      setMsg('长期摘要已清空')
      setConfirmClearSummary(false)
      setSummary('')
    } catch (e) { fail('清空摘要失败', e) } finally { setBusy('') }
  }

  const loadWindows = useCallback(async () => {
    setBusy('windows'); setErr('')
    try {
      const [cur, st, ev, cfg] = await Promise.all([
        hubGet<WindowCurrent>(MEMORY_WINDOWS_CURRENT),
        hubGet<{ apps?: WindowStat[]; total_switches?: number; total_duration_sec?: number }>(MEMORY_WINDOWS_STATS),
        hubGet<{ events?: WindowEvent[] }>(MEMORY_WINDOWS_EVENTS + '?limit=20'),
        hubGet<WindowConfig>(MEMORY_WINDOWS_CONFIG),
      ])
      setCurrent(cur)
      setWinStats(st?.apps ?? [])
      setWinTotal({ switches: st?.total_switches ?? 0, duration: st?.total_duration_sec ?? 0 })
      setEvents(ev?.events ?? [])
      setConfig(cfg ?? {})
      setIgnoreText((cfg?.ignore_processes ?? []).join(', '))
    } catch (e) { fail('读取窗口监控失败', e) } finally { setBusy('') }
  }, [])

  const saveWindowConfig = async () => {
    setBusy('win-config'); setErr(''); setMsg('')
    try {
      const body: WindowConfig = {
        enabled: config.enabled === true,
        idle_timeout_sec: Number(config.idle_timeout_sec ?? 300),
        max_events: Number(config.max_events ?? 500),
        poll_interval_sec: Number(config.poll_interval_sec ?? 0.5),
        ignore_processes: ignoreText.split(',').map((s) => s.trim()).filter(Boolean),
      }
      const r = await hubPost<{ ok?: boolean; error?: string }>(MEMORY_WINDOWS_CONFIG, body)
      if (r?.ok === false) { bad('保存窗口配置失败', r); return }
      setMsg('窗口监控配置已保存')
      await loadWindows()
    } catch (e) { fail('保存窗口配置失败', e) } finally { setBusy('') }
  }

  const clearWindows = async () => {
    setBusy('win-clear'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(MEMORY_WINDOWS_CLEAR, {})
      if (r?.ok === false) { bad('清空窗口事件失败', r); return }
      setMsg('窗口事件已清空')
      setConfirmClearWindows(false)
      await loadWindows()
    } catch (e) { fail('清空窗口事件失败', e) } finally { setBusy('') }
  }

  const loadRecent = useCallback(async () => {
    setBusy('recent'); setErr('')
    try {
      const r = await hubGet<{ items?: VectorItem[]; available?: boolean; error?: string }>(VECTOR_RECENT + '?limit=20')
      if (r?.available === false) { setErr('向量系统未初始化（后端返回 available=false）'); return }
      setRecent(r?.items ?? [])
    } catch (e) { fail('读取最近向量记忆失败', e) } finally { setBusy('') }
  }, [])

  const loadItem = async () => {
    if (!itemId.trim()) return
    setBusy('item'); setErr(''); setMsg(''); setItem(null)
    try {
      const r = await hubGet<VectorItem>(vectorItem(itemId.trim()))
      setItem(r)
    } catch (e) { fail('按 id 读取失败（不存在时后端 404）', e) } finally { setBusy('') }
  }

  const addVector = async () => {
    setBusy('vector-add'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; item_id?: string }>(VECTOR_ADD, { content: addContent.trim() })
      if (r?.ok === false) { bad('添加失败', r); return }
      setMsg('已添加：' + (r?.item_id ?? ''))
      setAddContent('')
      await loadRecent()
    } catch (e) { fail('添加失败', e) } finally { setBusy('') }
  }

  const batchAdd = async () => {
    setBusy('vector-batch'); setErr(''); setMsg('')
    let parsed: unknown
    try {
      parsed = JSON.parse(batchText || '[]')
    } catch {
      setErr('批量内容不是合法 JSON —— 没有提交')
      return
    }
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; count?: number }>(VECTOR_BATCH_ADD, { items: parsed })
      if (r?.ok === false) { bad('批量添加失败', r); return }
      setMsg('已批量添加 ' + (r?.count ?? 0) + ' 条')
      setBatchText('')
      await loadRecent()
    } catch (e) { fail('批量添加失败', e) } finally { setBusy('') }
  }

  const clearVector = async () => {
    setBusy('vector-clear'); setErr(''); setMsg('')
    try {
      const r = await hubDelete<{ ok?: boolean; error?: string }>(VECTOR_CLEAR)
      if (r?.ok === false) { bad('清空向量库失败', r); return }
      setMsg('向量记忆已清空')
      setConfirmClearVector(false)
      await loadRecent()
    } catch (e) { fail('清空向量库失败', e) } finally { setBusy('') }
  }

  useEffect(() => {
    void loadReview(); void loadWindows(); void loadRecent()
  }, [loadReview, loadWindows, loadRecent])

  const loading = busy !== ''
  const stats = review?.stats ?? {}

  return (
    <div className="space-y-3">
      {err && <ErrorBox message={err} />}
      {msg && <div className="rounded-md border border-emerald-800/60 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300">{msg}</div>}

      {/* 审查 / 压缩 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <ScanLine size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">记忆审查</h3>
          <span className="text-[10px] text-slate-500">只读体检；压缩会改写来源数据</span>
          <div className="ml-auto flex items-center gap-1.5">
            <button type="button" className={BTN} onClick={() => void loadReview()} disabled={loading} aria-label="刷新记忆审查">
              {busy === 'review' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
            </button>
            <button type="button" className={BTN} onClick={() => void triggerReview()} disabled={loading} aria-label="执行记忆审查">
              {busy === 'review-run' ? <Loader2 size={11} className="animate-spin" /> : <ScanLine size={11} />} 执行审查
            </button>
            <button type="button" className={BTN_EM} onClick={() => void compress()} disabled={loading} aria-label="触发记忆压缩">
              {busy === 'compress' ? <Loader2 size={11} className="animate-spin" /> : <Gauge size={11} />} 触发压缩
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {[
            ['总条目', stats.total_entries],
            ['已验证', stats.verified_entries],
            ['未验证', stats.unverified_entries],
            ['敏感', stats.sensitive_entries],
            ['高重要度', stats.high_importance_entries],
            ['向量条目', stats.embedding_entries],
            ['最近审查', review?.last_review ?? '—'],
            ['向量表', stats.vec_table ?? '—'],
          ].map(([k, v]) => (
            <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
              <div className="text-[10px] text-slate-500">{String(k)}</div>
              <div className="truncate font-mono text-[12px] text-cyan-300">{String(v ?? 0)}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 长期摘要 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <Save size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">长期摘要</h3>
          <span className="flex items-center gap-1 text-[10px] text-amber-400">
            <AlertTriangle size={10} /> 后端没有读接口（只有 PUT）—— 这里是盲写，会覆盖现有摘要
          </span>
        </div>
        <textarea
          className={INPUT + ' h-24 w-full'}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          aria-label="长期摘要内容"
          placeholder="写入新的长期摘要（会整体覆盖）"
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmSummary} onChange={(e) => setConfirmSummary(e.target.checked)} aria-label="确认覆盖摘要" />
            我确认覆盖现有摘要
          </label>
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void saveSummary()}
            disabled={loading || !confirmSummary || summary.trim() === ''}
            aria-label="保存长期摘要"
          >
            {busy === 'summary' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 保存摘要
          </button>
          <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmClearSummary} onChange={(e) => setConfirmClearSummary(e.target.checked)} aria-label="确认清空摘要" />
            我确认清空摘要
          </label>
          <button type="button" className={BTN_RED} onClick={() => void clearSummary()} disabled={loading || !confirmClearSummary} aria-label="清空长期摘要">
            清空摘要
          </button>
        </div>
      </div>

      {/* 窗口监控 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <Activity size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">窗口监控</h3>
          <div className="ml-auto">
            <button type="button" className={BTN} onClick={() => void loadWindows()} disabled={loading} aria-label="刷新窗口监控">
              {busy === 'windows' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
            </button>
          </div>
        </div>

        <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          <div className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">当前窗口</div>
            <div className="truncate text-[11px] text-cyan-300">{current?.title ?? (current?.is_idle ? '（空闲）' : '—')}</div>
            <div className="truncate text-[10px] text-slate-500">{current?.process ?? ''}</div>
          </div>
          <div className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">停留</div>
            <div className="font-mono text-[12px] text-cyan-300">{Math.round(current?.elapsed_sec ?? 0)}s</div>
          </div>
          <div className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">总切换</div>
            <div className="font-mono text-[12px] text-cyan-300">{winTotal.switches ?? 0}</div>
          </div>
          <div className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">总时长</div>
            <div className="font-mono text-[12px] text-cyan-300">{Math.round(winTotal.duration ?? 0)}s</div>
          </div>
        </div>

        {winStats.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1 font-mono text-[10px] text-slate-400">
            {winStats.slice(0, 12).map((s, i) => (
              <span key={i} className="rounded-sm bg-slate-800/60 px-1.5 py-0.5">
                {String(s.app ?? s.name ?? '?')} {Math.round(Number(s.duration_sec ?? s.total_sec ?? 0))}s
              </span>
            ))}
          </div>
        )}

        <Row label="监控开关">
          <input
            type="checkbox"
            checked={config.enabled === true}
            onChange={(e) => setConfig((c) => ({ ...c, enabled: e.target.checked }))}
            aria-label="窗口监控开关"
          />
        </Row>
        <Row label="空闲阈值(s)">
          <input className={INPUT + ' w-20'} value={String(config.idle_timeout_sec ?? '')} aria-label="空闲阈值秒"
            onChange={(e) => setConfig((c) => ({ ...c, idle_timeout_sec: Number(e.target.value) }))} />
        </Row>
        <Row label="最大事件数">
          <input className={INPUT + ' w-24'} value={String(config.max_events ?? '')} aria-label="窗口最大事件数"
            onChange={(e) => setConfig((c) => ({ ...c, max_events: Number(e.target.value) }))} />
        </Row>
        <Row label="轮询间隔(s)">
          <input className={INPUT + ' w-20'} value={String(config.poll_interval_sec ?? '')} aria-label="窗口轮询间隔"
            onChange={(e) => setConfig((c) => ({ ...c, poll_interval_sec: Number(e.target.value) }))} />
        </Row>
        <Row label="忽略进程">
          <input className={INPUT + ' w-72'} value={ignoreText} onChange={(e) => setIgnoreText(e.target.value)} placeholder="逗号分隔" aria-label="忽略进程列表" />
        </Row>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <button type="button" className={BTN_EM} onClick={() => void saveWindowConfig()} disabled={loading} aria-label="保存窗口配置">
            {busy === 'win-config' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 保存配置
          </button>
          <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmClearWindows} onChange={(e) => setConfirmClearWindows(e.target.checked)} aria-label="确认清空窗口事件" />
            我确认清空事件
          </label>
          <button type="button" className={BTN_RED} onClick={() => void clearWindows()} disabled={loading || !confirmClearWindows} aria-label="清空窗口事件">
            <Eraser size={11} /> 清空事件
          </button>
        </div>

        {events.length > 0 && (
          <pre className={PRE + ' mt-2'}>{JSON.stringify(events.slice(0, 8), null, 2)}</pre>
        )}
      </div>

      {/* 向量维护 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <Brain size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">向量维护</h3>
          <div className="ml-auto">
            <button type="button" className={BTN} onClick={() => void loadRecent()} disabled={loading} aria-label="刷新最近向量记忆">
              {busy === 'recent' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
            </button>
          </div>
        </div>

        <div className="mb-2 flex flex-col gap-1">
          {recent.slice(0, 8).map((it, i) => (
            <div key={(it.id ?? '') + i} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[10px] text-cyan-300">{it.id ?? ''}</span>
                <span className="font-mono text-[10px] text-slate-600">{it.timestamp ?? ''}</span>
                <span className="ml-auto text-[10px] text-slate-500">{String(it.metadata?.type ?? '')}</span>
              </div>
              <div className="line-clamp-2 text-slate-400">{it.content ?? ''}</div>
            </div>
          ))}
          {recent.length === 0 && <div className="text-[11px] text-slate-500">没有取到向量记忆。</div>}
        </div>

        <Row label="按 id 查看">
          <input className={INPUT + ' w-64'} value={itemId} onChange={(e) => setItemId(e.target.value)} placeholder="mem_2026..." aria-label="向量记忆 id" />
          <button type="button" className={BTN} onClick={() => void loadItem()} disabled={loading || itemId.trim() === ''} aria-label="按 id 读取向量记忆">
            {busy === 'item' ? <Loader2 size={11} className="animate-spin" /> : null} 读取
          </button>
        </Row>
        {item && <pre className={PRE}>{JSON.stringify(item, null, 2)}</pre>}

        <Row label="新增一条">
          <input className={INPUT + ' w-80'} value={addContent} onChange={(e) => setAddContent(e.target.value)} placeholder="要记住的内容" aria-label="新增向量记忆内容" />
          <button type="button" className={BTN_EM} onClick={() => void addVector()} disabled={loading || addContent.trim() === ''} aria-label="新增向量记忆">
            {busy === 'vector-add' ? <Loader2 size={11} className="animate-spin" /> : null} 添加
          </button>
        </Row>

        <Row label="批量添加">
          <textarea
            className={INPUT + ' h-20 w-full font-mono'}
            value={batchText}
            onChange={(e) => setBatchText(e.target.value)}
            placeholder='[{"content": "..."}, {"content": "...", "metadata": {}}]'
            aria-label="批量向量记忆 JSON"
          />
        </Row>
        <div className="mb-2">
          <button type="button" className={BTN_EM} onClick={() => void batchAdd()} disabled={loading || batchText.trim() === ''} aria-label="批量添加向量记忆">
            {busy === 'vector-batch' ? <Loader2 size={11} className="animate-spin" /> : null} 批量添加
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 rounded-sm border border-red-900/40 bg-red-500/5 p-2">
          <span className="text-[10px] text-red-300">清空向量库不可撤销（整库删除）。</span>
          <label className="flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmClearVector} onChange={(e) => setConfirmClearVector(e.target.checked)} aria-label="确认清空向量库" />
            我确认清空
          </label>
          <button type="button" className={BTN_RED} onClick={() => void clearVector()} disabled={loading || !confirmClearVector} aria-label="清空向量库">
            <Trash2 size={11} /> 清空向量库
          </button>
        </div>
      </div>
    </div>
  )
}
