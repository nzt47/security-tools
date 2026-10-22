/**
 * 健康体检 —— /api/health 的评分 / 趋势 / 历史 / 告警 / 权重 / 快速检查（8 条）
 * ------------------------------------------------------------------
 * 与「健康仪表盘」已有的 /api/health/dashboard 不是一回事：
 *   · dashboard 是给总览卡片用的聚合视图（本页上半部分已在用）；
 *   · 这里这一组是**可下钻的体检面**：分维度得分与指标、趋势、历史快照、
 *     告警规则状态、权重编辑、以及一次毫秒级的快速体检。
 *
 * 【权重为什么允许在页面上改】后端给了 PUT /api/health/weights，且权重直接影响
 * "健康度"这个数字。看得见数字却改不了权重，用户会以为分数是客观物理量；
 * 反过来，改权重需要显式"确认"——它会改变所有人看到的评分。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Activity, AlertTriangle, Download, Gauge, Loader2, RefreshCw, Save, ShieldCheck, Zap,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPut } from '../components/ui'
import {
  HEALTH_ALERTS,
  HEALTH_EXPORT,
  HEALTH_HISTORY,
  HEALTH_QUICK_CHECK,
  HEALTH_SCORE,
  HEALTH_SUMMARY,
  HEALTH_TREND,
  HEALTH_WEIGHTS,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

interface Dimension { score?: number; weight?: number; indicators?: Record<string, number>; issues?: unknown[] }
interface ScoreResp {
  overall_score?: number
  level?: string
  dimensions?: Record<string, Dimension>
  critical_issues?: unknown[]
  recommendations?: unknown[]
}
interface QuickCheck {
  ok?: boolean
  level?: string
  score?: number
  elapsed_ms?: number
  issues?: unknown[]
  metrics?: Record<string, number>
}
interface TrendResp { data_points?: number; trend?: { trend?: string; change?: number } }
interface HistoryResp { history?: ScoreResp[]; count?: number }
interface AlertsResp { alerts?: { name?: string; condition?: string; severity?: string; fired?: boolean; message?: string }[] }
interface WeightsResp { dimensions?: string[]; weights?: Record<string, number> }

export default function HealthVitals() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [score, setScore] = useState<ScoreResp | null>(null)
  const [summary, setSummary] = useState<ScoreResp | null>(null)
  const [trend, setTrend] = useState<TrendResp | null>(null)
  const [history, setHistory] = useState<ScoreResp[]>([])
  const [alerts, setAlerts] = useState<AlertsResp['alerts']>([])
  const [weights, setWeights] = useState<WeightsResp | null>(null)
  const [weightText, setWeightText] = useState('')
  const [confirmWeights, setConfirmWeights] = useState(false)
  const [quick, setQuick] = useState<QuickCheck | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadAll = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [sc, su, tr, hi, al, we] = await Promise.all([
        hubGet<ScoreResp>(HEALTH_SCORE),
        hubGet<ScoreResp>(HEALTH_SUMMARY),
        hubGet<TrendResp>(HEALTH_TREND + '?days=7'),
        hubGet<HistoryResp>(HEALTH_HISTORY + '?limit=5'),
        hubGet<AlertsResp>(HEALTH_ALERTS),
        hubGet<WeightsResp>(HEALTH_WEIGHTS),
      ])
      setScore(sc)
      setSummary(su)
      setTrend(tr)
      setHistory(hi?.history ?? [])
      setAlerts(al?.alerts ?? [])
      setWeights(we)
      setWeightText(JSON.stringify(we?.weights ?? {}, null, 2))
    } catch (e) { fail('读取健康体检失败', e) } finally { setBusy('') }
  }, [])

  const runQuick = async () => {
    setBusy('quick'); setErr(''); setMsg('')
    try {
      const r = await hubGet<QuickCheck>(HEALTH_QUICK_CHECK)
      if (r?.ok === false) { bad('快速体检失败', r as { error?: string }); return }
      setQuick(r)
      setMsg('快速体检完成（' + Math.round(r?.elapsed_ms ?? 0) + 'ms）')
    } catch (e) { fail('快速体检失败', e) } finally { setBusy('') }
  }

  const saveWeights = async () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(weightText || '{}')
    } catch {
      setErr('权重不是合法 JSON —— 没有提交')
      return
    }
    setBusy('weights'); setErr(''); setMsg('')
    try {
      const r = await hubPut<{ ok?: boolean; error?: string }>(HEALTH_WEIGHTS, { weights: parsed })
      if (r?.ok === false) { bad('保存权重失败', r); return }
      setMsg('健康维度权重已更新')
      setConfirmWeights(false)
      await loadAll()
    } catch (e) { fail('保存权重失败', e) } finally { setBusy('') }
  }

  useEffect(() => { void loadAll() }, [loadAll])

  const loading = busy !== ''
  const dims = Object.entries(score?.dimensions ?? {})

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ShieldCheck size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">健康体检</h3>
        <span className="text-[10px] text-slate-500">
          分维度得分 / 趋势 / 历史 / 告警 / 权重 / 一次毫秒级快速体检
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {/* 导出走浏览器下载（GET 返回文件），不经过 hubGet 的 JSON 解析 */}
          <a href={HEALTH_EXPORT} download className={BTN} aria-label="导出健康度报告">
            <Download size={11} /> 导出报告
          </a>
          <button type="button" className={BTN_EM} onClick={() => void runQuick()} disabled={loading} aria-label="快速体检">
            {busy === 'quick' ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />} 快速体检
          </button>
          <button type="button" className={BTN} onClick={() => void loadAll()} disabled={loading} aria-label="刷新健康体检">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['总分', score?.overall_score ?? summary?.overall_score ?? '—'],
          ['等级', score?.level ?? summary?.level ?? '—'],
          ['趋势', (trend?.trend?.trend ?? '—') + (trend?.trend?.change !== undefined ? ' (' + trend.trend.change + ')' : '')],
          ['历史快照', history.length],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="truncate font-mono text-[12px] text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>

      {quick && (
        <div className="mb-2 rounded-sm border border-slate-800 bg-slate-950/60 p-2">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            <Activity size={11} className="text-cyan-400" /> 快速体检 · {quick.level ?? '-'} · {quick.score ?? '-'} 分
            <span className="ml-auto font-mono text-[10px] text-slate-600">{Math.round(quick.elapsed_ms ?? 0)}ms</span>
          </div>
          <div className="flex flex-wrap gap-1 font-mono text-[10px] text-slate-400">
            {Object.entries(quick.metrics ?? {}).map(([k, v]) => (
              <span key={k} className="rounded-sm bg-slate-800/60 px-1.5 py-0.5">{k}={Math.round(v)}</span>
            ))}
          </div>
          {(quick.issues?.length ?? 0) > 0 && (
            <div className="mt-1 text-[10px] text-amber-300">问题 {quick.issues?.length} 条</div>
          )}
        </div>
      )}

      {dims.length > 0 && (
        <div className="mb-2 overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead className="text-[10px] text-slate-500">
              <tr>
                <th className="px-1 py-1">维度</th>
                <th className="px-1 py-1">得分</th>
                <th className="px-1 py-1">权重</th>
                <th className="px-1 py-1">指标</th>
              </tr>
            </thead>
            <tbody>
              {dims.map(([name, d]) => (
                <tr key={name} className="border-t border-slate-800/60">
                  <td className="px-1 py-1 text-slate-300">{name}</td>
                  <td className="px-1 py-1 font-mono text-cyan-300">{Math.round(d.score ?? 0)}</td>
                  <td className="px-1 py-1 font-mono text-slate-400">{d.weight ?? '—'}</td>
                  <td className="px-1 py-1 font-mono text-[10px] text-slate-500">
                    {Object.entries(d.indicators ?? {}).map(([k, v]) => k + '=' + v).join(' · ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(alerts?.length ?? 0) > 0 && (
        <div className="mb-2">
          <div className="mb-1 flex items-center gap-1 text-[11px] text-amber-300">
            <AlertTriangle size={11} /> 健康度告警（{alerts?.length}）
          </div>
          <div className="flex flex-col gap-1">
            {alerts?.map((a, i) => (
              <div key={(a.name ?? '') + i} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-cyan-300">{a.name ?? '?'}</span>
                <span className="text-slate-400">{a.condition ?? ''}</span>
                <span className="ml-auto text-[10px] text-slate-500">{a.severity ?? ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
        <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
          <Gauge size={11} className="text-cyan-400" /> 维度权重（改它会改变所有人看到的健康度）
        </div>
        <textarea
          className={INPUT + ' h-20 w-full font-mono'}
          value={weightText}
          onChange={(e) => setWeightText(e.target.value)}
          aria-label="健康维度权重 JSON"
        />
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmWeights} onChange={(e) => setConfirmWeights(e.target.checked)} aria-label="确认修改权重" />
            我确认修改权重
          </label>
          <button type="button" className={BTN_EM} onClick={() => void saveWeights()} disabled={loading || !confirmWeights} aria-label="保存权重">
            {busy === 'weights' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 保存权重
          </button>
          <span className="text-[10px] text-slate-600">
            可选维度：{(weights?.dimensions ?? []).join(' / ')}
          </span>
        </div>
      </div>
    </div>
  )
}
