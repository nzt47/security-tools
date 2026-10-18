/**
 * 模块节点详情 / 干预 —— 2 条
 * ------------------------------------------------------------------
 *   GET  /api/modules/<module_id>/detail   状态/指标/可执行动作/近期审计
 *   POST /api/modules/<module_id>/actions  {action, reason, params} 统一干预入口
 *
 * 【为什么干预要点两次、还要填原因】后端把这类动作记为审计事件（_audit），
 * 且部分动作是重启/清空级别。一次点击就生效的钮，在排障现场最容易被误触。
 * 故：点动作 → 展开确认条（可填原因）→ 再点「确认执行」。
 *
 * 【为什么把"后端声明的动作"当数据渲染】动作清单来自 detail.actions（后端
 * node_actions），前端**不自造**动作名：紧急动作的 params 在服务端固定（const:），
 * 前端能改的只有 reason。
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Loader2, Play, RefreshCw, X } from 'lucide-react'
import { hubGet, hubPost } from '../components/ui'
import { MODULES_ACTIONS, modulesDetail } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_DANGER = BTN + ' border-amber-800/70 text-amber-300 hover:bg-amber-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface NodeAction {
  action?: string
  label?: string
  description?: string
  danger?: boolean
}

interface ModuleDetailPayload {
  module_id?: string
  name?: string
  domain?: string
  path?: string
  type?: string
  description?: string
  status?: string
  status_detail?: string
  metrics?: Record<string, unknown>
  actions?: NodeAction[]
  recent_actions?: Record<string, unknown>[]
  error?: string
}

export default function ModuleDetail({ moduleId, onClose }: { moduleId: string; onClose: () => void }) {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [data, setData] = useState<ModuleDetailPayload | null>(null)
  const [pending, setPending] = useState('')
  const [reason, setReason] = useState('')
  const [result, setResult] = useState<Record<string, unknown> | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const r = await hubGet<ModuleDetailPayload>(modulesDetail(moduleId))
      if (r?.error) { setErr('读取详情失败：' + r.error); return }
      setData(r)
    } catch (e) { fail('读取详情失败', e) } finally { setBusy('') }
  }, [moduleId])

  useEffect(() => { void load() }, [load])

  const run = async (action: string) => {
    setBusy('action:' + action); setErr(''); setMsg(''); setResult(null)
    try {
      const r = await hubPost<Record<string, unknown>>(MODULES_ACTIONS(moduleId), {
        action, reason: reason.trim(), params: {},
      })
      setResult(r)
      const ok = (r as { ok?: boolean })?.ok
      setMsg(ok === false ? ('动作 ' + action + ' 未成功') : ('动作 ' + action + ' 已执行'))
      setPending('')
      setReason('')
      await load()
    } catch (e) { fail('执行动作失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const actions = data?.actions ?? []

  return (
    <div className="mt-2 rounded-lg border border-slate-700 bg-slate-900/80 p-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium text-slate-200">{data?.name || moduleId}</span>
        <span className="font-mono text-[10px] text-slate-500">{moduleId}</span>
        {data?.status && <span className="text-[10px] text-slate-400">状态 {data.status}</span>}
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label={'刷新模块详情 ' + moduleId}>
            {busy === 'load' ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />} 刷新
          </button>
          <button type="button" className={BTN} onClick={onClose} aria-label={'关闭模块详情 ' + moduleId}>
            <X size={10} /> 关闭
          </button>
        </div>
      </div>

      {err && <div className="mb-1.5 rounded border border-red-900/60 bg-red-500/10 px-2 py-1 text-[10px] text-red-300">{err}</div>}
      {msg && <div className="mb-1.5 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-300">{msg}</div>}

      {data?.description && <div className="mb-1.5 text-[11px] text-slate-400">{data.description}</div>}
      {data?.status_detail && (
        <div className="mb-1.5 flex items-center gap-1 text-[10px] text-amber-300">
          <AlertTriangle size={10} /> {data.status_detail}
        </div>
      )}

      {data?.metrics && Object.keys(data.metrics).length > 0 && (
        <pre className={PRE}>{JSON.stringify(data.metrics, null, 2)}</pre>
      )}

      <div className="mt-1.5">
        <div className="mb-1 text-[10px] text-slate-500">可执行动作（来自后端声明的动作清单）</div>
        <div className="flex flex-wrap items-center gap-1">
          {actions.map((a) => {
            const key = String(a.action ?? '')
            if (!key) return null
            return (
              <button
                key={key}
                type="button"
                className={a.danger ? BTN_DANGER : BTN}
                onClick={() => setPending(pending === key ? '' : key)}
                disabled={loading}
                aria-label={'执行动作 ' + key}
                title={a.description ?? ''}
              >
                <Play size={10} /> {a.label || key}
              </button>
            )
          })}
          {actions.length === 0 && <span className="text-[10px] text-slate-600">该节点未声明可执行动作。</span>}
        </div>
      </div>

      {pending && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 rounded border border-amber-900/50 bg-amber-500/5 px-2 py-1.5">
          <span className="text-[10px] text-amber-300">执行 {pending} 会写入审计；请确认，并可选填原因。</span>
          <input
            className={INPUT + ' w-56'}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="原因（写入审计）"
            aria-label="动作原因"
          />
          <button type="button" className={BTN_DANGER} onClick={() => void run(pending)} disabled={loading} aria-label={'确认执行动作 ' + pending}>
            {busy === 'action:' + pending ? <Loader2 size={10} className="animate-spin" /> : null} 确认执行
          </button>
          <button type="button" className={BTN} onClick={() => setPending('')}>取消</button>
        </div>
      )}

      {result && <pre className={PRE + ' mt-1.5'}>{JSON.stringify(result, null, 2)}</pre>}

      {(data?.recent_actions ?? []).length > 0 && (
        <div className="mt-1.5">
          <div className="mb-1 text-[10px] text-slate-500">近期审计（最近 10 条）</div>
          <pre className={PRE}>{JSON.stringify(data?.recent_actions, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
