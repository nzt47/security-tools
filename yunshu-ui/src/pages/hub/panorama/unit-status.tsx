/**
 * 功能单位工作状况 —— 「每个能力单位有没有在工作、最近一次动过没、有没有红」的统一视图。
 * ================================================================
 * 数据来自 GET /api/system/units（后端 unit_monitor 聚合 12 个探测单位：服务进程 / 审计链 /
 * 技能资产 / 技能使用 / 工具 / 工作流 / 调度 / 记忆压缩 / 长期记忆 / 反思经验 / 向量 / 吞噬草稿）。
 *
 * 【口径】后端只归一只读快照，前端不重算：状态五档 ok/warn/off/never/error 直接上屏；
 * 每个单位的 issues 逐条显示（例如「摘要 v88 是 0 字节」「长期记忆 0 行」），
 * 这样「功能在跑但没产出」这类问题不再被一屏绿色掩盖。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { Badge, ErrorBox, hubGet } from '../components/ui'
import { SYSTEM_UNITS } from '@/api/endpoints'

type UnitStatus = 'ok' | 'warn' | 'off' | 'never' | 'error'

interface UnitRow {
  id: string
  name: string
  family: string
  description?: string
  status: UnitStatus
  summary?: string
  metrics?: Record<string, unknown>
  issues?: string[]
  last_activity?: string | null
  probe_ms?: number
}

interface UnitsResp {
  ok?: boolean
  units?: UnitRow[]
  summary?: Record<string, number>
  health_score?: number
  generated_at?: string
  cached?: boolean
  error?: string
}

const STATUS_META: Record<UnitStatus, { label: string; badge: string; dot: string }> = {
  ok: { label: '正常', badge: 'green', dot: 'bg-emerald-400' },
  warn: { label: '需注意', badge: 'amber', dot: 'bg-amber-400' },
  off: { label: '已关闭', badge: 'slate', dot: 'bg-slate-500' },
  never: { label: '从未运行', badge: 'cyan', dot: 'bg-cyan-400' },
  error: { label: '异常', badge: 'red', dot: 'bg-red-400' },
}

const FAMILY_LABEL: Record<string, string> = {
  runtime: '运行时',
  skills: '技能',
  tools: '工具',
  workflows: '工作流',
  scheduler: '调度',
  memory: '记忆',
  ingestion: '吞噬',
}

const STATUS_ORDER: UnitStatus[] = ['ok', 'warn', 'off', 'never', 'error']

function fmtTime(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('zh-CN', { hour12: false })
}

function metricLine(metrics?: Record<string, unknown>) {
  if (!metrics) return ''
  return Object.entries(metrics)
    .slice(0, 6)
    .map(([k, v]) => k + '=' + (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)))
    .join(' · ')
}

export default function UnitStatus() {
  const [data, setData] = useState<UnitsResp | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [family, setFamily] = useState('')

  const load = useCallback(async (refresh = false) => {
    setBusy(true)
    setErr('')
    try {
      const url = SYSTEM_UNITS + (refresh ? '?refresh=1' : '')
      const r = await hubGet<UnitsResp>(url)
      if (r?.ok === false) setErr(r.error || '后端返回 ok=false')
      else setData(r)
    } catch (e) {
      setErr('功能单位快照加载失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => { void load(false) }, [load])
  useEffect(() => {
    const t = setInterval(() => { void load(false) }, 30000)
    return () => clearInterval(t)
  }, [load])

  const families = useMemo(() => Array.from(new Set((data?.units ?? []).map((u) => u.family))).sort(), [data])
  const units = useMemo(
    () => (data?.units ?? []).filter((u) => !family || u.family === family),
    [data, family],
  )
  const summary = data?.summary ?? {}

  return (
    <div className="mb-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4" data-testid="unit-status">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Activity size={14} className="text-cyan-400" />
        <span className="text-sm font-medium text-slate-200">功能单位工作状况</span>
        {data?.health_score != null && (
          <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
            健康分 {data.health_score}
          </span>
        )}
        {STATUS_ORDER.map((s) => (
          <span key={s} className="inline-flex items-center gap-1 text-[11px] text-slate-400">
            <span className={'inline-block h-1.5 w-1.5 rounded-full ' + STATUS_META[s].dot} />
            {STATUS_META[s].label} {summary[s] ?? 0}
          </span>
        ))}
        <span className="ml-auto">
          <button type="button" onClick={() => void load(true)} disabled={busy}
            className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-50">
            <RefreshCw size={11} className={busy ? 'animate-spin' : ''} /> 重新探测</button>
        </span>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-1">
        <button type="button" onClick={() => setFamily('')}
          className={'rounded-md border px-2 py-0.5 text-[11px] ' + (family === '' ? 'border-cyan-700 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:bg-slate-800')}>
          全部</button>
        {families.map((f) => (
          <button key={f} type="button" onClick={() => setFamily(f)}
            className={'rounded-md border px-2 py-0.5 text-[11px] ' + (family === f ? 'border-cyan-700 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:bg-slate-800')}>
            {FAMILY_LABEL[f] ?? f}</button>
        ))}
        {data?.generated_at && (
          <span className="ml-auto text-[10px] text-slate-600">采集于 {fmtTime(data.generated_at)}{data.cached ? '（缓存）' : ''}</span>
        )}
      </div>

      {err && <ErrorBox message={err} />}
      {!err && !data && <div className="py-4 text-[11px] text-slate-500">加载中…</div>}

      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {units.map((u) => {
          const meta = STATUS_META[u.status] ?? STATUS_META.warn
          return (
            <div key={u.id} className="rounded-lg border border-slate-800 bg-slate-950/50 p-2.5" data-unit={u.id}>
              <div className="flex items-center gap-1.5">
                <span className={'inline-block h-2 w-2 rounded-full ' + meta.dot} />
                <span className="text-[12px] font-medium text-slate-200">{u.name}</span>
                <Badge color={meta.badge as 'green' | 'red' | 'amber' | 'cyan' | 'slate'}>{meta.label}</Badge>
                <span className="ml-auto text-[10px] text-slate-600">{FAMILY_LABEL[u.family] ?? u.family}</span>
              </div>
              <div className="mt-1 text-[11px] text-slate-400">{u.summary || '—'}</div>
              {metricLine(u.metrics) && (
                <div className="mt-0.5 truncate font-mono text-[10px] text-slate-600" title={metricLine(u.metrics)}>
                  {metricLine(u.metrics)}
                </div>
              )}
              {(u.issues ?? []).length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {(u.issues ?? []).slice(0, 3).map((it, i) => (
                    <li key={i} className="text-[10px] leading-snug text-amber-300">· {it}</li>
                  ))}
                </ul>
              )}
              <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-600">
                <span>最近活动：{fmtTime(u.last_activity)}</span>
                {u.probe_ms != null && <span>探测 {u.probe_ms}ms</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
