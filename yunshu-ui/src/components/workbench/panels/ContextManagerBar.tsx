/**
 * 上下文管理器 —— 工作台版（从 legacy ContextMonitor 移植，适配深色科技风）
 * ------------------------------------------------
 * 功能：只读展示上下文 token 水位/压缩次数；滑块调节
 *       token_limit / send / recv（松手防抖保存）；手动压缩；机制说明。
 * 注：输入框"滑杆遮挡"问题为 textarea 滚动条所致，已通过按钮外置+增高上限修复，
 *     与本组件无关（滑块保留）。
 * 数据源：/api/context/status、/api/context/config、/api/context/compress，
 *         以及 /api/llm-monitor/stats（会话级读数：轮数 / 调用频数 / tok·s⁻¹ / 总 tokens / 缓存命中）
 * 注：config/compress 写操作需 FLASK_API_TOKEN（401 时提示）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Gauge, RefreshCw, Minimize2 } from 'lucide-react'
import { Loading, hubGet } from '../../../pages/hub/components/ui'
// 【P1-front 第七批】GET /api/context/status 已带 X-Envelope: v2，改用显式信封解析。
// 【P1-front 第九批】同页两个 POST（config/compress）也已迁移，一并改用 postEnvelope。
import { getEnvelope, postEnvelope } from '@/api/envelope'

import {
  CONTEXT_COMPRESS,
  CONTEXT_CONFIG,
  CONTEXT_STATUS,
  LLM_MONITOR_STATS,
} from '@/api/endpoints';

interface CtxStatus {
  current_tokens: number
  /** 分母 = **编排窗口**（真正生效的那个）；后端取不到时是 null，界面必须如实显示 — */
  token_limit: number | null
  token_limit_source?: string
  configured_token_limit?: number
  configured_token_limit_note?: string
  percentage: number | null
  per_message_send_limit: number
  per_message_recv_limit: number
  /** 发送侧语义：warn_only = 超限**只告警不截断** */
  send_limit_semantics?: string
  compress_threshold: number
  compress_rounds: number
  compress_degraded?: boolean
  status_level: string
  status_reasons?: string[]
  send_tokens: number
  recv_tokens: number
  messages_count: number
  /** 轮数 = 用户发言条数（一次发言 = 一轮） */
  turns?: number
  /** 【2026-10-14 对齐 DSH】当前窗口（本会话基线之上）的压缩次数 */
  compress_rounds_window?: number
  /** 【2026-10-14】会话统计：模型用时 / 工具调用用时 / 首 token 平均（毫秒） */
  session_stats?: {
    turns?: number
    model_ms?: number
    tool_ms?: number
    ttft_ms?: number | null
    ttft_samples?: number
  }
  /** 【2026-10-14】最近一轮的上下文装配明细（系统提示词 / 工具定义 / 对话消息） */
  context_breakdown?: {
    window_tokens?: number | null
    system_tokens?: number
    tools_tokens?: number | null
    history_tokens?: number | null
  }
}

/** 会话级 LLM 读数（/api/llm-monitor/stats 的消费子集） */
interface LlmStats {
  /** 调用频数（LLM 调用次数，含工具循环/摘要） */
  total?: number
  total_tokens?: number
  total_response_tokens?: number
  /** 生成吞吐（输出 token / 总耗时） */
  tok_per_s?: number
  cache_hit_tokens?: number
  cache_miss_tokens?: number
  /** 前缀缓存命中率；API 未上报缓存字段时为 null */
  cache_hit_ratio?: number | null
  cache_reported_calls?: number
}

/** POST /api/context/compress 的响应体（【2026-10-14】不再有 freed_tokens：压缩不删消息，
 *  释放量不可核实 ⇒ 后端改报摘要版本与并入消息数） */
interface CompressResp {
  ok?: boolean
  summary_version?: number
  version_before?: number
  messages_compressed?: number
  error?: string
}

const STORAGE_KEY = 'yunshu_ctx_monitor'

export function ContextManagerBar() {
  const [status, setStatus] = useState<CtxStatus | null>(null)
  const [llm, setLlm] = useState<LlmStats | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  // 滑块本地值（拖动时不覆盖服务端值）
  const [localLimit, setLocalLimit] = useState<number | null>(null)
  const [localSend, setLocalSend] = useState<number | null>(null)
  const [localRecv, setLocalRecv] = useState<number | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const msgTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    // 显式信封解析：data 就是状态对象本身（原来靠 pickObj 猜形状）
    getEnvelope<CtxStatus>(CONTEXT_STATUS).then((d) => {
      setStatus(d)
      setError('')
    }).catch((e) => setError(String(e)))
    // 会话级 LLM 读数（tok/s、缓存命中、调用频数、总 tokens）：与上下文状态同轮询。
    // 该端点走裸 JSON（非信封）⇒ 用 hubGet 并兼容 data 包裹形态。
    hubGet<LlmStats | { data?: LlmStats }>(LLM_MONITOR_STATS).then((r) => {
      setLlm((r as { data?: LlmStats })?.data ?? (r as LlmStats) ?? null)
    }).catch(() => { /* 读数失败不打断上下文水位展示 */ })
  }, [])

  useEffect(() => {
    load()
    // 5s 轮询（与旧版对齐）
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
      if (saved.expanded != null) setExpanded(saved.expanded)
    } catch { /* ignore */ }
  }, [])

  const persistExpanded = (v: boolean) => {
    setExpanded(v)
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
      saved.expanded = v
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved))
    } catch { /* ignore */ }
  }

  const flashMsg = (m: string, ms = 3000) => {
    if (msgTimer.current) clearTimeout(msgTimer.current)
    setMsg(m)
    msgTimer.current = setTimeout(() => setMsg(''), ms)
  }

  // 保存配置（滑块松手防抖 300ms 后写 /api/context/config）
  const scheduleSave = (patch: Record<string, number>) => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      postEnvelope(CONTEXT_CONFIG, patch).then(() => {
        flashMsg('上下文配置已保存')
      }).catch((e) => {
        const m = e instanceof Error ? e.message : String(e)
        flashMsg(m.includes('401') ? '保存需配置 FLASK_API_TOKEN（仅本地界面演示）' : `保存失败：${m}`, 3500)
      })
    }, 300)
  }

  const manualCompress = async () => {
    if (busy) return
    setBusy(true)
    setMsg('')
    try {
      // 【2026-10-14】后端不再回报 freed_tokens（压缩不删消息，释放量不可核实）；
      // 改报「摘要版本 + 并入消息数」—— 面板只展示能被核实的东西。
      const d = await postEnvelope<CompressResp>(CONTEXT_COMPRESS)
      if (d.ok) flashMsg(`已压缩：摘要版本 v${d.summary_version ?? '?'}（并入 ${d.messages_compressed ?? 0} 条消息）`)
      else if (d.error) flashMsg(`压缩失败：${d.error}`, 3500)
      else flashMsg('本轮没有可压缩的消息（摘要未变更）')
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      flashMsg(m.includes('401') ? '压缩需配置 FLASK_API_TOKEN' : `压缩失败：${m}`, 3500)
    } finally {
      setBusy(false)
      load()
    }
  }

  // percentage 可能为 null（后端取不到真实窗口时**如实报 None**，不拿硬编码冒充分母）
  const pct = status?.percentage ?? 0
  const pctKnown = status?.percentage != null
  const barColor = pct >= 80 ? 'bg-red-500' : pct >= 60 ? 'bg-amber-400' : 'bg-cyan-500'
  const fmt = (n?: number | null) => (n != null ? n.toLocaleString() : '-')
  const reasons = status?.status_reasons ?? []
  // 【2026-10-14 对齐 DSH】三组读数的取数（取不到就显示 —，不拿占位数字冒充）
  const ss = status?.session_stats ?? {}
  const bk = status?.context_breakdown ?? {}
  const msText = (ms?: number | null) =>
    ms == null ? '—'
      : ms >= 60000 ? `${(ms / 60000).toFixed(1)} min`
        : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s`
          : `${Math.round(ms)} ms`

  return (
    <div className="border-t border-slate-800 bg-slate-900/40">
      {/* 折叠条（纯状态展示，无滑块） */}
      <button
        onClick={() => persistExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-[11px] text-slate-400 transition-colors hover:bg-slate-800/40"
        title="点击展开/折叠上下文监视器"
      >
        <Gauge size={12} className="shrink-0 text-cyan-400" />
        <span className="shrink-0 font-medium">上下文</span>
        {/* 水位进度条 */}
        <div className="h-1.5 min-w-[60px] max-w-[140px] flex-1 overflow-hidden rounded-full bg-slate-800">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
        <span className={`shrink-0 font-mono text-[10px] ${pct >= 80 ? 'text-red-400' : pct >= 60 ? 'text-amber-400' : 'text-slate-300'}`}>
          {pctKnown ? `${pct.toFixed(0)}%` : '—%'}
        </span>
        <span className="hidden shrink-0 font-mono text-[10px] text-slate-500 sm:inline">{fmt(status?.current_tokens)}/{fmt(status?.token_limit)}</span>
        {llm?.tok_per_s != null && (
          <span className="hidden shrink-0 font-mono text-[10px] text-cyan-500 md:inline">{Number(llm.tok_per_s).toFixed(0)} tok/s</span>
        )}
        {llm?.cache_hit_ratio != null && (
          <span
            className="hidden shrink-0 font-mono text-[10px] text-emerald-500 lg:inline"
            title={`前缀缓存命中 ${(llm.cache_hit_ratio * 100).toFixed(1)}% · 命中 ${fmt(llm.cache_hit_tokens)} / 计 ${fmt((llm.cache_hit_tokens ?? 0) + (llm.cache_miss_tokens ?? 0))} tok`}
          >
            缓存 {(llm.cache_hit_ratio * 100).toFixed(0)}%
          </span>
        )}
        {status && (status.compress_rounds_window ?? status.compress_rounds) > 0 && (
          <span
            className="hidden shrink-0 text-[10px] text-slate-600 md:inline"
            title={`当前窗口压缩 ${status.compress_rounds_window ?? status.compress_rounds} 次（累计摘要版本 ${status.compress_rounds}）`}
          >
            压缩 {status.compress_rounds_window ?? status.compress_rounds} 次
          </span>
        )}
        <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>

      {/* 展开面板 */}
      {expanded && (
        <div className="space-y-3 border-t border-slate-800/70 px-4 py-3">
          {error && <div className="text-[11px] text-red-400">状态加载失败：{error}</div>}
          {msg && <div className="rounded-md border border-slate-700 bg-slate-800/60 px-2.5 py-1.5 text-[11px] text-cyan-400">{msg}</div>}
          {!status ? <Loading text="加载上下文状态…" /> : (
            <>
              {/* ── DeepSeek Harness 同构读数（2026-10-14）──────────────────────
                  三组：会话统计 / Token 用量 / 上下文已用。取数口径：
                    · 会话统计   ← status.session_stats（后端流式主循环每轮实测累计）
                    · Token 用量 ← /api/llm-monitor/stats（前缀缓存命中/未命中/输出）
                    · 上下文已用 ← status.context_breakdown（最近一轮装配明细）
                  任一项取不到就显示 —，**不拿占位数字冒充**（本仓纪律）。 */}
              <div className="grid gap-2 sm:grid-cols-3">
                {([
                  {
                    title: '会话统计',
                    rows: [
                      ['模型用时', msText(ss.model_ms)],
                      ['工具调用用时', msText(ss.tool_ms)],
                      ['首 token 平均（TTFT）', ss.ttft_ms != null ? `${Math.round(ss.ttft_ms)} ms` : '—'],
                      ['输出速度（TPS）', llm?.tok_per_s != null ? `${Number(llm.tok_per_s).toFixed(1)} tok/s` : '—'],
                    ],
                  },
                  {
                    title: 'Token 用量',
                    rows: [
                      ['缓存命中', llm?.cache_hit_ratio != null ? `${(llm.cache_hit_ratio * 100).toFixed(0)}%` : '未上报'],
                      ['未缓存输入', fmt(llm?.cache_miss_tokens)],
                      ['缓存读取', fmt(llm?.cache_hit_tokens)],
                      ['输出', fmt(llm?.total_response_tokens)],
                    ],
                  },
                  {
                    title: '上下文已用',
                    rows: [
                      ['系统提示词', bk.system_tokens != null ? `${fmt(bk.system_tokens)} tok` : '—'],
                      ['工具定义', bk.tools_tokens != null ? `${fmt(bk.tools_tokens)} tok` : '—'],
                      ['对话消息', bk.history_tokens != null ? `${fmt(bk.history_tokens)} tok` : `${fmt(status.current_tokens)} tok`],
                    ],
                  },
                ] as { title: string; rows: [string, string][] }[]).map((g) => (
                  <div key={g.title} className="rounded-lg border border-slate-800 bg-slate-900/60 px-2.5 py-2">
                    <div className="mb-1 text-[10px] font-medium text-cyan-300">{g.title}</div>
                    <div className="space-y-0.5">
                      {g.rows.map(([label, value]) => (
                        <div key={label} className="flex items-center justify-between gap-2 text-[11px]">
                          <span className="text-slate-500">{label}</span>
                          <span className="font-mono text-slate-200">{value}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              {/* 窗口概览（水位/压缩/轮数/调用频数） */}
              <div className="grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
                {[
                  { l: '窗口已用', v: pctKnown ? `${pct.toFixed(0)}%` : '—', c: pct >= 80 ? 'text-red-400' : pct >= 60 ? 'text-amber-400' : 'text-emerald-400' },
                  { l: '窗口 Token', v: `${fmt(status.current_tokens)}/${fmt(status.token_limit)}`, c: 'text-slate-200' },
                  { l: '轮数', v: fmt(status.turns), c: 'text-slate-200' },
                  { l: '当前窗口压缩', v: `${fmt(status.compress_rounds_window ?? status.compress_rounds)} 次`, c: 'text-cyan-400' },
                ].map((s) => (
                  <div key={s.l} className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5">
                    <div className="text-[10px] text-slate-500">{s.l}</div>
                    <div className={`font-mono text-sm font-semibold ${s.c}`}>{s.v}</div>
                  </div>
                ))}
              </div>

              {/* 窗口来源与成因（读数必须可复核：分母从哪来、红色是因为什么） */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
                <span>
                  窗口来源：<span className="font-mono text-slate-400">{status.token_limit_source ?? '-'}</span>
                </span>
                {status.configured_token_limit != null && status.configured_token_limit !== status.token_limit && (
                  <span title={status.configured_token_limit_note}>
                    已保存值 <span className="font-mono text-slate-400">{fmt(status.configured_token_limit)}</span>
                  </span>
                )}
                {reasons.length > 0 && (
                  <span className={reasons.some((r) => r === 'usage_critical' || r === 'usage_high') ? 'text-amber-400' : 'text-slate-500'}>
                    成因：{reasons.map((r) => (r === 'summary_degraded' ? '摘要退化（累计压缩次数多，与占用无关）' : r === 'summary_warn' ? '摘要质量下降' : r === 'usage_critical' ? '占用 ≥95%' : r === 'usage_high' ? '占用 ≥80%' : r)).join(' · ')}
                  </span>
                )}
              </div>

              {/* 进度明细（只读） */}
              <div>
                <div className="mb-1 flex justify-between text-[10px] text-slate-500">
                  <span>收 {fmt(status.recv_tokens)} tok · 发 {fmt(status.send_tokens)} tok</span>
                  <span>阈值 {Math.round(status.compress_threshold * 100)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                  <div className={`h-full ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                </div>
              </div>

              {/* 控制面板：滑块（松手 300ms 后自动保存） */}
              <div className="grid gap-2 sm:grid-cols-3">
                {[
                  // 值域对齐真实模型能力（deepseek-flash 实测 context_window=1048576 /
                  // max_output_tokens=393216）；原先 20000 / 8192 的上限让"想调大也调不动"。
                  { l: '最大 Token', hint: '上下文窗口上限；超限丢弃最旧消息（改完当场生效，重启后回落代码默认值；config.yaml 不参与运行时合成）', val: localLimit ?? status.token_limit ?? 131072, min: 8192, max: 1048576, step: 8192, set: (v: number) => { setLocalLimit(v); scheduleSave({ token_limit: v }) } },
                  { l: '单次发送', hint: '单条消息最大 token —— **超限只告警，不截断原文**', val: localSend ?? status.per_message_send_limit, min: 1024, max: 131072, step: 1024, set: (v: number) => { setLocalSend(v); scheduleSave({ per_message_send_limit: v }) } },
                  { l: '单次回复', hint: '单次回复的 max_tokens（真实生效，受模型能力约束）', val: localRecv ?? status.per_message_recv_limit, min: 1024, max: 131072, step: 1024, set: (v: number) => { setLocalRecv(v); scheduleSave({ per_message_recv_limit: v }) } },
                ].map((s) => (
                  <div key={s.l} className="rounded-lg border border-slate-800 bg-slate-900/50 px-2.5 py-2">
                    <div className="mb-1 flex items-center justify-between">
                      <span className="text-[10px] text-slate-500">{s.l}</span>
                      <span className="font-mono text-[11px] text-cyan-400">{fmt(s.val)}</span>
                    </div>
                    <input
                      type="range" min={s.min} max={s.max} step={s.step}
                      value={s.val}
                      onChange={(e) => s.set(Number(e.target.value))}
                      className="w-full accent-cyan-500"
                    />
                    <div className="mt-0.5 text-[9px] leading-tight text-slate-600">{s.hint}</div>
                  </div>
                ))}
              </div>

              {/* 操作 */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <button
                    onClick={manualCompress}
                    disabled={busy}
                    className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-[11px] text-white hover:bg-blue-500 disabled:opacity-40"
                  >
                    <Minimize2 size={11} /> {busy ? '压缩中…' : '手动压缩'}
                  </button>
                  <button onClick={load} className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800">
                    <RefreshCw size={11} /> 刷新
                  </button>
                </div>
                <span className="text-[9px] text-slate-600">压缩为写操作，需 FLASK_API_TOKEN</span>
              </div>

            </>
          )}
        </div>
      )}
    </div>
  )
}
