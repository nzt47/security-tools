/**
 * 编排与蒸馏 —— 语义层热更 / 规划引擎开关 / 过程蒸馏（4 条）
 * ------------------------------------------------------------------
 *   GET  /api/orchestrator/semantic-config   当前生效语义层配置 + API 覆盖层
 *   POST /api/orchestrator/semantic-config   热更（只接受已知键，类型+范围严格校验）
 *   POST /api/planning/toggle                规划引擎运行开关（热生效）
 *   GET  /api/process-distill/health         蒸馏可用性（LLM 配置 / 产物数）
 *   POST /api/process-distill/distill        从知识库/素材蒸馏并固化为 workflow/skill
 *
 * 【为什么"规划开关"要写明不持久】后端把它做成运行期翻转（_planning_enabled），
 * 重启后以 config.yaml 为准。不写清楚，用户会以为关掉就永久关了。
 *
 * 【为什么蒸馏的 query/paths 至少要一个】后端 400：没有素材无法蒸馏。
 * 这里先本地拦一道并说明，而不是让用户点完再看报错。
 */
import { useCallback, useEffect, useState } from 'react'
import { FileStack, Loader2, Play, RefreshCw, Settings2, Wand2 } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  ORCHESTRATOR_SEMANTIC_CONFIG,
  PLANNING_TOGGLE,
  PROCESS_DISTILL,
  PROCESS_DISTILL_HEALTH,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

export default function EngineOrchestration() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [sem, setSem] = useState<Record<string, unknown> | null>(null)
  const [override, setOverride] = useState<Record<string, unknown> | null>(null)
  const [draft, setDraft] = useState('')
  const [planning, setPlanning] = useState<boolean | null>(null)

  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [query, setQuery] = useState('')
  const [paths, setPaths] = useState('')
  const [distill, setDistill] = useState<Record<string, unknown> | null>(null)
  const [confirmPlan, setConfirmPlan] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [s, h] = await Promise.allSettled([
        hubGet<{ config?: Record<string, unknown>; api_override?: Record<string, unknown> }>(ORCHESTRATOR_SEMANTIC_CONFIG),
        hubGet<Record<string, unknown>>(PROCESS_DISTILL_HEALTH),
      ])
      if (s.status === 'fulfilled') {
        setSem(s.value?.config ?? null)
        setOverride(s.value?.api_override ?? null)
        setDraft(JSON.stringify(s.value?.api_override ?? {}, null, 2))
      }
      if (h.status === 'fulfilled') setHealth(h.value)
    } catch (e) { fail('读取编排配置失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const saveSemantic = async () => {
    setBusy('sem'); setErr(''); setMsg('')
    let payload: unknown
    try {
      payload = JSON.parse(draft || '{}')
    } catch { setErr('覆盖层不是合法 JSON —— 没有提交'); return }
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; applied?: unknown }>(ORCHESTRATOR_SEMANTIC_CONFIG, payload as Record<string, unknown>)
      if (r?.ok === false) { setErr('热更被拒绝：' + (r.error || 'ok=false')); return }
      setMsg('语义层配置已热更（下次请求生效）')
      await load()
    } catch (e) { fail('热更失败', e) } finally { setBusy('') }
  }

  const togglePlanning = async (next: boolean) => {
    setBusy('plan'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; planning_enabled?: boolean; note?: string; error?: string }>(PLANNING_TOGGLE, { enabled: next })
      if (r?.ok === false) { setErr('切换失败：' + (r.error || 'ok=false')); return }
      setPlanning(Boolean(r?.planning_enabled))
      setMsg('规划引擎已' + (r?.planning_enabled ? '启用' : '停用') + '（运行期；持久化请改 config.yaml planning.enabled）')
      setConfirmPlan(false)
    } catch (e) { fail('切换失败', e) } finally { setBusy('') }
  }

  const runDistill = async () => {
    const list = paths.split(/[\n,]/).map((s) => s.trim()).filter(Boolean)
    if (!query.trim() && list.length === 0) {
      setErr('query 与 paths 至少提供一个 —— 没有素材无法蒸馏')
      return
    }
    setBusy('distill'); setErr(''); setMsg(''); setDistill(null)
    try {
      const r = await hubPost<Record<string, unknown>>(PROCESS_DISTILL, {
        query: query.trim(), paths: list, top_k: 5,
      })
      setDistill(r)
      setMsg((r as { ok?: boolean })?.ok === false ? '蒸馏未成功' : '蒸馏完成（产物见下方结果）')
    } catch (e) { fail('蒸馏失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Settings2 size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">编排与蒸馏</h2>
        <span className="text-[11px] text-slate-500">语义层热更 / 规划引擎开关 / 过程蒸馏（把素材固化成 workflow 或技能）</span>
        <div className="ml-auto">
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新编排配置">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="grid gap-2 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            <Settings2 size={11} className="text-cyan-400" /> 语义层配置（当前生效）
          </div>
          <pre className={PRE}>{JSON.stringify(sem ?? {}, null, 2)}</pre>
          <div className="mt-2 mb-1 text-[11px] text-slate-400">API 覆盖层（提交后即时生效；只接受已知键）</div>
          <textarea
            className={INPUT + ' h-32 w-full font-mono'}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="语义层覆盖层 JSON"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <button type="button" className={BTN_EM} onClick={() => void saveSemantic()} disabled={loading} aria-label="提交语义层配置">
              {busy === 'sem' ? <Loader2 size={11} className="animate-spin" /> : null} 提交热更
            </button>
            <span className="text-[10px] text-slate-500">已知键：enabled / min_score / top_k / use_vector / use_bm25 / use_reranker / fusion_mode</span>
          </div>
          {override && Object.keys(override).length === 0 && (
            <div className="mt-1 text-[10px] text-slate-600">当前没有 API 覆盖（用的是 env/config.yaml 的值）。</div>
          )}
        </div>

        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <Play size={11} className="text-cyan-400" /> 规划引擎（运行期开关）
            </div>
            <div className="mb-1 text-[10px] text-amber-400">
              热生效、**不落配置**：重启后以 config.yaml 的 planning.enabled 为准。
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmPlan} onChange={(e) => setConfirmPlan(e.target.checked)} aria-label="确认切换规划引擎" />
                我确认切换
              </label>
              <button type="button" className={BTN_EM} onClick={() => void togglePlanning(true)} disabled={loading || !confirmPlan} aria-label="启用规划引擎">
                启用
              </button>
              <button type="button" className={BTN} onClick={() => void togglePlanning(false)} disabled={loading || !confirmPlan} aria-label="停用规划引擎">
                停用
              </button>
              {planning !== null && <span className="text-[10px] text-slate-400">上次结果：{planning ? '已启用' : '已停用'}</span>}
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <FileStack size={11} className="text-cyan-400" /> 过程蒸馏可用性
            </div>
            <pre className={PRE}>{JSON.stringify(health ?? {}, null, 2)}</pre>
          </div>
        </div>
      </div>

      <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
          <Wand2 size={11} className="text-cyan-400" /> 从素材蒸馏（query 与 paths 至少一个）
        </div>
        <div className="grid gap-2 lg:grid-cols-2">
          <input
            className={INPUT}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="query：要蒸馏的主题/问题"
            aria-label="蒸馏 query"
          />
          <input
            className={INPUT}
            value={paths}
            onChange={(e) => setPaths(e.target.value)}
            placeholder="paths：素材路径（逗号或换行分隔，可空）"
            aria-label="蒸馏素材路径"
          />
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button type="button" className={BTN_EM} onClick={() => void runDistill()} disabled={loading} aria-label="执行蒸馏">
            {busy === 'distill' ? <Loader2 size={11} className="animate-spin" /> : <Wand2 size={11} />} 开始蒸馏
          </button>
          <span className="text-[10px] text-slate-500">产物会固化为 workflow / skill；这里是唯一入口（此前只有 API）。</span>
        </div>
        {distill && <pre className={PRE + ' mt-2 max-h-72'}>{JSON.stringify(distill, null, 2)}</pre>}
      </div>
    </div>
  )
}
