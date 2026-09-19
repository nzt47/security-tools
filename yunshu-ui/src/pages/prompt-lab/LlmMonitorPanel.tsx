/**
 * LlmMonitorPanel —— 「LLM 通信监控」（提示词实验室主内容区 Tab 之一）
 * ------------------------------------------------------------------
 * 参照 legacy（http://localhost:5678/legacy → 📡 LLM 通信监控）实现：
 *   GET  /api/llm-monitor/stats             调用统计（总量/发送与接收 tokens/平均耗时/估算费用）
 *   GET  /api/llm-monitor/records           最近收发记录（支持 source 过滤 + 分页）
 *   POST /api/llm-monitor/clear             清空记录（含已落盘的会话快照）
 *   POST /api/llm-monitor/toggle            启用/暂停监控
 *
 * 三条硬要求（对应需求）：
 *   1. **折叠面板**：每次通信一行，点击展开查看完整收发内容
 *      （请求 = system prompt + messages + tools 定义；响应 = reasoning + 文本 + tool_calls + error）。
 *   2. **时间 → Token**：记录行不再显示时间戳，改为显示本次通信的 Token 数量（▲发送 ▾接收）。
 *   3. **会话持久化**：服务关闭时后端把最后一条通信落盘（atexit），重开后本面板
 *      回填该条并标注「上次会话」——见 agent/llm_monitor.py::persist_session_last。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Copy, Loader2, Radio, RefreshCw, Search, Trash2 } from 'lucide-react'
import { hubGet, hubPost } from '../hub/components/ui'

const AUTO_REFRESH_MS = 10_000
const PAGE_SIZE = 30

interface LlmStats {
  enabled?: boolean
  total?: number
  avg_duration_ms?: number
  total_request_tokens?: number
  total_response_tokens?: number
  total_tokens?: number
  estimated_cost_usd?: number
  buffer_usage?: string
  max_records?: number
  persisted?: { file?: string; persisted_at?: string; timestamp_full?: string }
}

interface LlmMessage {
  role?: string
  content?: unknown
  tool_calls?: { function?: { name?: string; arguments?: string } }[]
}

interface LlmRecord {
  id: string
  timestamp?: number
  timestamp_str?: string
  timestamp_full?: string
  provider?: string
  model?: string
  source?: string
  duration_ms?: number
  request_tokens?: number
  response_tokens?: number
  total_tokens?: number
  system_prompt?: string
  messages?: LlmMessage[]
  tools?: { function?: { name?: string }; name?: string }[]
  response_text?: string
  tool_calls?: { function?: { name?: string; arguments?: string } }[]
  reasoning?: string
  error?: string
  restored?: boolean
  [k: string]: unknown
}

const unwrap = <T,>(r: unknown, fallback: T): T => {
  const rr = r as { data?: T }
  return rr?.data ?? (r as unknown as T) ?? fallback
}

/** 数字千分位缩写（1.2k / 3.4M） */
function formatNum(n: unknown): string {
  const v = Number(n) || 0
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`
  return String(v)
}

/** 中段截断：保留首尾（长文本可读且不撑爆页面） */
function truncateMid(text: string, maxLen: number): string {
  const t = text ?? ''
  if (t.length <= maxLen) return t
  const half = Math.floor(maxLen / 2)
  return `${t.slice(0, half)}\n\n... [中间省略 ${t.length - maxLen} 字符] ...\n\n${t.slice(-half)}`
}

function contentToText(content: unknown): string {
  if (typeof content === 'string') return content
  if (content == null) return ''
  try {
    return JSON.stringify(content)
  } catch {
    return String(content)
  }
}

/** 复制纯文本到剪贴板（含非 https 环境的降级路径） */
function copyText(text: string, done: (msg: string) => void) {
  const fallback = () => {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      done('已复制到剪贴板')
    } catch {
      done('复制失败：浏览器未授权剪贴板')
    }
  }
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => done('已复制到剪贴板')).catch(fallback)
  } else {
    fallback()
  }
}

const SOURCE_ICON: Record<string, string> = { summarize: '📝', tool_calling: '🔧', chat: '💬' }

export default function LlmMonitorPanel() {
  const [stats, setStats] = useState<LlmStats | null>(null)
  const [records, setRecords] = useState<LlmRecord[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [source, setSource] = useState('')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async (opts?: { append?: boolean; source?: string }) => {
    const src = opts?.source ?? source
    setError('')
    try {
      const offset = opts?.append ? records.length : 0
      const [s, rec] = await Promise.allSettled([
        hubGet<LlmStats | { data?: LlmStats }>('/api/llm-monitor/stats'),
        hubGet<{ records?: LlmRecord[]; total?: number; data?: LlmRecord[] }>(
          `/api/llm-monitor/records?limit=${PAGE_SIZE}&offset=${offset}${src ? `&source=${encodeURIComponent(src)}` : ''}`,
        ),
      ])
      if (s.status === 'fulfilled') setStats(unwrap<LlmStats>(s.value, {} as LlmStats))
      if (rec.status === 'fulfilled') {
        const d = rec.value as { records?: LlmRecord[]; total?: number; data?: LlmRecord[] }
        const list = Array.isArray(d.records) ? d.records : Array.isArray(d.data) ? d.data : []
        setRecords((prev) => (opts?.append ? [...prev, ...list] : list))
        setTotal(Number(d.total ?? list.length))
      }
      const fail = s.status === 'rejected' ? s.reason : rec.status === 'rejected' ? rec.reason : null
      if (fail) setError('LLM 通信数据加载失败：' + String(fail))
    } catch (e) {
      setError(`LLM 通信数据加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [records.length, source])

  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), AUTO_REFRESH_MS)
    return () => clearInterval(timer)
    // 仅依赖 source/首次：load 内部读取 records.length（分页偏移），
    // 放进依赖会导致每页都重建定时器（自动刷新失效）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])

  useEffect(() => () => {
    if (searchTimer.current) clearTimeout(searchTimer.current)
  }, [])

  const clear = async () => {
    setBusy(true)
    try {
      await hubPost('/api/llm-monitor/clear')
      setMsg('LLM 通信记录已清空（含已落盘的会话快照）')
      setExpanded({})
      await load()
    } catch (e) {
      setError(`清空失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (enabled: boolean) => {
    try {
      await hubPost('/api/llm-monitor/toggle', { enabled })
      setStats((prev) => ({ ...(prev ?? {}), enabled }))
      setMsg(enabled ? '监控已启用' : '监控已暂停')
    } catch (e) {
      setError(`切换监控状态失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const onSearch = (v: string) => {
    setSearch(v)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => setSearch(v), 250)
  }

  /** 命中搜索词：请求/响应正文或模型/来源任一匹配即保留 */
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return records
    const hit = (r: LlmRecord) => {
      const parts = [
        r.model, r.provider, r.source, r.response_text, r.reasoning, r.error, r.system_prompt,
        ...(r.messages ?? []).map((m) => contentToText(m.content)),
        ...(r.messages ?? []).flatMap((m) => (m.tool_calls ?? []).map((t) => t.function?.name)),
        ...(r.tools ?? []).map((t) => t.function?.name ?? t.name),
      ]
      return parts.some((p) => String(p ?? '').toLowerCase().includes(q))
    }
    return records.filter(hit)
  }, [records, search])

  const restoredCount = records.filter((r) => r.restored).length

  return (
    <section className="pl-category pl-llm-monitor">
      <div className="pl-monitor-head">
        <h2 className="pl-category-title" style={{ color: '#f472b6' }}>
          <span className="pl-category-dot" style={{ background: '#f472b6' }} />
          LLM 通信监控
          <span className="pl-category-count">
            {loading ? '加载中…' : `${total} 条记录 · ${formatNum(stats?.total_tokens ?? 0)} tokens`}
          </span>
        </h2>
        <label className="pl-monitor-switch" title="启用/暂停监控记录">
          <input
            type="checkbox"
            checked={stats?.enabled !== false}
            onChange={(e) => void toggleEnabled(e.target.checked)}
          />
          <span>{stats?.enabled === false ? '○ 已暂停' : '● 监控中'}</span>
        </label>
      </div>
      <p className="pl-category-desc">
        查看每次 LLM 调用的完整收发内容：请求 = 发给 LLM 的全部消息 + 工具定义，响应 = LLM 返回文本 + 工具调用。
        记录行显示<b>本次通信的 Token 数量</b>（▲发送 ▾接收），点击行即可<b>展开</b>当次详细通信信息。
        {stats?.persisted?.persisted_at && (
          <> 服务关闭时会自动保存<b>会话最后一条通信</b>（快照 {stats.persisted.persisted_at}）。</>
        )}
      </p>

      {error && <p className="pl-error">{error}</p>}
      {msg && <p className="pl-id-msg">{msg}</p>}

      {/* 统计条（对齐 legacy：总调用 / 发送 tokens / 接收 tokens / 平均耗时 / 估算费用） */}
      <div className="pl-monitor-stats">
        <div className="lm-stat-box"><b>{stats?.total ?? 0}</b><span>总调用</span></div>
        <div className="lm-stat-box"><b className="ok">{formatNum(stats?.total_request_tokens ?? 0)}</b><span>发送 Tokens</span></div>
        <div className="lm-stat-box"><b className="warn">{formatNum(stats?.total_response_tokens ?? 0)}</b><span>接收 Tokens</span></div>
        <div className="lm-stat-box"><b>{stats?.avg_duration_ms ?? 0}</b><span>平均耗时(ms)</span></div>
        <div className="lm-stat-box"><b>${(stats?.estimated_cost_usd ?? 0).toFixed(6)}</b><span>估算费用</span></div>
      </div>

      <div className="pl-monitor-toolbar">
        <button type="button" className="pl-btn" onClick={() => void load()} title="立即刷新">
          <RefreshCw size={12} /> 刷新
        </button>
        <button type="button" className="pl-btn" onClick={() => void clear()} disabled={busy} title="清空全部记录（含已落盘的会话快照）">
          {busy ? <Loader2 size={12} className="spin" /> : <Trash2 size={12} />} 清空
        </button>
        <select
          className="pl-text pl-monitor-filter"
          value={source}
          onChange={(e) => { setSource(e.target.value); setExpanded({}) }}
          title="按来源筛选"
        >
          <option value="">全部来源</option>
          <option value="chat">chat（对话）</option>
          <option value="summarize">summarize（摘要）</option>
          <option value="tool_calling">tool_calling（工具调用）</option>
        </select>
        <label className="pl-monitor-search">
          <Search size={12} />
          <input
            className="pl-text"
            placeholder="搜索消息内容…"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </label>
        {restoredCount > 0 && (
          <span className="pl-id-hint" title="由上一会话关闭时的快照回填">
            含 {restoredCount} 条上次会话遗留记录
          </span>
        )}
        <span className="pl-id-hint" style={{ marginLeft: 'auto' }}>
          仅保留最近 {stats?.max_records ?? 500} 条（{stats?.buffer_usage ?? '-'}）· 每 10s 自动刷新
        </span>
      </div>

      {/* 记录列表：折叠面板 */}
      {visible.length === 0 ? (
        <div className="pl-monitor-empty">
          {loading ? '加载中…' : records.length === 0 ? '暂无 LLM 通信记录（发起一次对话后出现）' : '没有匹配的记录'}
        </div>
      ) : (
        <div className="lm-records">
          {visible.map((r) => {
            const open = Boolean(expanded[r.id])
            const isError = Boolean(r.error)
            const tcCount = r.tool_calls?.length ?? 0
            const hasReasoning = Boolean(r.reasoning)
            return (
              <div key={r.id} className={`lm-record ${isError ? 'error' : ''}`}>
                <button
                  type="button"
                  className="lm-record-head"
                  aria-expanded={open}
                  onClick={() => setExpanded((prev) => ({ ...prev, [r.id]: !open }))}
                >
                  {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  <span className="lm-ico">{SOURCE_ICON[String(r.source ?? 'chat')] ?? '💬'}</span>
                  <span className="lm-model">{r.model || r.provider || '?'}</span>
                  <span className={`lm-badge ${String(r.source ?? 'chat')}`}>{r.source || 'chat'}</span>
                  {/* 需求：时间显示 → token 数量显示 */}
                  <span className="lm-tokens" title={`本次通信 Token：发送 ${r.request_tokens ?? 0} / 接收 ${r.response_tokens ?? 0}`}>
                    ▲{formatNum(r.request_tokens ?? 0)} ▾{formatNum(r.response_tokens ?? 0)}
                    <em>tok</em>
                  </span>
                  {tcCount > 0 && <span className="lm-flag" title={`${tcCount} 个工具调用`}>🛠{tcCount}</span>}
                  {hasReasoning && <span className="lm-flag" title="含推理过程（reasoning）">💭</span>}
                  {isError && <span className="lm-flag err" title={String(r.error)}>❌</span>}
                  {r.restored && <span className="lm-flag restored" title="由上一会话关闭时的快照回填">上次会话</span>}
                  <span className="lm-duration">{r.duration_ms != null ? `${Number(r.duration_ms).toFixed(0)}ms` : ''}</span>
                </button>

                {open && (
                  <div className="lm-record-detail">
                    {/* 请求看板 */}
                    <div className="lm-panel">
                      <div className="lm-panel-head">
                        <span>📤 发送到 LLM（{formatNum(r.request_tokens ?? 0)} tokens）</span>
                        <button
                          type="button"
                          className="pl-btn"
                          onClick={() => copyText(buildRequestText(r), setMsg)}
                        >
                          <Copy size={11} /> 复制
                        </button>
                      </div>
                      <div className="lm-panel-body">
                        {r.system_prompt && (
                          <div className="lm-msg system">
                            <span className="lm-role">system</span>
                            <pre className="lm-content">{truncateMid(String(r.system_prompt), 1200)}</pre>
                          </div>
                        )}
                        {(r.messages ?? []).map((m, i) => (
                          <div key={`${r.id}-m-${i}`} className={`lm-msg ${String(m.role ?? 'unknown')}`}>
                            <span className="lm-role">{String(m.role ?? 'unknown')}</span>
                            <pre className="lm-content">{truncateMid(contentToText(m.content), 1000)}</pre>
                            {(m.tool_calls ?? []).map((tc, j) => (
                              <div key={`${r.id}-m-${i}-tc-${j}`} className="lm-toolcall">
                                🛠 {tc.function?.name || '?'}({truncateMid(String(tc.function?.arguments ?? ''), 300)})
                              </div>
                            ))}
                          </div>
                        ))}
                        {(r.tools ?? []).length > 0 && (
                          <div className="lm-msg system">
                            <span className="lm-role">tools(定义)</span>
                            <pre className="lm-content tools">
                              {(r.tools ?? []).length} 个工具定义：{(r.tools ?? [])
                                .map((t) => t.function?.name ?? t.name ?? '?')
                                .join(', ')}
                            </pre>
                          </div>
                        )}
                        {!r.system_prompt && (r.messages ?? []).length === 0 && (r.tools ?? []).length === 0 && (
                          <div className="lm-empty">（无请求内容）</div>
                        )}
                      </div>
                    </div>

                    {/* 响应看板 */}
                    <div className="lm-panel">
                      <div className="lm-panel-head">
                        <span>📥 LLM 返回（{formatNum(r.response_tokens ?? 0)} tokens）</span>
                        <button
                          type="button"
                          className="pl-btn"
                          onClick={() => copyText(buildResponseText(r), setMsg)}
                        >
                          <Copy size={11} /> 复制
                        </button>
                      </div>
                      <div className="lm-panel-body">
                        {r.reasoning && (
                          <div className="lm-msg reasoning">
                            <span className="lm-role">💭 reasoning</span>
                            <pre className="lm-content">{truncateMid(String(r.reasoning), 4000)}</pre>
                          </div>
                        )}
                        {r.response_text && (
                          <div className="lm-msg assistant">
                            <span className="lm-role">assistant</span>
                            <pre className="lm-content">{truncateMid(String(r.response_text), 6000)}</pre>
                          </div>
                        )}
                        {(r.tool_calls ?? []).map((tc, i) => (
                          <div key={`${r.id}-tc-${i}`} className="lm-msg toolcall">
                            <span className="lm-role">🛠 {tc.function?.name || 'tool_call'}</span>
                            <pre className="lm-content mono">{truncateMid(String(tc.function?.arguments ?? '{}'), 2000)}</pre>
                          </div>
                        ))}
                        {r.error && (
                          <div className="lm-msg error">
                            <span className="lm-role">❌ error</span>
                            <pre className="lm-content">{String(r.error)}</pre>
                          </div>
                        )}
                        {!r.reasoning && !r.response_text && (r.tool_calls ?? []).length === 0 && !r.error && (
                          <div className="lm-empty">（无响应内容）</div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {records.length < total && (
        <div className="pl-monitor-more">
          <button type="button" className="pl-btn" onClick={() => void load({ append: true })} disabled={loading}>
            <Radio size={12} /> 加载更多（已显示 {records.length}/{total}）
          </button>
        </div>
      )}
    </section>
  )
}

/** 请求看板的纯文本导出（供「复制」按钮使用） */
function buildRequestText(r: LlmRecord): string {
  const parts: string[] = []
  if (r.system_prompt) parts.push(`[system]\n${r.system_prompt}`)
  for (const m of r.messages ?? []) {
    parts.push(`[${m.role ?? 'unknown'}]\n${contentToText(m.content)}`)
    for (const tc of m.tool_calls ?? []) {
      parts.push(`[tool_call] ${tc.function?.name ?? '?'}(${tc.function?.arguments ?? ''})`)
    }
  }
  if ((r.tools ?? []).length) {
    parts.push(`[tools] ${(r.tools ?? []).map((t) => t.function?.name ?? t.name ?? '?').join(', ')}`)
  }
  return parts.join('\n\n')
}

/** 响应看板的纯文本导出（供「复制」按钮使用） */
function buildResponseText(r: LlmRecord): string {
  const parts: string[] = []
  if (r.reasoning) parts.push(`[reasoning]\n${r.reasoning}`)
  if (r.response_text) parts.push(`[assistant]\n${r.response_text}`)
  for (const tc of r.tool_calls ?? []) {
    parts.push(`[tool_call] ${tc.function?.name ?? '?'}(${tc.function?.arguments ?? ''})`)
  }
  if (r.error) parts.push(`[error]\n${r.error}`)
  return parts.join('\n\n')
}
