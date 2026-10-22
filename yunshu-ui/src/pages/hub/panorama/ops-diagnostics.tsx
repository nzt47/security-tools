/**
 * 诊断 + 可观测 —— 运行时自检与请求可观测面（11 条）
 * ------------------------------------------------------------------
 *   诊断    /api/diagnostics/config|health|logs|tools|error_correlation
 *   可观测  /api/observability/state|traces|traces/<id>|access_logs|access_stats
 *           /api/observability/alerts/<name>（PUT 改 / DELETE 删）
 *
 * 【为什么把"坏掉的端点"也接上面】/api/diagnostics/config 实测返回
 * {"error": "Config.get() missing 1 required positional argument: 'self'"} ——
 * 是个真后端缺陷。把它接上、把错误如实打在页面上，比让它继续藏在"未接线"
 * 里更有用：缺陷要么被修，要么被看见，不该靠不接线来保持体面。
 * （本 PR 里标注为待修，不在此修。）
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, Activity, FileText, Loader2, RefreshCw, Save, Trash2, Wrench,
} from 'lucide-react'
import { ErrorBox, hubDelete, hubGet, hubPut } from '../components/ui'
import {
  DIAGNOSTICS_CONFIG,
  DIAGNOSTICS_ERROR_CORRELATION,
  DIAGNOSTICS_HEALTH,
  DIAGNOSTICS_LOGS,
  DIAGNOSTICS_TOOLS,
  OBSERVABILITY_ACCESS_LOGS,
  OBSERVABILITY_ACCESS_STATS,
  OBSERVABILITY_STATE,
  OBSERVABILITY_TRACES,
  observabilityAlert,
  observabilityTrace,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-40 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface ToolRow { name?: string; category?: string; enabled?: boolean; description?: string }
interface TraceRow { trace_id?: string; name?: string; duration_ms?: number; status?: string; start_time?: string }
interface AccessRow {
  datetime?: string; method?: string; endpoint?: string; status_code?: number
  response_time_ms?: number; client_ip?: string; trace_id?: string
}

type Tab = 'diag' | 'observe'

export default function OpsDiagnostics() {
  const [tab, setTab] = useState<Tab>('diag')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  // 诊断
  const [diagConfig, setDiagConfig] = useState<Record<string, unknown> | null>(null)
  const [diagHealth, setDiagHealth] = useState<Record<string, unknown> | null>(null)
  const [diagLogs, setDiagLogs] = useState<Record<string, unknown>[]>([])
  const [diagCorr, setDiagCorr] = useState<Record<string, unknown> | null>(null)
  const [tools, setTools] = useState<ToolRow[]>([])
  const [toolCats, setToolCats] = useState<Record<string, number>>({})

  // 可观测
  const [accessStats, setAccessStats] = useState<Record<string, unknown> | null>(null)
  const [accessLogs, setAccessLogs] = useState<AccessRow[]>([])
  const [traces, setTraces] = useState<TraceRow[]>([])
  const [traceId, setTraceId] = useState('')
  const [traceDetail, setTraceDetail] = useState<Record<string, unknown> | null>(null)
  const [state, setState] = useState<Record<string, unknown> | null>(null)
  const [alertName, setAlertName] = useState('')
  const [alertBody, setAlertBody] = useState('{\n  "condition": "health.overall_score < 30",\n  "severity": "critical"\n}')
  const [confirmDeleteAlert, setConfirmDeleteAlert] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const loadDiag = useCallback(async () => {
    setBusy('diag'); setErr('')
    try {
      // config 端点当前后端 500/错误体：单独取，失败只影响这一格
      const [cfg, h, lg, corr, tl] = await Promise.allSettled([
        hubGet<Record<string, unknown>>(DIAGNOSTICS_CONFIG),
        hubGet<Record<string, unknown>>(DIAGNOSTICS_HEALTH),
        hubGet<{ logs?: Record<string, unknown>[] }>(DIAGNOSTICS_LOGS + '?limit=20'),
        hubGet<Record<string, unknown>>(DIAGNOSTICS_ERROR_CORRELATION),
        hubGet<{ tools?: ToolRow[]; categories?: Record<string, number> }>(DIAGNOSTICS_TOOLS),
      ])
      if (cfg.status === 'fulfilled') setDiagConfig(cfg.value); else setDiagConfig({ error: String(cfg.reason) })
      setDiagHealth(h.status === 'fulfilled' ? h.value : null)
      setDiagLogs(lg.status === 'fulfilled' ? (lg.value?.logs ?? []) : [])
      setDiagCorr(corr.status === 'fulfilled' ? corr.value : null)
      if (tl.status === 'fulfilled') {
        setTools(tl.value?.tools ?? [])
        setToolCats(tl.value?.categories ?? {})
      }
    } catch (e) { fail('读取诊断信息失败', e) } finally { setBusy('') }
  }, [])

  const loadObserve = useCallback(async () => {
    setBusy('observe'); setErr('')
    try {
      const [st, as, al, tr, obs] = await Promise.allSettled([
        hubGet<Record<string, unknown>>(OBSERVABILITY_STATE),
        hubGet<Record<string, unknown>>(OBSERVABILITY_ACCESS_STATS),
        hubGet<{ access_logs?: AccessRow[] }>(OBSERVABILITY_ACCESS_LOGS + '?limit=20'),
        hubGet<{ traces?: TraceRow[] }>(OBSERVABILITY_TRACES + '?limit=20'),
        hubGet<Record<string, unknown>>(OBSERVABILITY_STATE),
      ])
      if (st.status === 'fulfilled') setState(st.value)
      if (as.status === 'fulfilled') setAccessStats(as.value)
      setAccessLogs(al.status === 'fulfilled' ? (al.value?.access_logs ?? []) : [])
      setTraces(tr.status === 'fulfilled' ? (tr.value?.traces ?? []) : [])
      if (obs.status === 'rejected') setErr('可观测状态读取失败：' + String(obs.reason))
    } catch (e) { fail('读取可观测数据失败', e) } finally { setBusy('') }
  }, [])

  const loadTrace = async () => {
    if (!traceId.trim()) return
    setBusy('trace'); setErr(''); setTraceDetail(null)
    try {
      const r = await hubGet<Record<string, unknown>>(observabilityTrace(traceId.trim()))
      setTraceDetail(r)
    } catch (e) { fail('读取追踪详情失败（不存在时后端 404）', e) } finally { setBusy('') }
  }

  const saveAlert = async () => {
    if (!alertName.trim()) return
    let parsed: unknown
    try {
      parsed = JSON.parse(alertBody || '{}')
    } catch {
      setErr('告警规则不是合法 JSON —— 没有提交')
      return
    }
    setBusy('alert'); setErr(''); setMsg('')
    try {
      const r = await hubPut<{ ok?: boolean; error?: string }>(observabilityAlert(alertName.trim()), parsed)
      if (r?.ok === false) { setErr('保存告警规则失败：' + (r.error || 'ok=false')); return }
      setMsg('告警规则已保存：' + alertName.trim())
    } catch (e) { fail('保存告警规则失败', e) } finally { setBusy('') }
  }

  const deleteAlert = async () => {
    if (!alertName.trim()) return
    setBusy('alert-del'); setErr(''); setMsg('')
    try {
      const r = await hubDelete<{ ok?: boolean; error?: string }>(observabilityAlert(alertName.trim()))
      if (r?.ok === false) { setErr('删除告警规则失败：' + (r.error || 'ok=false')); return }
      setMsg('告警规则已删除：' + alertName.trim())
      setConfirmDeleteAlert(false)
    } catch (e) { fail('删除告警规则失败', e) } finally { setBusy('') }
  }

  useEffect(() => { void loadDiag(); void loadObserve() }, [loadDiag, loadObserve])

  const loading = busy !== ''

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Wrench size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">诊断 / 可观测</h3>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            className={BTN}
            aria-label="刷新诊断与可观测"
            disabled={loading}
            onClick={() => { if (tab === 'diag') void loadDiag(); else void loadObserve() }}
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      <div className="mb-2 flex items-center gap-1">
        {([['diag', '诊断'], ['observe', '可观测']] as const).map(([id, label]) => (
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

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      {tab === 'diag' && (
        <div className="space-y-2">
          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <AlertTriangle size={11} className="text-amber-400" /> 配置摘要
              {diagConfig && 'error' in diagConfig && (
                <span className="text-[10px] text-red-400">后端返回错误（已知缺陷，未在本轮修）</span>
              )}
            </div>
            <pre className={PRE}>{JSON.stringify(diagConfig ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Activity size={11} className="text-cyan-400" /> 综合健康检查
            </div>
            <pre className={PRE}>{JSON.stringify(diagHealth ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <AlertTriangle size={11} className="text-amber-400" /> 异常关联
            </div>
            <pre className={PRE}>{JSON.stringify(diagCorr ?? {}, null, 2)}</pre>
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Wrench size={11} className="text-cyan-400" /> 已注册工具（{tools.length}）
              <span className="text-[10px] text-slate-600">
                {Object.entries(toolCats).map(([k, v]) => k + '=' + v).join(' · ')}
              </span>
            </div>
            <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto">
              {tools.slice(0, 60).map((t, i) => (
                <span
                  key={(t.name ?? '') + i}
                  title={t.description}
                  className={'rounded-sm px-1.5 py-0.5 font-mono text-[10px] ' + (t.enabled ? 'bg-slate-800/70 text-slate-300' : 'bg-red-500/10 text-red-300')}
                >
                  {t.name ?? '?'}
                </span>
              ))}
              {tools.length === 0 && <span className="text-[10px] text-slate-600">—</span>}
            </div>
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <FileText size={11} className="text-cyan-400" /> 最近日志（{diagLogs.length}）
            </div>
            <pre className={PRE}>{JSON.stringify(diagLogs.slice(0, 5), null, 2)}</pre>
          </div>
        </div>
      )}

      {tab === 'observe' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {[
              ['总访问', (accessStats?.total_accesses as number) ?? 0],
              ['错误率', (((accessStats?.error_rate as number) ?? 0) * 100).toFixed(1) + '%'],
              ['平均耗时', Math.round((accessStats?.avg_response_time_ms as number) ?? 0) + 'ms'],
              ['独立端点', (accessStats?.unique_endpoints as number) ?? 0],
            ].map(([k, v]) => (
              <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
                <div className="text-[10px] text-slate-500">{String(k)}</div>
                <div className="font-mono text-[12px] text-cyan-300">{String(v)}</div>
              </div>
            ))}
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <FileText size={11} className="text-cyan-400" /> 访问日志（{accessLogs.length}）
            </div>
            <div className="max-h-52 overflow-y-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="text-[10px] text-slate-500">
                  <tr>
                    <th className="px-1 py-1">时间</th>
                    <th className="px-1 py-1">方法</th>
                    <th className="px-1 py-1">端点</th>
                    <th className="px-1 py-1">状态</th>
                    <th className="px-1 py-1">耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {accessLogs.map((r, i) => (
                    <tr key={i} className="border-t border-slate-800/60">
                      <td className="px-1 py-1 font-mono text-[10px] text-slate-500">{String(r.datetime ?? '').slice(0, 19)}</td>
                      <td className="px-1 py-1 text-slate-400">{r.method ?? ''}</td>
                      <td className="px-1 py-1 font-mono text-[10px] text-cyan-300">{r.endpoint ?? ''}</td>
                      <td className={'px-1 py-1 font-mono text-[10px] ' + ((r.status_code ?? 200) >= 400 ? 'text-red-400' : 'text-emerald-400')}>
                        {r.status_code ?? ''}
                      </td>
                      <td className="px-1 py-1 font-mono text-[10px] text-slate-500">{Math.round(r.response_time_ms ?? 0)}ms</td>
                    </tr>
                  ))}
                  {accessLogs.length === 0 && (
                    <tr><td colSpan={5} className="px-1 py-2 text-center text-[10px] text-slate-600">没有访问记录</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Activity size={11} className="text-cyan-400" /> 追踪（{traces.length}）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input className={INPUT + ' w-64'} value={traceId} onChange={(e) => setTraceId(e.target.value)} placeholder="trace_id" aria-label="追踪 id" />
              <button type="button" className={BTN} onClick={() => void loadTrace()} disabled={loading || traceId.trim() === ''} aria-label="读取追踪详情">
                {busy === 'trace' ? <Loader2 size={11} className="animate-spin" /> : null} 查看详情
              </button>
            </div>
            {traces.length > 0 && (
              <div className="mt-1 flex flex-col gap-1">
                {traces.slice(0, 8).map((t, i) => (
                  <button
                    key={(t.trace_id ?? '') + i}
                    type="button"
                    onClick={() => setTraceId(t.trace_id ?? '')}
                    className="flex items-center gap-2 rounded-sm border border-slate-800 bg-slate-900/40 px-2 py-1 text-left text-[11px] hover:bg-slate-800/50"
                  >
                    <span className="font-mono text-[10px] text-cyan-300">{t.trace_id ?? ''}</span>
                    <span className="text-slate-400">{t.name ?? ''}</span>
                    <span className="ml-auto font-mono text-[10px] text-slate-500">{Math.round(t.duration_ms ?? 0)}ms</span>
                  </button>
                ))}
              </div>
            )}
            {traceDetail && <pre className={PRE + ' mt-1'}>{JSON.stringify(traceDetail, null, 2)}</pre>}
          </div>

          <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Save size={11} className="text-cyan-400" /> 告警规则（PUT 覆盖 / DELETE 删除）
            </div>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <input className={INPUT + ' w-56'} value={alertName} onChange={(e) => setAlertName(e.target.value)} placeholder="规则名，如 YunshuHealthScoreCritical" aria-label="告警规则名" />
            </div>
            <textarea
              className={INPUT + ' h-20 w-full font-mono'}
              value={alertBody}
              onChange={(e) => setAlertBody(e.target.value)}
              aria-label="告警规则 JSON"
            />
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <button type="button" className={BTN_EM} onClick={() => void saveAlert()} disabled={loading || alertName.trim() === ''} aria-label="保存告警规则">
                {busy === 'alert' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 保存规则
              </button>
              <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmDeleteAlert} onChange={(e) => setConfirmDeleteAlert(e.target.checked)} aria-label="确认删除告警规则" />
                我确认删除该规则
              </label>
              <button type="button" className={BTN_RED} onClick={() => void deleteAlert()} disabled={loading || !confirmDeleteAlert || alertName.trim() === ''} aria-label="删除告警规则">
                <Trash2 size={11} /> 删除规则
              </button>
            </div>
          </div>

          {state && (
            <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
              <div className="mb-1 text-[11px] text-slate-400">可观测总状态</div>
              <pre className={PRE}>{JSON.stringify(state, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
