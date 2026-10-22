/**
 * 可转换工作流 —— GET /api/workflow-learning/convertible（1 条）
 * ------------------------------------------------------------------
 * 后端列出「满足质量门控且还没转换过」的工作流；转成 Skill 之后它就不再出现在这里
 * （所以空列表是**正常态**，不是出错 —— 页面上直说这句，避免被当成故障）。
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Repeat } from 'lucide-react'
import { hubGet } from '../components/ui'
import { WORKFLOW_LEARNING_CONVERTIBLE } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const PRE = 'max-h-48 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface Candidate {
  id?: string
  name?: string
  title?: string
  score?: number
  runs?: number
  [k: string]: unknown
}

export default function ConvertibleWorkflows() {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [rows, setRows] = useState<Candidate[]>([])
  const [total, setTotal] = useState(0)

  const load = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const r = await hubGet<{ candidates?: Candidate[]; total?: number }>(WORKFLOW_LEARNING_CONVERTIBLE)
      setRows(r?.candidates ?? [])
      setTotal(r?.total ?? (r?.candidates ?? []).length)
    } catch (e) { setErr('读取可转换工作流失败：' + (e instanceof Error ? e.message : String(e))) } finally { setBusy(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Repeat size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">可转换的工作流（{total}）</h3>
        <span className="text-[10px] text-slate-500">满足质量门控、且尚未转成 Skill 的工作流；转完即从这里消失</span>
        <button type="button" className={BTN + ' ml-auto'} onClick={() => void load()} disabled={busy} aria-label="刷新可转换工作流">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
        </button>
      </div>
      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      <div className="flex flex-col gap-1">
        {rows.map((c, i) => (
          <div key={String(c.id ?? c.name ?? i)} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
            <span className="font-mono text-[10px] text-cyan-300">{String(c.id ?? c.name ?? '')}</span>
            <span className="text-slate-300">{String(c.title ?? c.name ?? '')}</span>
            {c.score != null && <span className="text-[10px] text-slate-500">评分 {String(c.score)}</span>}
            {c.runs != null && <span className="text-[10px] text-slate-500">跑过 {String(c.runs)} 次</span>}
          </div>
        ))}
        {rows.length === 0 && <div className="text-[11px] text-slate-500">没有可转换的工作流 —— 空列表是正常态（要么还没学到，要么都转过了）。</div>}
      </div>
      {rows.length > 0 && <pre className={PRE + ' mt-2'}>{JSON.stringify(rows.slice(0, 5), null, 2)}</pre>}
    </div>
  )
}
