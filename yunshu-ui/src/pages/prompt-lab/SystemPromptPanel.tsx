/**
 * 系统提示词模板 —— 读取 / 预览 / 编辑 / 重置 / 同步状态（3 条）
 * ------------------------------------------------------------------
 *   GET  /api/system-prompt              模板 + 占位符说明 + 代入示例的预览
 *   POST /api/system-prompt              保存自定义模板（后端校验占位符）
 *   POST /api/system-prompt/reset        重置为默认
 *   GET  /api/system-prompt/sync-status  配置模板与运行模板是否同步
 *
 * 【为什么这里要显示"占位符清单"】模板里的 {body_status} 这类占位符写错一个字母，
 * 后端会直接 400 拒绝保存（并且是在你写完几百字之后）。把可用的占位符摆在编辑器
 * 旁边，比让用户试错便宜得多。
 *
 * 【为什么重置要两次确认】重置会丢掉整段自定义提示词 —— 这是本页唯一不可恢复的动作
 * （保存至少还能改回来）。故"重置"先展开确认条，再点确认。
 */
import { useCallback, useEffect, useState } from 'react'
import { FileCode2, Loader2, RefreshCw, RotateCcw, Save } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../hub/components/ui'
import { SYSTEM_PROMPT, SYSTEM_PROMPT_RESET, SYSTEM_PROMPT_SYNC_STATUS } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-64 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface PromptResp {
  template?: string
  is_custom?: boolean
  is_default?: boolean
  placeholders?: Record<string, string>
  preview?: string
}

interface SyncResp {
  is_synced?: boolean
  config_template_length?: number
  runtime_template_length?: number
}

export default function SystemPromptPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [data, setData] = useState<PromptResp | null>(null)
  const [draft, setDraft] = useState('')
  const [sync, setSync] = useState<SyncResp | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [p, s] = await Promise.allSettled([
        hubGet<PromptResp>(SYSTEM_PROMPT),
        hubGet<SyncResp>(SYSTEM_PROMPT_SYNC_STATUS),
      ])
      if (p.status === 'fulfilled') {
        setData(p.value)
        setDraft(p.value?.template ?? '')
      }
      if (s.status === 'fulfilled') setSync(s.value)
    } catch (e) { fail('读取系统提示词失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async () => {
    setBusy('save'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(SYSTEM_PROMPT, { content: draft })
      if (r?.ok === false) {
        // 后端会把「未知占位符」写进 error，原样上屏最有帮助
        setErr('保存被拒绝：' + (r.error || 'ok=false'))
        return
      }
      setMsg('系统提示词模板已保存')
      await load()
    } catch (e) { fail('保存失败', e) } finally { setBusy('') }
  }

  const reset = async () => {
    setBusy('reset'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(SYSTEM_PROMPT_RESET, {})
      if (r?.ok === false) { setErr('重置失败：' + (r.error || 'ok=false')); return }
      setMsg('已重置为默认模板')
      setConfirmReset(false)
      await load()
    } catch (e) { fail('重置失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const dirty = data?.template !== undefined && draft !== data.template

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <FileCode2 size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">系统提示词</h2>
        <span className="text-[11px] text-slate-500">
          {data?.is_custom ? '当前：自定义模板' : '当前：默认模板'}
          {sync ? (sync.is_synced ? ' · 配置与运行一致' : ' · 配置与运行**不一致**') : ''}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新系统提示词">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="grid gap-2 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            模板编辑
            {dirty && <span className="text-[10px] text-amber-400">（有未保存改动）</span>}
          </div>
          <textarea
            className={INPUT + ' h-64 w-full font-mono'}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="系统提示词模板"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" className={BTN_EM} onClick={() => void save()} disabled={loading || draft.trim() === ''} aria-label="保存系统提示词">
              {busy === 'save' ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />} 保存
            </button>
            <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
              <input type="checkbox" checked={confirmReset} onChange={(e) => setConfirmReset(e.target.checked)} aria-label="确认重置模板" />
              我确认重置为默认
            </label>
            <button type="button" className={BTN_RED} onClick={() => void reset()} disabled={loading || !confirmReset} aria-label="重置系统提示词">
              {busy === 'reset' ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} 重置
            </button>
          </div>
        </div>

        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-[11px] text-slate-400">可用占位符（写错一个就会 400）</div>
            <div className="flex flex-col gap-1">
              {Object.entries(data?.placeholders ?? {}).map(([k, v]) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setDraft((d) => d + '{' + k + '}')}
                  title="点一下插入到模板末尾"
                  className="flex items-start gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-left text-[11px] hover:bg-slate-800/50"
                >
                  <span className="font-mono text-[10px] text-cyan-300">{'{' + k + '}'}</span>
                  <span className="text-[10px] text-slate-500">{v}</span>
                </button>
              ))}
              {Object.keys(data?.placeholders ?? {}).length === 0 && (
                <div className="text-[10px] text-slate-600">—</div>
              )}
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-[11px] text-slate-400">代入示例值后的预览</div>
            <pre className={PRE}>{data?.preview ?? '—'}</pre>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-[11px] text-slate-400">同步状态</div>
            <pre className={PRE}>{JSON.stringify(sync ?? {}, null, 2)}</pre>
          </div>
        </div>
      </div>
    </div>
  )
}
