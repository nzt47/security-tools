/**
 * LlmHealthMenu —— 会话任务区「LLM 自检」下拉
 * ------------------------------------------------------------------
 * 【为什么要有它（而不是浏览器里的通用终端）】
 * 排查"对话像在自说自话 / 子代理跑不通 / 后台任务永远为空"这类问题时，真正需要执行的是
 * 一组**固定判据**：key 是否有效、模型名是否被识别、地址是否正确。让用户把这些命令
 * 复制到命令行执行，既麻烦又要经手密钥；做成服务端一键自检后：
 *   - 结论结构化（key 掩码、HTTP 状态、耗时、上游原文、修复建议）；
 *   - 密钥不出服务端；
 *   - 与对话流**同一判据**（plugins/chat.py::key_usable），不会出现"自检说没事、
 *     对话却在演示模式"的矛盾。
 *
 * 数据源：POST /api/diagnostics/llm-check（需要 API 令牌；会向 LLM 端点发一次最小真实调用）
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, ChevronRight, Loader2, Stethoscope, X } from 'lucide-react'
import { authHeader } from '../../../lib/apiToken'

interface LlmCheck {
  ok?: boolean
  config?: {
    provider?: string
    model?: string
    base_url?: string
    api_key_masked?: string
    api_key_length?: number
  }
  workbench_demo_mode?: boolean
  workbench_note?: string
  probe?: {
    ok?: boolean
    http_status?: number
    latency_ms?: number
    endpoint?: string
    model?: string
    usage?: Record<string, number>
    error?: string
    raw_message?: string
    hints?: string[]
  }
  checked_at?: string
  error?: string
}

export function LlmHealthMenu() {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<LlmCheck | null>(null)
  const [fetchError, setFetchError] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const run = useCallback(async () => {
    setLoading(true)
    setFetchError('')
    try {
      const res = await fetch('/api/diagnostics/llm-check', {
        method: 'POST',
        headers: { ...authHeader() },
      })
      const body = (await res.json().catch(() => ({}))) as LlmCheck
      if (res.status === 401) {
        setFetchError('需要 API 令牌（401）：请在「系统组件 → 插件管理 → API 令牌」填入 FLASK_API_TOKEN 后重试')
      } else if (!res.ok) {
        setFetchError(String(body?.error ?? `HTTP ${res.status}`))
      }
      setData(body)
    } catch (e) {
      setFetchError(`自检请求失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open && !data && !loading) void run()
  }, [open, data, loading, run])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const healthy = data?.ok === true
  const probe = data?.probe

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="LLM 自检：key 是否有效 / 模型名是否被识别 / 地址是否正确（无需命令行）"
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
          open
            ? 'border-amber-600/60 bg-amber-500/10 text-amber-300'
            : 'border-slate-700 text-slate-400 hover:border-amber-600/60 hover:bg-slate-800 hover:text-amber-300'
        }`}
      >
        <Stethoscope size={11} />
        LLM 自检
        {data && (
          <span className={healthy ? 'text-emerald-400' : 'text-rose-400'}>
            {healthy ? '正常' : '异常'}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-[70] w-[420px] rounded-xl border border-slate-700 bg-slate-900/98 p-3 text-[11.5px] shadow-2xl backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-medium text-slate-200">🩺 LLM 连通性自检</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void run()}
                disabled={loading}
                title="重新自检（会向 LLM 端点发一次极小的真实请求）"
                className="rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                {loading ? <Loader2 size={11} className="animate-spin" /> : '重新自检'}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭 LLM 自检"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          {fetchError && (
            <p className="mb-2 rounded-md border border-rose-900/60 bg-rose-500/10 px-2 py-1 text-rose-300">{fetchError}</p>
          )}

          {loading && !data && <p className="text-slate-400">正在自检（含一次最小真实调用）…</p>}

          {data && (
            <>
              {/* 结论 */}
              <div
                className={`mb-2 flex items-start gap-2 rounded-lg border px-2 py-1.5 ${
                  healthy ? 'border-emerald-900/60 bg-emerald-500/10' : 'border-rose-900/60 bg-rose-500/10'
                }`}
              >
                {healthy ? (
                  <CheckCircle2 size={13} className="mt-[1px] shrink-0 text-emerald-400" />
                ) : (
                  <AlertTriangle size={13} className="mt-[1px] shrink-0 text-rose-400" />
                )}
                <span className={healthy ? 'text-emerald-200' : 'text-rose-200'}>
                  {healthy ? 'LLM 可用：对话/子代理/后台任务都能走真实模型' : 'LLM 不可用或工作台会走演示模式'}
                </span>
              </div>

              {/* 配置（密钥掩码） */}
              <div className="mb-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2 font-mono text-[10.5px] text-slate-300">
                <div className="mb-1 flex items-center gap-1 font-sans text-[10px] text-slate-500">
                  <Activity size={10} /> 当前生效配置（.env，密钥已掩码）
                </div>
                <div>provider : {data.config?.provider || '-'}</div>
                <div>model&nbsp;&nbsp;&nbsp;: {data.config?.model || '-'}</div>
                <div>base_url : {data.config?.base_url || '（未配置）'}</div>
                <div>
                  api_key&nbsp;&nbsp;: {data.config?.api_key_masked || '（未配置）'}
                  {data.config?.api_key_length ? `（长度 ${data.config.api_key_length}）` : ''}
                </div>
              </div>

              {/* 工作台判定 */}
              {data.workbench_demo_mode && (
                <p className="mb-2 rounded-md border border-amber-900/60 bg-amber-500/10 px-2 py-1 text-[10.5px] leading-relaxed text-amber-300">
                  {data.workbench_note}
                </p>
              )}

              {/* 探测结果 */}
              {probe && (
                <div className="mb-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2 font-mono text-[10.5px] text-slate-300">
                  <div className="mb-1 font-sans text-[10px] text-slate-500">
                    最小真实调用：{probe.endpoint || '-'}
                  </div>
                  <div>
                    结果 : {probe.ok ? 'HTTP 200 ✓' : `失败（${probe.error || '无错误信息'}）`}
                    {probe.latency_ms ? ` · ${Math.round(probe.latency_ms)}ms` : ''}
                    {probe.model ? ` · ${probe.model}` : ''}
                  </div>
                  {probe.raw_message && (
                    <div className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words text-rose-300">
                      {probe.raw_message}
                    </div>
                  )}
                </div>
              )}

              {/* 修复建议 */}
              {(probe?.hints ?? []).length > 0 && (
                <div className="mb-1 rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                  <div className="mb-1 text-[10px] text-slate-500">下一步该改什么</div>
                  <ul className="space-y-1">
                    {(probe?.hints ?? []).map((h, i) => (
                      <li key={i} className="flex gap-1 leading-relaxed text-slate-300">
                        <ChevronRight size={11} className="mt-[2px] shrink-0 text-cyan-400" />
                        <span>{h}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {data.checked_at && (
                <div className="font-mono text-[9.5px] text-slate-600">检查于 {data.checked_at}</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
