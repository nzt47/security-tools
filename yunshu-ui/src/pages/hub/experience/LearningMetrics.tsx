/**
 * 学习有效性度量 —— GET /api/learning/metrics（1 条）
 * ------------------------------------------------------------------
 * 放在「经验」页：这份度量数的是"学到的东西有没有用"（经验/知识卡/技能/工作流的
 * 产出增量、进化采纳率、按任务类型的失败率），正是经验库该回答的问题。
 *
 * 【为什么把"全 0"单独说一句】新部署下这些指标天然是 0，看起来像坏了。
 * 页面明写"窗口内没有样本时全 0 是正常态"，比让人去查后端强。
 */
import { useCallback, useEffect, useState } from 'react'
import { BarChart3, Loader2, RefreshCw } from 'lucide-react'
import { hubGet } from '../components/ui'
import { LEARNING_METRICS } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface Metrics {
  days?: number
  evaluation?: { total?: number; passed?: number; failed?: number; failure_rate?: number }
  kpis?: Record<string, unknown>
  generated_at?: string
  error?: string
}

export default function LearningMetrics() {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [data, setData] = useState<Metrics | null>(null)

  const load = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const r = await hubGet<Metrics>(LEARNING_METRICS)
      if (r?.error) { setErr('读取失败：' + r.error); return }
      setData(r)
    } catch (e) { setErr('读取失败：' + (e instanceof Error ? e.message : String(e))) } finally { setBusy(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const ev = data?.evaluation
  const empty = (ev?.total ?? 0) === 0

  return (
    <div className="mb-3 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <BarChart3 size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">学习有效性度量</h3>
        {data?.days != null && <span className="text-[10px] text-slate-500">窗口 {data.days} 天</span>}
        <button type="button" className={BTN + ' ml-auto'} onClick={() => void load()} disabled={busy} aria-label="刷新学习度量">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
        </button>
      </div>
      {err && <div className="mb-2 rounded border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['评估样本', ev?.total ?? 0],
          ['通过', ev?.passed ?? 0],
          ['失败', ev?.failed ?? 0],
          ['失败率', ((ev?.failure_rate ?? 0) * 100).toFixed(1) + '%'],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="font-mono text-[13px] text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>
      {empty && <div className="mb-2 text-[10px] text-slate-500">窗口内没有评估样本 —— 全 0 是正常态（不是接口坏了）。</div>}
      <pre className={PRE}>{JSON.stringify(data?.kpis ?? {}, null, 2)}</pre>
    </div>
  )
}
