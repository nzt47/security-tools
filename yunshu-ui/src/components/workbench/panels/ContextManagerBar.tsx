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
import { ChevronDown, Gauge, Info, RefreshCw, Minimize2, X } from 'lucide-react'
import { hubGet, Loading } from '../../../pages/hub/components/ui'
// 【P1-front 第七批】GET /api/context/status 已带 X-Envelope: v2，改用显式信封解析。
// 【P1-front 第九批】同页两个 POST（config/compress）也已迁移，一并改用 postEnvelope。
import { getEnvelope, postEnvelope } from '@/api/envelope'

import {
  AGENT_LINES,
  CONTEXT_COMPRESS,
  CONTEXT_CONFIG,
  CONTEXT_STATUS,
  LLM_MONITOR_STATS,
} from '@/api/endpoints';

import { CompressPrefixSwitch } from './CompressPrefixSwitch';
import { Button, ModalBase, Slider } from '@/components/ui';

interface CtxStatus {
  /** 分子 = 本会话最近 percentage_window_messages 条正文（与压缩触发同源） */
  current_tokens: number
  /** 会话「收+发」累计（仅供参考；与压缩触发不是同一个数） */
  session_total_tokens?: number
  percentage_semantics?: string
  percentage_note?: string
  percentage_window_messages?: number
  /** 未分会话历史池（冻结口径） */
  legacy_pool?: {
    available?: boolean
    policy?: string
    legacy_messages?: number
    messages_total?: number
    note?: string
  }
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
  /** 【2026-11-09】这次压缩用的路径：true = 复用了本轮对话前缀（命中价）；false = 独立前缀（全价） */
  used_prefix?: boolean
  prefix_messages?: number
  error?: string
}

/** /api/agent-lines 的消费子集（各分身的默认窗口） */
interface LineListPayload {
  active?: string | null
  lines?: Array<{ id?: string; name?: string; context_window_tokens?: number }>
}

const STORAGE_KEY = 'yunshu_ctx_monitor'

export function ContextManagerBar() {
  const [status, setStatus] = useState<CtxStatus | null>(null)
  const [llm, setLlm] = useState<LlmStats | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  // 说明面板（Owner 2026-11-15：原来常驻的机制说明太占版面 ⇒ 收进「手动压缩」同一行的两个按钮）
  const [infoTab, setInfoTab] = useState<'mechanism' | 'window' | null>(null)
  // 滑块本地值（拖动时不覆盖服务端值）
  const [localLimit, setLocalLimit] = useState<number | null>(null)
  const [localSend, setLocalSend] = useState<number | null>(null)
  const [localRecv, setLocalRecv] = useState<number | null>(null)
  // 【2026-11-15】各分身（主线）的窗口一览：直接读 /api/agent-lines（同一份档案事实，不前端另造）
  const [lines, setLines] = useState<Array<{ id: string; name: string; window: number }>>([])
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

  /* 【2026-11-17 Owner】说明浮层必须**关得掉**（Esc / 点暗底 / 右上角关闭）。
     2026-11-18：这三条路现在由 ui/ModalBase 统一提供（Esc 挂捕获阶段、遮罩点击、closeButton），
     本组件不再自己挂 window 监听 —— 否则两处监听会对同一次 Esc 各关一次。 */

  // 各分身的默认窗口（档案事实；取不到就空表，界面如实说"取不到"，不编数）。
  // 与 5s 水位轮询分开：主线档案不会每 5 秒变一次，60s 足够。
  const pullLines = useCallback(() => {
    hubGet<LineListPayload | { data?: LineListPayload }>(AGENT_LINES).then((r) => {
      const payload = (r as { data?: LineListPayload })?.data ?? (r as LineListPayload)
      setLines((payload?.lines ?? []).map((l) => ({
        id: String(l.id ?? ''),
        name: String(l.name || l.id || ''),
        window: Number(l.context_window_tokens ?? 0),
      })))
    }).catch(() => { /* 读不到就保持空表（界面会如实显示"读不到"，不编数） */ })
  }, [])

  useEffect(() => {
    pullLines()
    const t = setInterval(pullLines, 60000)
    return () => clearInterval(t)
  }, [pullLines])

  // 【2026-11-15】打开「窗口取值参考」时**立刻重取一次**：组件可能在本页加载早期就挂载了
  // （那时令牌还没进 localStorage ⇒ 401 ⇒ 表为空），只靠 60s 轮询会让人以为"没有这个功能"。
  useEffect(() => {
    if (infoTab === 'window') pullLines()
  }, [infoTab, pullLines])

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
      if (d.ok) {
        // 路径可核实：开了复用就直接说，别让人去翻日志（前缀 = 命中价；独立 = 全价）
        const path = d.used_prefix
          ? `复用对话前缀（${d.prefix_messages ?? 0} 条同前缀消息，命中价）`
          : '独立前缀（全价；开关关 / 前缀过期 / 模型不一致 / 装不下窗口）'
        flashMsg(`已压缩：摘要版本 v${d.summary_version ?? '?'}（并入 ${d.messages_compressed ?? 0} 条消息；${path}）`, 6000)
      }
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
      <Button onClick={() => persistExpanded(!expanded)} className="w-full" title="点击展开/折叠上下文监视器" variant="default" size="xs">
        <Gauge size={12} className="shrink-0 text-cyan-400" />
        <span className="shrink-0 font-medium">上下文</span>
        {/* 水位进度条 */}
        <div className="h-1.5 min-w-[60px] max-w-[140px] flex-1 overflow-hidden rounded-full bg-slate-800">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
        <span className={`shrink-0 font-mono text-micro ${pct >= 80 ? 'text-red-400' : pct >= 60 ? 'text-amber-400' : 'text-slate-300'}`}>
          {pctKnown ? `${pct.toFixed(0)}%` : '—%'}
        </span>
        <span className="hidden shrink-0 font-mono text-micro text-slate-500 sm:inline">{fmt(status?.current_tokens)}/{fmt(status?.token_limit)}</span>
        {llm?.tok_per_s != null && (
          <span className="hidden shrink-0 font-mono text-micro text-cyan-500 md:inline">{Number(llm.tok_per_s).toFixed(0)} tok/s</span>
        )}
        {llm?.cache_hit_ratio != null && (
          <span
            className="hidden shrink-0 font-mono text-micro text-emerald-500 lg:inline"
            title={`前缀缓存命中 ${(llm.cache_hit_ratio * 100).toFixed(1)}% · 命中 ${fmt(llm.cache_hit_tokens)} / 计 ${fmt((llm.cache_hit_tokens ?? 0) + (llm.cache_miss_tokens ?? 0))} tok`}
          >
            缓存 {(llm.cache_hit_ratio * 100).toFixed(0)}%
          </span>
        )}
        {status && (status.compress_rounds_window ?? status.compress_rounds) > 0 && (
          <span
            className="hidden shrink-0 text-micro text-slate-600 md:inline"
            title={`当前窗口压缩 ${status.compress_rounds_window ?? status.compress_rounds} 次（累计摘要版本 ${status.compress_rounds}）`}
          >
            压缩 {status.compress_rounds_window ?? status.compress_rounds} 次
          </span>
        )}
        <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </Button>

      {/* 展开面板 */}
      {expanded && (
        <div className="space-y-3 border-t border-slate-800/70 px-4 py-3">
          {error && <div className="text-caption text-red-400">状态加载失败：{error}</div>}
          {msg && <div className="rounded-md border border-slate-700 bg-slate-800/60 px-2.5 py-1.5 text-caption text-cyan-400">{msg}</div>}
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
                    <div className="mb-1 text-micro font-medium text-cyan-300">{g.title}</div>
                    <div className="space-y-0.5">
                      {g.rows.map(([label, value]) => (
                        <div key={label} className="flex items-center justify-between gap-2 text-caption">
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
                    <div className="text-micro text-slate-500">{s.l}</div>
                    <div className={`font-mono text-sm font-semibold ${s.c}`}>{s.v}</div>
                  </div>
                ))}
              </div>

              {/* 窗口来源与成因（读数必须可复核：分母从哪来、红色是因为什么） */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-slate-500">
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
                <div className="mb-1 flex justify-between text-micro text-slate-500">
                  <span>收 {fmt(status.recv_tokens)} tok · 发 {fmt(status.send_tokens)} tok</span>
                  <span>阈值 {Math.round(status.compress_threshold * 100)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                  <div className={`h-full ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                </div>
                {/* 【2026-11-09】口径同源：占用率 = 最近 N 条正文 token ÷ 窗口（与压缩触发同一口径，
                    工具结果不计入）；会话累计单列，避免把「累计」误读成「这一窗用了多少」。 */}
                <div className="mt-1 text-micro leading-relaxed text-slate-500">
                  {status.percentage_note
                    ? status.percentage_note
                    : `口径：本会话最近 ${status.percentage_window_messages ?? 200} 条正文 token（与压缩触发同源；工具结果不计入）`}
                  {status.session_total_tokens != null && (
                    <> · 会话累计 <span className="font-mono">{fmt(status.session_total_tokens)}</span> tok</>
                  )}
                </div>
                {/* 【2026-11-10】未分会话历史池：会话分家前的无主消息按「冻结」披露（L5：如实标无数据/冻结，
                    不折算成 0，也不藏起来让人以为压缩漏了它们）。 */}
                {(status.legacy_pool?.legacy_messages ?? 0) > 0 && (
                  <div className="mt-1 text-micro leading-relaxed text-slate-500"
                    title={status.legacy_pool?.note}>
                    未分会话历史池 <span className="font-mono">{fmt(status.legacy_pool?.legacy_messages ?? 0)}</span> 条 · 已冻结（不参与压缩/摘要/最近消息）
                  </div>
                )}
              </div>

              {/* 【2026-11-05】65% 换会话提示：到 80% 才会自动压缩，压完早期细节只剩摘要，
                  所以在 65% 就提醒一次（不阻断、不自动新建会话）。 */}
              {pct >= 65 && pct < 80 && (
                <div className="rounded-md border border-amber-800/60 bg-amber-500/10 px-2.5 py-1.5 text-caption leading-relaxed text-amber-200">
                  上下文已到 <b>{Math.round(pct)}%</b>：建议<b>生成交接文档并转入新会话</b>继续 ——
                  到 {Math.round(status.compress_threshold * 100)}% 会自动压缩，压缩后早期细节只剩摘要。
                  入口：会话头「交接文档」。
                </div>
              )}
              {pct >= 80 && (
                <div className="rounded-md border border-rose-800/60 bg-rose-500/10 px-2.5 py-1.5 text-caption leading-relaxed text-rose-200">
                  上下文已到 <b>{Math.round(pct)}%</b>（超过压缩阈值）：现在生成交接文档再换会话，比等到压缩后更划算。
                </div>
              )}

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
                      <span className="text-micro text-slate-500">{s.l}</span>
                      <span className="font-mono text-caption text-cyan-400">{fmt(s.val)}</span>
                    </div>
                    <Slider
                      min={s.min} max={s.max} step={s.step}
                      value={s.val}
                      onValueChange={s.set}
                      aria-label="预算比例"
                    />
                    <div className="mt-0.5 text-micro leading-tight text-slate-600">{s.hint}</div>
                  </div>
                ))}
              </div>

              {/* 操作 */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Button variant="primary" size="sm" onClick={manualCompress} disabled={busy}>
                    <Minimize2 size={11} /> {busy ? '压缩中…' : '手动压缩'}
                  </Button>
                  {/* 【2026-11-09】压缩复用对话前缀：与「手动压缩」同一行 —— 按下去压什么、
                      这一次压缩怎么算钱，两个问题在同一处可见（开关中心是同一份登记表）。 */}
                  <CompressPrefixSwitch />
                  {/* 【2026-11-15 Owner】原来常驻在面板底部的「上下文管理机制」太占版面 ⇒ 收成按钮，
                      与「手动压缩」同一行（和 §11.123 的开关同一处：按下去压什么、规则在哪，一屏可见）。
                      第二个按钮是「窗口取值参考」：按任务类型的建议值 + 我们这里的真实约束（全局一个窗口、
                      分身影子目前没有窗口字段）。 */}
                  <Button
                    size="xs"
                    variant="default"
                    active={infoTab === 'mechanism'}
                    onClick={() => setInfoTab((v) => (v === 'mechanism' ? null : 'mechanism'))}
                    aria-expanded={infoTab === 'mechanism'}
                    title="自动压缩 / 三级水位 / 摘要退化 / System Prompt 预算 / 主动丢弃 —— 这套机制怎么运作"
                  >
                    <Info size={11} /> 上下文管理说明
                  </Button>
                  <Button
                    size="xs"
                    variant="default"
                    active={infoTab === 'window'}
                    onClick={() => setInfoTab((v) => (v === 'window' ? null : 'window'))}
                    aria-expanded={infoTab === 'window'}
                    aria-label="窗口取值参考"
                    title="窗口该开多大：按任务类型的建议值 + 本机的真实约束（全局一个窗口；分身不各带窗口）"
                  >
                    <Info size={11} /> 窗口取值参考
                  </Button>
                  <Button onClick={load} variant="default" size="xs">
                    <RefreshCw size={11} /> 刷新
                  </Button>
                </div>
                <span className="text-micro text-slate-600">压缩为写操作，需 FLASK_API_TOKEN</span>
              </div>

              {/* 说明面板（Owner 2026-11-15）：原来常驻的机制正文收进「手动压缩」同一行的按钮。
                  两个入口各自只讲一件事：① 这套自动策略怎么运作 ② 窗口该开多大 + 我们这里的真实约束。 */}
              {/* 【2026-11-17 Owner 第三次反馈「窗口取值参考」还是看不全】根因不是高度不够，而是**定位错了**：
                  面板在 mosaic 面板内部，祖先有 overflow 裁切；而 max-h-[70vh] 是相对**视口**算的
                  （630px @900 高），永远大于祖先给它的可用高度（实测 573px）⇒ 这个上限从未生效，
                  盒子被祖先裁短、内容既看不全也不会出现滚动条（scrollH == clientH 的假绿就是这么来的）。
                  ⇒ 两个说明面板改成 **fixed 浮层**（inset-4 起算、自己的滚动条），祖先裁不到它。
                  判据见 .local/verify_window_ref.py：浮层矩形完全在视口内 + 滚到底能看见最后一行。 */}
              {/* 【2026-11-17 实测】浮层必须 **portal 到 body**：留在 mosaic 子树里时，
                  它虽然 z-50，但外层 .workbench-root（z-auto）下的 header.wb-topbar（z-10）
                  是另一个层叠上下文、按顺序画在它上面 ⇒ 顶部那条点不到暗底（elementFromPoint
                  命中 header，点暗底自然关不掉）。portal 出去后层叠次序与祖先无关。 */}
              {/* 【2026-11-18 封装收口】说明浮层的外壳交给 ui/ModalBase（portal + 遮罩 + Esc 捕获 +
                  滚动锁定 + × 关闭），这里只留内容。 */}
              {infoTab === 'mechanism' && (
                <ModalBase
                  open
                  onClose={() => setInfoTab(null)}
                  title="上下文管理说明"
                  header={<span className="flex items-center gap-1.5"><Info size={11} className="text-primary" /> 上下文管理机制（多层次自动策略，无需手动干预）</span>}
                  width="max-w-3xl"
                  scrollable
                  closeButton
                  bodyClassName="space-y-2"
                >
                <div className="text-caption leading-relaxed text-slate-300">
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                    <span className="text-cyan-400">① 异步后台压缩</span>：每次对话后检查 token 总量，
                    使用率超过阈值（<b className="text-slate-100">{Math.round(status.compress_threshold * 100)}%</b>）时，
                    在后台自动把<b className="text-slate-100">旧对话压缩为摘要</b>，保留关键决策与结论。
                    当前窗口已压缩 <b className="text-slate-100">{status.compress_rounds_window ?? status.compress_rounds} 次</b>
                    （累计摘要版本 <b className="text-slate-100">{status.compress_rounds}</b>）。
                    压缩<b className="text-slate-100">不会降低水位</b>（消息不删，只并进摘要），
                    所以两次压缩之间正文需再长 10% 才会触发第二次 —— 否则过了阈值会每条消息压一次。
                  </div>
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                    <span className="text-cyan-400">② 三级水位预警</span>：
                    🟢 &lt;60% 正常 · 🟡 60%~80% 接近阈值 · 🔴 ≥80% 即将溢出。
                  </div>
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                    <span className="text-cyan-400">③ 摘要退化检测</span>：每次压缩都会损失细节，
                    <b className="text-slate-100">累计压缩 ≥3 次</b>质量开始下降、<b className="text-slate-100">≥5 次</b>退化明显；
                    判据用的是<b className="text-slate-100">累计值</b>（退化是累积效应，与当前占用无关），
                    故可能「占用很低但成因显示摘要退化」。
                  </div>
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                    <span className="text-cyan-400">④ System Prompt 预算保护</span>：System prompt 有
                    <b className="text-slate-100"> 10000 tokens</b> 的预算上限，超限时自动截断工具状态列表，保证核心指令完整。
                  </div>
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                    <span className="text-cyan-400">⑤ 主动丢弃</span>：即使压缩后仍超限，系统会从
                    <b className="text-slate-100">最旧的非摘要消息</b>开始丢弃，直到满足 token_limit。
                  </div>
                  <div className="text-micro text-slate-500">
                    提示：水位长期 ≥80% 或摘要严重退化时，建议新建会话继续（系统不会自动替你新建会话）。
                  </div>
                </div>
                </ModalBase>
              )}

              {/* 【2026-11-18 封装收口】同上：外壳交给 ui/ModalBase。原来的注释保留在这里：
                  说明面板曾"无界高度"被祖先 overflow 裁掉；max-h-[70vh] 治不好（vh 相对视口）；
                  现在由 ModalBase 的 scrollable（max-h 85vh + 正文自滚）承担。 */}
              {infoTab === 'window' && (
                <ModalBase
                  open
                  onClose={() => setInfoTab(null)}
                  title="窗口取值参考"
                  header={<span className="flex items-center gap-1.5"><Info size={11} className="text-primary" /> 窗口该开多大（建议值 + 我们这里的真实约束）</span>}
                  width="max-w-3xl"
                  scrollable
                  closeButton
                  bodyClassName="space-y-2"
                >

                  {/* ① 本机的真实数值（全部来自后端读数，不写死） */}
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5 text-caption leading-relaxed text-slate-300">
                    <span className="text-cyan-400">① 现在生效的窗口</span>：
                    <b className="text-slate-100"> {fmt(status.token_limit)}</b> tok
                    （来源 <span className="font-mono text-slate-400">{status.token_limit_source ?? '-'}</span>
                    {String(status.token_limit_source ?? '').startsWith('line:')
                      ? ' —— 这是当前分身档案里的默认值'
                      : String(status.token_limit_source ?? '').startsWith('runtime_override')
                        ? ' —— 你在面板上调过，进程内生效'
                        : ' —— 没有任何覆盖，用内置默认'}
                    ；面板滑块 = **运行时覆盖**，可调 8K–1M，重启回落）· 到
                    <b className="text-slate-100"> {Math.round(status.compress_threshold * 100)}% </b>
                    自动压缩 · 单次发送上限 <b className="text-slate-100">{fmt(status.per_message_send_limit)}</b>（超限只告警不截断）·
                    单次回复 <b className="text-slate-100">{fmt(status.per_message_recv_limit)}</b> tok。
                  </div>

                  {/* ② 按任务的建议起点（通用经验值，**不是本机配置**） */}
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5 text-caption leading-relaxed text-slate-300">
                    <div className="text-cyan-400">② 按任务类型的建议起点（通用经验，不是本机配置）</div>
                    <div className="mt-1 space-y-0.5">
                      {[
                        ['日常对话', '8K–16K', '历史太多会稀释注意力'],
                        ['写作 / 单篇总结', '16K–32K', '覆盖整篇，又避开中段衰减'],
                        ['代码 Agent / 跨文件理解', '32K–64K', '需要上下文，但推理比检索更怕噪声'],
                        ['RAG 检索问答', '64K–128K', '关键仍是检索质量，不是无限塞上下文'],
                        ['合同 / 论文 / 整书分析', '128K+', '仍需分章处理，别一次性塞满'],
                      ].map(([task, win, why]) => (
                        <div key={task} className="flex flex-wrap items-baseline gap-x-2">
                          <span className="text-slate-200">{task}</span>
                          <span className="font-mono text-cyan-300">{win}</span>
                          <span className="text-slate-500">{why}</span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-1 text-micro text-slate-500">
                      用法：先按任务选一个起点，再按下面的真实约束往下压 —— 表格是起点，不是配额。
                    </div>
                  </div>

                  {/* ③ 工程要点（都对着本机的实测数字写） */}
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5 text-caption leading-relaxed text-slate-300">
                    <div className="text-cyan-400">③ 工程要点（对我们意味着什么）</div>
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-slate-400">
                      <li>
                        <b className="text-slate-200">标称长度 ≠ 有效长度</b>：deepseek-flash 标称 1,048,576，
                        实际能稳定利用的更低 —— 要用 RULER / NoLiMa / 大海捞针这类基准测，别拿标称当预算。
                      </li>
                      <li>
                        <b className="text-slate-200">越大 ≠ 越准</b>：信息越多注意力越稀释，关键内容放在中段最容易丢。
                        「装什么」优先于「装多少」：检索 / 摘要 / 滑动窗口 / 任务分步能解决的，不必上超大窗口。
                      </li>
                      <li>
                        <b className="text-slate-200">算力随长度近似 O(n²)</b>：长上下文显著抬高首 token 延迟与成本。
                        本机实测一次真实请求 <b className="text-slate-100">8.8k tok</b>，其中 tools 段约 7.6k、
                        前缀缓存命中 <b className="text-slate-100">96.7%</b> ⇒ 砍 tools 省的是**上下文窗口**，几乎不省钱。
                      </li>
                      <li>
                        <b className="text-slate-200">Agent 要做 token 预算</b>：系统指令 / 工具输出 / 检索片段 / 历史
                        分别设上限，超了就压缩或丢弃 —— 本面板 ①②⑤ 就是这套（压缩 → 水位预警 → 主动丢弃）。
                      </li>
                    </ul>
                  </div>

                  {/* ④ 分身：已经做了（2026-11-15）—— 给出**各分身窗口一览**（读 /api/agent-lines，不手抄） */}
                  <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5 text-caption leading-relaxed text-slate-300">
                    <span className="text-cyan-400">④ 按智能体分身分窗（已支持）</span>：每个分身档案带
                    <b className="text-slate-100"> context_window_tokens</b>，切激活主线即换默认。各分身当前声明值：
                    {lines.length === 0 ? (
                      <span className="text-slate-500">（读不到 /api/agent-lines，未列出 —— 不编数）</span>
                    ) : (
                      <div className="mt-1 grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
                        {lines.map((l) => {
                          const isLine = String(status.token_limit_source ?? "") === `line:${l.id}(${l.window})`
                          return (
                            <div key={l.id} className="flex flex-wrap items-baseline gap-x-2">
                              <span className={isLine ? "text-cyan-300" : "text-slate-300"}>{l.name}</span>
                              <span className="font-mono text-slate-400">{l.id}</span>
                              <span className="font-mono text-cyan-300">
                                {l.window > 0 ? Math.round(l.window / 1024) + "K" : "未声明"}
                              </span>
                              {isLine && <span className="text-micro text-emerald-400">← 当前生效</span>}
                            </div>
                          )
                        })}
                      </div>
                    )}
                    <div className="mt-1 text-slate-400">
                      优先级：<b className="text-slate-100">面板/接口的运行时覆盖</b>（你在滑块上调的值，进程内生效）
                      &gt; <b className="text-slate-100">本分身的默认值</b>（line:…，重启后回到它） &gt;
                      内置默认 131,072。切换主线时不会覆盖你手调过的值。
                    </div>
                    <div className="mt-1 text-micro text-slate-500">
                      改分身默认：工作台 → 智能体主线（主线档案页，列表里有「窗口 …」徽章、编辑区有 context_window_tokens 字段）；改本机当前值：用上面的滑块。
                    </div>
                  </div>
                </ModalBase>
              )}

            </>
          )}
        </div>
      )}
    </div>
  )
}