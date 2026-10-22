/**
 * 运行模式 —— GET /api/mode（1 条）
 * ------------------------------------------------------------------
 * 后端返回当前行为模式：label / description / can_accept_tasks / enable_reflection。
 * 放在「健康」页：它回答的是"云枢现在是什么状态、还能不能接活"，与健康分同屏合适。
 *
 * 【为什么 can_accept_tasks 要单独高亮】它是这个端点里**唯一可执行含义**的字段：
 * 模式处于疲劳/降级时不再接新任务。把"能不能接活"埋在一堆字段里，等于没说。
 */
import { useCallback, useEffect, useState } from 'react'
import { Activity, Loader2, RefreshCw } from 'lucide-react'
import { hubGet } from '../components/ui'
import { MODE } from '@/api/endpoints'

interface ModePayload {
  mode?: string
  label?: string
  description?: string
  can_accept_tasks?: boolean
  enable_reflection?: boolean
  thinking_mode?: string
  reasons?: unknown[]
  error?: string
}

export default function ModeCard() {
  const [data, setData] = useState<ModePayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setBusy(true); setErr('')
    try {
      const r = await hubGet<ModePayload>(MODE)
      if (r?.error) { setErr('读取失败：' + r.error); return }
      setData(r)
    } catch (e) { setErr('读取失败：' + (e instanceof Error ? e.message : String(e))) } finally { setBusy(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <div className="mb-3 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Activity size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">运行模式</h3>
        {data?.label && <span className="text-[11px] text-cyan-300">{data.label}</span>}
        {data?.can_accept_tasks === false && (
          <span className="rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">当前不接受新任务</span>
        )}
        {data?.can_accept_tasks === true && (
          <span className="rounded-sm bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">可接新任务</span>
        )}
        <button type="button" className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800" onClick={() => void load()} disabled={busy} aria-label="刷新运行模式">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
        </button>
      </div>
      {err && <div className="mt-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {data?.description && <div className="mt-1.5 text-[11px] text-slate-400">{data.description}</div>}
      <div className="mt-1.5 flex flex-wrap gap-2 text-[10px] text-slate-500">
        {data?.mode && <span className="rounded-sm bg-slate-800 px-1.5 py-0.5 font-mono">{data.mode}</span>}
        {data?.thinking_mode && <span className="rounded-sm bg-slate-800 px-1.5 py-0.5">思考模式 {data.thinking_mode}</span>}
        {data?.enable_reflection != null && <span className="rounded-sm bg-slate-800 px-1.5 py-0.5">反思 {data.enable_reflection ? '开' : '关'}</span>}
      </div>
    </div>
  )
}
