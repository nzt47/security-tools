/**
 * 上下文管理器 —— 工作台版（从 legacy ContextMonitor 移植，适配深色科技风）
 * ------------------------------------------------
 * 功能：只读展示上下文 token 水位/压缩次数；滑块调节
 *       token_limit / send / recv（松手防抖保存）；手动压缩；机制说明。
 * 注：输入框"滑杆遮挡"问题为 textarea 滚动条所致，已通过按钮外置+增高上限修复，
 *     与本组件无关（滑块保留）。
 * 数据源：/api/context/status、/api/context/config、/api/context/compress
 * 注：config/compress 写操作需 FLASK_API_TOKEN（401 时提示）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Gauge, HelpCircle, RefreshCw, Minimize2 } from 'lucide-react'
import { hubGet, hubPost, pickObj, Loading } from '../../../pages/hub/components/ui'

interface CtxStatus {
  current_tokens: number
  token_limit: number
  percentage: number
  per_message_send_limit: number
  per_message_recv_limit: number
  compress_threshold: number
  compress_rounds: number
  status_level: string
  send_tokens: number
  recv_tokens: number
  messages_count: number
}

const STORAGE_KEY = 'yunshu_ctx_monitor'

/** 水位 → 文案/颜色（与进度条一致：>=80 红、60~80 黄、其余绿） */
function levelOf(pct: number): { label: string; dot: string; text: string } {
  if (pct >= 80) return { label: '即将溢出', dot: '🔴', text: 'text-red-400' }
  if (pct >= 60) return { label: '接近阈值', dot: '🟡', text: 'text-amber-400' }
  return { label: '正常', dot: '🟢', text: 'text-emerald-400' }
}

/** 压缩退化状态 */
function degradeOf(rounds: number): { label: string; dot: string; text: string } {
  if (rounds >= 5) return { label: '退化明显，建议新建会话', dot: '🔴', text: 'text-red-400' }
  if (rounds >= 3) return { label: '摘要质量开始下降', dot: '🟡', text: 'text-amber-400' }
  return { label: '质量良好', dot: '✓', text: 'text-emerald-400' }
}

export function ContextManagerBar() {
  const [status, setStatus] = useState<CtxStatus | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
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
    hubGet('/api/context/status').then((r) => {
      const d = pickObj<CtxStatus>(r) ?? (r as unknown as CtxStatus)
      setStatus(d)
      setError('')
    }).catch((e) => setError(String(e)))
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
      hubPost('/api/context/config', patch).then(() => {
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
      const r = await hubPost('/api/context/compress')
      const rr = r as { ok?: boolean; freed_tokens?: number; error?: string }
      if (rr.ok) flashMsg(`已压缩，释放 ${rr.freed_tokens ?? 0} tokens`)
      else if (rr.error) flashMsg(`压缩失败：${rr.error}`)
      else flashMsg(String(rr.ok ?? JSON.stringify(r).slice(0, 60)))
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      flashMsg(m.includes('401') ? '压缩需配置 FLASK_API_TOKEN' : `压缩失败：${m}`, 3500)
    } finally {
      setBusy(false)
      load()
    }
  }

  const pct = status?.percentage ?? 0
  const barColor = pct >= 80 ? 'bg-red-500' : pct >= 60 ? 'bg-amber-400' : 'bg-cyan-500'
  const lvl = levelOf(pct)
  const dg = degradeOf(status?.compress_rounds ?? 0)
  const fmt = (n?: number) => (n != null ? n.toLocaleString() : '-')

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
          {pct.toFixed(0)}%
        </span>
        <span className="hidden shrink-0 font-mono text-[10px] text-slate-500 sm:inline">{fmt(status?.current_tokens)}/{fmt(status?.token_limit)}</span>
        {status && status.compress_rounds > 0 && (
          <span className="hidden shrink-0 text-[10px] text-slate-600 md:inline">压缩 {status.compress_rounds} 次</span>
        )}
        {/* 机制说明快捷入口 */}
        <span
          role="button"
          tabIndex={0}
          title="上下文管理机制说明"
          onClick={(e) => {
            e.stopPropagation()
            setShowHelp((v) => !v)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault(); e.stopPropagation()
              setShowHelp((v) => !v)
            }
          }}
          className={`flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 transition-colors ${showHelp ? 'bg-cyan-500/15 text-cyan-300' : 'text-slate-500 hover:bg-slate-800 hover:text-slate-300'}`}
        >
          <HelpCircle size={11} />
          <span className="hidden lg:inline">机制说明</span>
        </span>
        <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>

      {/* 展开面板 */}
      {expanded && (
        <div className="space-y-3 border-t border-slate-800/70 px-4 py-3">
          {error && <div className="text-[11px] text-red-400">状态加载失败：{error}</div>}
          {msg && <div className="rounded-md border border-slate-700 bg-slate-800/60 px-2.5 py-1.5 text-[11px] text-cyan-400">{msg}</div>}
          {!status ? <Loading text="加载上下文状态…" /> : (
            <>
              {/* 概览 */}
              <div className="grid grid-cols-4 gap-2 text-center">
                {[
                  { l: '水位', v: `${pct.toFixed(0)}%`, c: pct >= 80 ? 'text-red-400' : pct >= 60 ? 'text-amber-400' : 'text-emerald-400' },
                  { l: 'Token', v: `${fmt(status.current_tokens)}/${fmt(status.token_limit)}`, c: 'text-slate-200' },
                  { l: '消息', v: fmt(status.messages_count), c: 'text-slate-200' },
                  { l: '压缩', v: `${status.compress_rounds} 次`, c: 'text-cyan-400' },
                ].map((s) => (
                  <div key={s.l} className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5">
                    <div className="text-[10px] text-slate-500">{s.l}</div>
                    <div className={`font-mono text-sm font-semibold ${s.c}`}>{s.v}</div>
                  </div>
                ))}
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
                  { l: '最大 Token', hint: '上下文窗口上限，超限丢弃最旧消息', val: localLimit ?? status.token_limit, min: 1024, max: 20000, step: 512, set: (v: number) => { setLocalLimit(v); scheduleSave({ token_limit: v }) } },
                  { l: '单次发送', hint: '单条消息最大 token，超限截断', val: localSend ?? status.per_message_send_limit, min: 256, max: 8192, step: 128, set: (v: number) => { setLocalSend(v); scheduleSave({ per_message_send_limit: v }) } },
                  { l: '单次回复', hint: '单条回复最大 token', val: localRecv ?? status.per_message_recv_limit, min: 256, max: 8192, step: 128, set: (v: number) => { setLocalRecv(v); scheduleSave({ per_message_recv_limit: v }) } },
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
                  <button
                    onClick={() => setShowHelp((v) => !v)}
                    className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] ${showHelp ? 'border-cyan-700 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400 hover:bg-slate-800'}`}
                  >
                    <HelpCircle size={11} /> {showHelp ? '收起机制说明' : '机制说明'}
                  </button>
                </div>
                <span className="text-[9px] text-slate-600">压缩为写操作，需 FLASK_API_TOKEN</span>
              </div>

              {/* ⚙️ 机制说明 */}
              {showHelp && (
                <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
                  <div className="flex items-center gap-1.5 text-[10.5px] font-medium text-cyan-300">
                    <HelpCircle size={11} /> 上下文管理机制（多层次自动策略，无需手动干预）
                  </div>

                  <div className="text-[11px] leading-relaxed text-slate-300">
                    <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                      <span className="text-cyan-400">① 异步后台压缩</span>：每次对话后检查 token 总量，
                      使用率超过阈值（<b className="text-slate-100">{Math.round(status.compress_threshold * 100)}%</b>）时，
                      在后台自动把<b className="text-slate-100">旧对话压缩为摘要</b>，保留关键决策与结论。
                      当前已压缩 <b className="text-slate-100">{status.compress_rounds} 次</b>。
                    </div>
                    <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                      <span className="text-cyan-400">② 三级水位预警</span>：
                      🟢 &lt;60% 正常 · 🟡 60%~80% 接近阈值 · 🔴 &gt;80% 即将溢出；
                      达到临界时，回复末尾会提示创建新会话。当前：<span className={lvl.text}>{lvl.dot} {lvl.label}</span>（{pct.toFixed(1)}%）。
                    </div>
                    <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                      <span className="text-cyan-400">③ 摘要退化检测</span>：每次压缩都会损失部分细节，
                      <b className="text-slate-100">压缩 ≥3 次</b>时摘要质量开始下降，<b className="text-slate-100">≥5 次</b>时退化明显，
                      建议创建新会话继续对话。当前：<span className={dg.text}>{dg.dot} {dg.label}</span>。
                    </div>
                    <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                      <span className="text-cyan-400">④ System Prompt 预算保护</span>：System prompt 有
                      <b className="text-slate-100"> 10000 tokens</b> 的预算上限，超限时自动截断工具状态列表，保证核心指令完整。
                    </div>
                    <div className="rounded-md border border-slate-800/80 bg-slate-900/40 px-2.5 py-1.5">
                      <span className="text-cyan-400">⑤ 主动丢弃</span>：即使压缩后仍超限，系统会从
                      <b className="text-slate-100">最旧的非摘要消息</b>开始丢弃，直到满足 token_limit。
                    </div>
                    <div className="text-[10px] text-slate-500">
                      提示：若反复压缩后仍频繁溢出（水位长期 ≥80%），建议点击顶部「新建会话」开始新对话。
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
