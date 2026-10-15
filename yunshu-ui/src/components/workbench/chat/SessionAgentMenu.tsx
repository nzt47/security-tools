/**
 * SessionAgentMenu —— 会话「智能体（分身）」选择器
 * ------------------------------------------------------------------
 * 与 SubagentMenu 的分工：SubagentMenu 选的是**当前任务的委托对象**（一次委派）；
 * 本组件选的是**本会话自己使用的分身**（会话级 agent，落库在 SessionMeta.agent），
 * 决定这段对话由母体还是某个已装配的分身来承接。
 *
 * 数据源（与 SubagentMenu 同源）：
 *   - GET /api/subagent/list → {subagents:[{name, model_id, status, ...}]}
 * 写入：
 *   - PUT /api/sessions/<id>/agent {agent: string|null}（null = 母体）
 *
 * 失败不做静默：列表拉取失败 / 切换失败都在下拉里显式上屏。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Check, ChevronDown, Loader2, RefreshCw, X } from 'lucide-react'
import { hubGet } from '../../../pages/hub/components/ui'
import { sessionApi } from '../../../lib/sessionApi'
import { SUBAGENT_LIST } from '@/api/endpoints'

interface Subagent {
  name: string
  model_id?: string
  status?: string
  memory_provider?: string
}

export interface SessionAgentMenuProps {
  /** 当前会话 id（空 = 不可切换） */
  sessionId?: string | null
  /** 当前会话的 agent；空/null = 母体 */
  agent?: string | null
  /** 切换成功回调（父层用它同步会话列表本地状态） */
  onChanged?: (agent: string | null) => void
}

export function SessionAgentMenu({ sessionId, agent, onChanged }: SessionAgentMenuProps) {
  const [open, setOpen] = useState(false)
  const [list, setList] = useState<Subagent[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const current = (agent ?? '').trim()

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await hubGet<{ ok?: boolean; subagents?: Subagent[]; count?: number; error?: string }>(
        SUBAGENT_LIST,
      )
      if (r?.ok === false) {
        setError(String(r.error ?? '分身列表查询失败'))
        setList([])
      } else {
        setList(Array.isArray(r?.subagents) ? r.subagents : [])
      }
    } catch (e) {
      // 分身系统未启用（subagent.enabled=False）时接口报错属正常，给出可执行提示
      setError(
        `智能体列表不可用：${e instanceof Error ? e.message : String(e)}（请确认后端已启动且 subagent.enabled=true）`,
      )
      setList([])
    } finally {
      setLoading(false)
      setLoaded(true)
    }
  }, [])

  // 首次展开时加载（关闭后保留已加载数据）
  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

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

  /** 选择分身（name='' → 母体）；失败上屏 */
  const pick = async (name: string) => {
    if (!sessionId) {
      setError('未选择会话：请先在左侧选中一个会话')
      return
    }
    const next = name.trim() || null
    setSaving(true)
    setError('')
    try {
      const res = await sessionApi.setAgent(sessionId, next)
      onChanged?.((res?.agent ?? next) || null)
      setOpen(false)
    } catch (e) {
      setError(`切换智能体失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const names = list.map((s) => s.name)
  const currentMissing = Boolean(current) && loaded && !names.includes(current)
  const label = current || '母体'

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`智能体：${label}`}
        disabled={!sessionId}
        title={
          sessionId
            ? '本会话智能体：选母体或某个分身来承接这段对话'
            : '未选择会话，无法切换智能体'
        }
        className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-40 ${
          open
            ? 'border-cyan-600/60 bg-cyan-500/10 text-cyan-300'
            : 'border-slate-700 text-slate-400 hover:border-cyan-600/60 hover:bg-slate-800 hover:text-cyan-300'
        }`}
      >
        <Bot size={11} />
        智能体
        <span className="max-w-[110px] truncate text-slate-500" title={label}>
          {label}
        </span>
        {error && !open && (
          <span
            title={error}
            className="rounded-full bg-rose-500/20 px-1.5 text-[9px] text-rose-300"
          >
            !
          </span>
        )}
        <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-[70] w-[300px] rounded-xl border border-slate-700 bg-slate-900/98 p-3 text-[11.5px] shadow-2xl backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <span className="font-medium text-slate-200">🧠 本会话智能体</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void load()}
                disabled={loading}
                title="刷新分身列表"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭智能体菜单"
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          <p className="mb-2 text-[10.5px] leading-relaxed text-slate-500">
            选中的智能体用于**本会话**（与「子代理」按钮的一次性委托是两件事）；母体 = 不指定分身。
          </p>

          {error && (
            <p className="mb-2 rounded-md border border-rose-900/60 bg-rose-500/10 px-2 py-1 text-rose-300">
              {error}
            </p>
          )}

          <div className="max-h-56 overflow-y-auto">
            <button
              type="button"
              onClick={() => void pick('')}
              disabled={saving}
              className={`mb-1 flex w-full items-start gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors disabled:opacity-50 ${
                !current ? 'border-cyan-600/60 bg-cyan-500/10' : 'border-slate-800 hover:bg-slate-800/60'
              }`}
            >
              <span className="mt-[1px] w-3 shrink-0 text-cyan-400">
                {!current ? <Check size={12} /> : null}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <b className="truncate font-medium text-slate-200">母体（默认）</b>
                </span>
                <span className="mt-0.5 block text-[10px] text-slate-500">
                  不指定分身，由母体承接本会话
                </span>
              </span>
            </button>

            {/* 当前选择已不在活跃列表：仍然可见可选（避免"选了却看不见"） */}
            {currentMissing && (
              <button
                type="button"
                onClick={() => void pick(current)}
                disabled={saving}
                className="mb-1 flex w-full items-start gap-2 rounded-lg border border-cyan-600/60 bg-cyan-500/10 px-2 py-1.5 text-left"
              >
                <span className="mt-[1px] w-3 shrink-0 text-cyan-400">
                  <Check size={12} />
                </span>
                <span className="min-w-0 flex-1">
                  <b className="truncate font-medium text-slate-200">{current}</b>
                  <span className="mt-0.5 block text-[10px] text-amber-300">
                    已不在活跃列表（分身跑完即回收或被销毁）
                  </span>
                </span>
              </button>
            )}

            {loading && list.length === 0 ? (
              <div className="px-2 py-3 text-center text-slate-500">加载分身列表…</div>
            ) : list.length === 0 ? (
              <div className="rounded-md border border-slate-800 bg-slate-950/60 px-3 py-3 text-center text-[10.5px] leading-relaxed text-slate-500">
                暂无活跃分身
                <div className="mt-1 text-[10px] text-slate-600">
                  要固定用一个分身，请到「装配车间 → 分身创建与组装」创建后再回来选择。
                </div>
              </div>
            ) : (
              list.map((sa) => {
                const active = sa.name === current
                return (
                  <button
                    key={sa.name}
                    type="button"
                    onClick={() => void pick(sa.name)}
                    disabled={saving}
                    className={`mb-1 flex w-full items-start gap-2 rounded-lg border px-2 py-1.5 text-left transition-colors disabled:opacity-50 ${
                      active
                        ? 'border-cyan-600/60 bg-cyan-500/10'
                        : 'border-slate-800 hover:bg-slate-800/60'
                    }`}
                  >
                    <span className="mt-[1px] w-3 shrink-0 text-cyan-400">
                      {active ? <Check size={12} /> : null}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <b className="truncate font-medium text-slate-200">{sa.name}</b>
                        {sa.status && <em className="not-italic text-[10px] text-slate-500">{sa.status}</em>}
                      </span>
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
                        {sa.model_id || '-'}
                        {sa.memory_provider ? ` · ${sa.memory_provider}` : ''}
                      </span>
                    </span>
                  </button>
                )
              })
            )}
          </div>

          {saving && (
            <div className="mt-1 flex items-center gap-1.5 text-[10px] text-cyan-300">
              <Loader2 size={10} className="animate-spin" /> 切换中…
            </div>
          )}
        </div>
      )}
    </div>
  )
}
