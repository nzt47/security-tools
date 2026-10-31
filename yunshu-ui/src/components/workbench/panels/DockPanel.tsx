/**
 * DockPanel —— 右侧「可切换边栏」（2026-10-31）
 * ------------------------------------------------------------------
 * 复用原「代码编辑器」面板 id（`PANEL.CODE`），因此**历史布局无需迁移**：
 * 已持久化的 localStorage 布局里仍然是 `code`，只是这一格从"固定编辑器"
 * 变成"可切换的右坞"，默认落在「编辑器」Tab。
 *
 * 五个视图各接**真实**后端（没有的就明确显示不可用，不做假界面）：
 *   - 编辑器：复用 CodeEditorPanel（localStorage 持久化）
 *   - 文档  ：GET /api/filesystem/list + POST /api/filesystem/read（只读浏览）
 *   - 子代理：GET /api/subagent/list（信封 v2）+ GET /api/subagent/history（扁平）
 *   - 浏览器：同源 iframe（跨域站点常因 X-Frame-Options 拒绝嵌入，属浏览器行为）
 *   - 终端  ：POST /api/sandbox/run（Python 沙盒；features.sandbox 关闭时如实显示后端原因）
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BookOpen, Bot, Code2, Globe, Loader2, Play, RefreshCw, Terminal } from 'lucide-react'
import { CodeEditorPanel } from './CodeEditorPanel'
import { getEnvelope } from '@/api/envelope'
import { hubGet, hubPost } from '../../../pages/hub/components/ui'
import { getApiToken } from '@/lib/apiToken'
import {
  FILESYSTEM_LIST,
  FILESYSTEM_READ,
  SUBAGENT_HISTORY,
  SUBAGENT_LIST,
  TERMINAL_ACTIVITY,
  TERMINAL_COMMANDS,
  TERMINAL_EXEC,
  BROWSER_REMOTE_SESSION,
  BROWSER_REMOTE_FRAME,
  BROWSER_REMOTE_INPUT,
  BROWSER_REMOTE_CLOSE,
} from '@/api/endpoints'

type DockTab = 'editor' | 'docs' | 'subagent' | 'browser' | 'terminal'

const TABS: { id: DockTab; label: string; icon: typeof Code2 }[] = [
  { id: 'editor', label: '编辑器', icon: Code2 },
  { id: 'docs', label: '文档', icon: BookOpen },
  { id: 'subagent', label: '子代理', icon: Bot },
  { id: 'browser', label: '浏览器', icon: Globe },
  { id: 'terminal', label: '终端', icon: Terminal },
]

interface FsItem {
  name: string
  type?: string
  size?: number
  modified?: string
}

interface SubagentRow {
  name: string
  model_id?: string
  status?: string
  created_at?: string
  is_expired?: boolean
}

interface DelegationRow {
  delegation_id?: string
  subagent?: string
  goal?: string
  ok?: boolean
  error_code?: string
  created_at?: string
}

const DOC_ROOT = 'docs/zh'

/** 看板容器：所有 Tab 共用同一套边框/滚动约束 */
function Pane({ children }: { children: ReactNode }) {
  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
}

function Hint({ children }: { children: ReactNode }) {
  return (
    <p className="border-b border-slate-800/60 px-3 py-1.5 text-[10px] leading-relaxed text-slate-500">
      {children}
    </p>
  )
}

export function DockPanel() {
  const [tab, setTab] = useState<DockTab>('editor')

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950/40">
      <div
        role="tablist"
        aria-label="右侧面板"
        className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-1.5 py-1.5"
      >
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = id === tab
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(id)}
              title={`切换到${label}`}
              className={`flex items-center gap-1 rounded-md border px-1.5 py-1 text-[10.5px] transition-colors ${
                active
                  ? 'border-cyan-600/60 bg-cyan-500/10 text-cyan-300'
                  : 'border-transparent text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              }`}
            >
              <Icon size={11} />
              {label}
            </button>
          )
        })}
      </div>

      {tab === 'editor' && <CodeEditorPanel />}
      {tab === 'docs' && <DocsPane />}
      {tab === 'subagent' && <SubagentPane />}
      {tab === 'browser' && <BrowserPane />}
      {tab === 'terminal' && <TerminalPane />}
    </div>
  )
}

/* ───────────────────────── 文档 ───────────────────────── */
function DocsPane() {
  const [path, setPath] = useState(DOC_ROOT)
  const [items, setItems] = useState<FsItem[]>([])
  const [file, setFile] = useState<{ path: string; content: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadDir = useCallback(async (dir: string) => {
    setLoading(true)
    setError('')
    setFile(null)
    try {
      const r = await hubGet<{ ok?: boolean; abs_path?: string; items?: FsItem[]; error?: string }>(
        `${FILESYSTEM_LIST}?path=${encodeURIComponent(dir)}`,
      )
      if (r?.ok === false) {
        setError(String(r.error ?? '目录读取失败'))
        setItems([])
      } else {
        setPath(dir)
        setItems(Array.isArray(r?.items) ? r.items : [])
      }
    } catch (e) {
      setError(`目录读取失败：${e instanceof Error ? e.message : String(e)}`)
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadDir(DOC_ROOT)
  }, [loadDir])

  const open = async (name: string) => {
    const full = `${path}/${name}`
    setLoading(true)
    setError('')
    try {
      const r = await hubPost<{ ok?: boolean; content?: string; error?: string }>(FILESYSTEM_READ, {
        path: full,
        max_size_mb: 2,
      })
      if (r?.ok === false) setError(String(r.error ?? '文件读取失败'))
      else setFile({ path: full, content: String(r?.content ?? '') })
    } catch (e) {
      setError(`文件读取失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Pane>
      <Hint>
        只读浏览 <code className="text-slate-400">docs/zh</code>；点目录进入、点 .md 查看正文。
      </Hint>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        <button
          type="button"
          onClick={() => void loadDir(DOC_ROOT)}
          title="回到 docs/zh"
          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800"
        >
          根目录
        </button>
        <button
          type="button"
          onClick={() => void loadDir(path.replace(/\/[^/]+$/, '') || DOC_ROOT)}
          title="上一级"
          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800"
        >
          上一级
        </button>
        <span className="truncate font-mono text-[10px] text-slate-500" title={path}>
          {path}
        </span>
        {loading && <Loader2 size={11} className="ml-auto animate-spin text-slate-500" />}
      </div>
      {error && <p className="px-3 py-2 text-[10.5px] text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {file ? (
          <div className="p-2">
            <div className="mb-1 truncate font-mono text-[10px] text-cyan-300" title={file.path}>
              {file.path}
            </div>
            <pre className="m-0 whitespace-pre-wrap break-words text-[10.5px] leading-relaxed text-slate-300">
              {file.content}
            </pre>
          </div>
        ) : items.length === 0 && !loading ? (
          <p className="px-2 py-3 text-center text-[10.5px] text-slate-500">目录为空</p>
        ) : (
          items.map((it) => (
            <button
              key={it.name}
              type="button"
              onClick={() => (it.type === 'dir' ? void loadDir(`${path}/${it.name}`) : void open(it.name))}
              className="mb-0.5 flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[10.5px] text-slate-300 hover:bg-slate-800/70"
            >
              <span className="shrink-0 text-slate-500">{it.type === 'dir' ? '📁' : '📄'}</span>
              <span className="truncate">{it.name}</span>
            </button>
          ))
        )}
      </div>
    </Pane>
  )
}

/* ───────────────────────── 子代理 ───────────────────────── */
function SubagentPane() {
  const [rows, setRows] = useState<SubagentRow[]>([])
  const [history, setHistory] = useState<DelegationRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      // 列表走统一信封（v2）；历史仍是扁平 body —— 两者分别解析，别把口径混在一起
      const d = await getEnvelope<{ ok?: boolean; subagents?: SubagentRow[]; error?: string }>(
        SUBAGENT_LIST,
      )
      setRows(Array.isArray(d?.subagents) ? d.subagents : [])
      const h = await hubGet<{ records?: DelegationRow[] }>(`${SUBAGENT_HISTORY}?limit=8`)
      setHistory(Array.isArray(h?.records) ? h.records : [])
    } catch (e) {
      setError(`子代理数据不可用：${e instanceof Error ? e.message : String(e)}`)
      setRows([])
      setHistory([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <Pane>
      <Hint>活跃分身 = 当前可委派对象（跑完即回收）；下方是最近的委派记录。</Hint>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        <button
          type="button"
          onClick={() => void load()}
          className="flex items-center gap-1 rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800"
        >
          {loading ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
          刷新
        </button>
        <span className="ml-auto text-[10px] text-slate-500">
          活跃 {rows.length} · 记录 {history.length}
        </span>
      </div>
      {error && <p className="px-3 py-2 text-[10.5px] text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {rows.length === 0 ? (
          <p className="py-1 text-[10.5px] text-slate-500">暂无活跃分身（可到「装配车间 → 分身创建与组装」创建）</p>
        ) : (
          rows.map((r) => (
            <div key={r.name} className="mb-1 rounded border border-slate-800 bg-slate-950/50 px-2 py-1">
              <div className="flex items-center gap-1.5 text-[10.5px] text-slate-200">
                <Bot size={11} className="text-cyan-400" />
                <b className="truncate">{r.name}</b>
                {r.model_id && <span className="ml-auto font-mono text-[9px] text-slate-500">{r.model_id}</span>}
              </div>
              {r.created_at && (
                <div className="mt-0.5 text-[9.5px] text-slate-600">创建 {r.created_at.slice(0, 19).replace('T', ' ')}</div>
              )}
            </div>
          ))
        )}
        <div className="mt-2 mb-1 text-[10px] uppercase tracking-wider text-slate-600">最近委派</div>
        {history.length === 0 ? (
          <p className="text-[10.5px] text-slate-500">暂无委派记录</p>
        ) : (
          history.map((h) => (
            <div key={h.delegation_id ?? `${h.subagent}-${h.created_at}`} className="mb-1 border-l-2 border-slate-800 pl-2">
              <div className="flex items-center gap-1.5 text-[10px]">
                <span className={h.ok ? 'text-emerald-400' : 'text-rose-400'}>{h.ok ? '成功' : '失败'}</span>
                <span className="truncate text-slate-400">{h.subagent ?? '—'}</span>
              </div>
              <div className="truncate text-[9.5px] text-slate-500" title={h.goal ?? ''}>
                {h.goal ?? h.error_code ?? ''}
              </div>
            </div>
          ))
        )}
      </div>
    </Pane>
  )
}

/* ───────────────────────── 浏览器 ───────────────────────── */

/**
 * 只接受 http(s) 绝对地址，其它一律拒绝（含 `javascript:` / `data:` / `file:`）。
 *
 * 【为什么必须显式校验】iframe 的 src 会按 HTML 语义解释，用户输入若带
 * `javascript:` 就是一段可执行脚本（CodeQL: DOM text reinterpreted as HTML）。
 * 这里用 `new URL()` 解析后**只回填协议+主机+路径**，等于把输入重新序列化，
 * 不做字符串拼接。
 */
function normalizeHttpUrl(raw: string): string {
  const text = raw.trim()
  if (!text) return ''
  try {
    const u = new URL(text)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return ''
    return u.href
  } catch {
    return ''
  }
}

/** 可交互浏览器的会话信息（服务端 viewport 用于把显示坐标换算回页面坐标） */
interface RemoteSession {
  id: string
  width: number
  height: number
}

/**
 * 浏览器：两种模式
 *   - 内嵌（默认）：同源 iframe。跨域站点常因 X-Frame-Options / CSP 拒绝被嵌入。
 *   - 交互：服务端受控 Chromium（Playwright）出截图流，点击/键盘/滚动都发回服务端执行。
 *     适合"同源 iframe 被 X-Frame-Options 挡住、但仍要操作"的站点；导航受 SSRF 守卫约束
 *     （默认只允许公网 http(s)，本机/内网一律 400）。
 */
function modeBtnClass(active: boolean): string {
  return 'rounded border px-1.5 py-0.5 text-[10px] transition-colors ' + (
    active
      ? 'border-cyan-600/60 bg-cyan-500/10 text-cyan-300'
      : 'border-slate-800 text-slate-400 hover:bg-slate-800/60')
}

function BrowserPane() {
  const [url, setUrl] = useState('http://127.0.0.1:5678/')
  const [current, setCurrent] = useState('')
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'iframe' | 'remote'>('iframe')
  const [session, setSession] = useState<RemoteSession | null>(null)
  const [frame, setFrame] = useState('')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const frameRef = useRef('')
  const sessionRef = useRef<RemoteSession | null>(null)

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  /** 换帧时释放上一张 object URL（否则每次轮询都会漏一个 Blob） */
  const replaceFrame = useCallback((next: string) => {
    const prev = frameRef.current
    frameRef.current = next
    setFrame(next)
    if (prev) URL.revokeObjectURL(prev)
  }, [])

  const pullFrame = useCallback(async (sid: string) => {
    const token = getApiToken()
    const res = await fetch(BROWSER_REMOTE_FRAME + '?session_id=' + encodeURIComponent(sid), {
      headers: token ? { Authorization: 'Bearer ' + token } : {},
    })
    if (!res.ok) throw new Error('HTTP ' + res.status)
    const blob = await res.blob()
    replaceFrame(URL.createObjectURL(blob))
  }, [replaceFrame])

  const openRemote = useCallback(async (target: string) => {
    setBusy(true)
    setError('')
    try {
      const r = await hubPost<{
        ok?: boolean; session_id?: string; width?: number; height?: number
        title?: string; error?: string
      }>(BROWSER_REMOTE_SESSION, { url: target })
      if (!r?.ok || !r.session_id) throw new Error(r?.error || '会话创建失败')
      const next = { id: r.session_id, width: r.width ?? 1280, height: r.height ?? 800 }
      setSession(next)
      setTitle(r.title || '')
      await pullFrame(next.id)
    } catch (e) {
      setSession(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [pullFrame])

  const send = useCallback(async (payload: Record<string, unknown>) => {
    const sid = sessionRef.current?.id
    if (!sid) return
    setError('')
    try {
      const r = await hubPost<{ ok?: boolean; url?: string; title?: string; error?: string }>(
        BROWSER_REMOTE_INPUT, { session_id: sid, ...payload })
      if (!r?.ok) throw new Error(r?.error || '操作失败')
      if (r.title) setTitle(r.title)
      if (r.url) setUrl(r.url)
      await pullFrame(sid)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [pullFrame])

  const closeRemote = useCallback(async () => {
    const sid = sessionRef.current?.id
    sessionRef.current = null
    setSession(null)
    setTitle('')
    replaceFrame('')
    if (!sid) return
    try {
      await hubPost(BROWSER_REMOTE_CLOSE, { session_id: sid })
    } catch {
      /* 关闭失败不阻塞界面：会话在服务端会按空闲超时回收 */
    }
  }, [replaceFrame])

  // 交互模式轮询出帧（1.5s；服务端单会话，重复打开会替换旧会话）
  useEffect(() => {
    if (!session) return
    const timer = window.setInterval(() => {
      void pullFrame(session.id).catch(() => undefined)
    }, 1500)
    return () => window.clearInterval(timer)
  }, [session, pullFrame])

  // 卸载时释放 Blob 并关闭会话
  useEffect(() => () => {
    if (frameRef.current) URL.revokeObjectURL(frameRef.current)
    const sid = sessionRef.current?.id
    if (sid) void hubPost(BROWSER_REMOTE_CLOSE, { session_id: sid }).catch(() => undefined)
  }, [])

  const switchMode = (next: 'iframe' | 'remote') => {
    setError('')
    if (next === 'remote') {
      setMode('remote')
      // 已经在内嵌模式打开过页面 ⇒ 用同一地址接管；否则等用户输入
      // （默认地址是本机，会被 SSRF 守卫拒绝，替用户先撞一次墙没有意义）
      const target = normalizeHttpUrl(current)
      if (target) void openRemote(target)
      return
    }
    setMode('iframe')
    void closeRemote()
  }

  const onFrameClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const img = imgRef.current
    if (!img || !session) return
    const rect = img.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const x = Math.round((e.clientX - rect.left) * (session.width / rect.width))
    const y = Math.round((e.clientY - rect.top) * (session.height / rect.height))
    void send({ kind: 'click', x, y })
  }

  return (
    <Pane>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        {(['iframe', 'remote'] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => switchMode(m)}
            aria-pressed={mode === m}
            className={modeBtnClass(mode === m)}
          >
            {m === 'iframe' ? '内嵌' : '交互'}
          </button>
        ))}
        <span className="ml-auto truncate text-[9.5px] text-slate-600">
          {mode === 'iframe' ? '同源内嵌 · 跨域常被 X-Frame-Options 拒绝' : '受控 Chromium · 截图流 · 仅公网 http(s)'}
        </span>
      </div>
      <form
        className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1"
        onSubmit={(e) => {
          e.preventDefault()
          const next = normalizeHttpUrl(url)
          if (!next) {
            setError('只支持 http(s) 地址（javascript:/data:/file: 一律拒绝）')
            return
          }
          setError('')
          setCurrent(next)
          if (mode === 'remote') void openRemote(next)
        }}
      >
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-label="浏览器地址"
          placeholder="http(s)://…"
          className="min-w-0 flex-1 rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
        />
        <button
          type="submit"
          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-800"
        >
          打开
        </button>
        {mode === 'remote' && (
          <>
            <button type="button" onClick={() => void send({ kind: 'back' })} aria-label="后退"
              className="rounded-sm border border-slate-800 px-1 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800">←</button>
            <button type="button" onClick={() => void send({ kind: 'forward' })} aria-label="前进"
              className="rounded-sm border border-slate-800 px-1 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800">→</button>
            <button type="button" onClick={() => void send({ kind: 'reload' })} aria-label="刷新画面"
              className="rounded-sm border border-slate-800 px-1 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800">⟳</button>
          </>
        )}
      </form>
      {mode === 'remote' && (
        <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="键盘输入"
            placeholder="输入文本后回车（发到当前焦点）"
            className="min-w-0 flex-1 rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 text-[10px] text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                if (text) { void send({ kind: 'type', text }); setText('') }
              }
            }}
          />
          {['Enter', 'Tab', 'Escape', 'Backspace'].map((k) => (
            <button key={k} type="button" onClick={() => void send({ kind: 'key', key: k })}
              className="rounded-sm border border-slate-800 px-1 py-0.5 font-mono text-[9px] text-slate-400 hover:bg-slate-800">
              {k}
            </button>
          ))}
          <button type="button" onClick={() => void closeRemote()}
            className="rounded-sm border border-rose-900/60 px-1 py-0.5 text-[9px] text-rose-300 hover:bg-rose-950/40">
            关闭会话
          </button>
        </div>
      )}
      {error && <p className="px-3 py-1 text-[10px] text-rose-300">{error}</p>}
      <div className="relative min-h-0 flex-1">
        {mode === 'iframe' ? (
          current ? (
            <iframe
              key={current}
              title="浏览器"
              src={current}
              className="h-full w-full border-0 bg-white"
              sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
            />
          ) : (
            <p className="px-3 py-4 text-center text-[10.5px] text-slate-500">输入地址后点「打开」</p>
          )
        ) : session && frame ? (
          <img
            ref={imgRef}
            src={frame}
            alt="交互浏览器画面"
            onClick={onFrameClick}
            className="h-full w-full cursor-crosshair bg-white object-contain"
            draggable={false}
          />
        ) : (
          <p className="px-3 py-4 text-center text-[10.5px] text-slate-500">
            {busy ? '正在启动受控浏览器…' : '输入公网地址后点「打开」，或切回内嵌模式'}
          </p>
        )}
        {mode === 'remote' && title && (
          <span className="pointer-events-none absolute bottom-1 left-1 rounded bg-slate-950/80 px-1.5 py-0.5 text-[9px] text-slate-400">
            {title}
          </span>
        )}
      </div>
    </Pane>
  )
}

/* ───────────────────────── 终端（白名单命令） ───────────────────────── */
interface TerminalCommand {
  id: string
  label: string
  description?: string
  params?: { name: string; type: string; min?: number; max?: number; default?: number }[]
}

interface TerminalResult {
  ok?: boolean
  argv?: string[]
  exit_code?: number
  stdout?: string
  stderr?: string
  duration_ms?: number
  timed_out?: boolean
  error?: string
}

function TerminalPane() {
  const [mode, setMode] = useState<'cmd' | 'activity'>('cmd')

  return (
    <Pane>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        {(['cmd', 'activity'] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            aria-pressed={mode === m}
            className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
              mode === m
                ? 'border-cyan-600/60 bg-cyan-500/10 text-cyan-300'
                : 'border-slate-800 text-slate-400 hover:bg-slate-800/60'
            }`}
          >
            {m === 'cmd' ? '命令' : '实时活动'}
          </button>
        ))}
        <span className="ml-auto text-[9.5px] text-slate-600">
          {mode === 'cmd' ? '白名单执行 · 不是自由 shell' : '审计链尾部 · 3s 轮询'}
        </span>
      </div>
      {mode === 'cmd' ? <CommandView /> : <ActivityView />}
    </Pane>
  )
}

function CommandView() {
  const [commands, setCommands] = useState<TerminalCommand[]>([])
  const [picked, setPicked] = useState('')
  const [paramText, setParamText] = useState('10')
  const [out, setOut] = useState<TerminalResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await hubGet<{ ok?: boolean; commands?: TerminalCommand[]; error?: string }>(TERMINAL_COMMANDS)
      if (r?.ok === false) setError(String(r.error ?? '命令清单读取失败'))
      else {
        const list = Array.isArray(r?.commands) ? r.commands : []
        setCommands(list)
        if (list.length && !picked) setPicked(list[0].id)
      }
    } catch (e) {
      setError(`命令清单不可用：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [picked])

  useEffect(() => {
    void load()
    // 只在首次挂载拉一次清单
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const current = commands.find((c) => c.id === picked)
  const needsParam = Boolean(current?.params?.length)

  const run = async () => {
    if (!picked) return
    setLoading(true)
    setError('')
    try {
      const params: Record<string, unknown> = {}
      if (needsParam) params[current!.params![0].name] = paramText
      const r = await hubPost<TerminalResult>(TERMINAL_EXEC, { command_id: picked, params })
      setOut(r ?? {})
    } catch (e) {
      setError(`执行失败：${e instanceof Error ? e.message : String(e)}`)
      setOut(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Hint>
        白名单执行（**不是自由 shell**）：只能选命令，参数仅支持整数；argv 由服务端常量表拼出。
      </Hint>
      <div className="shrink-0 border-b border-slate-800/60 px-2 py-1.5">
        <div className="flex flex-wrap gap-1">
          {commands.map((c) => {
            const active = c.id === picked
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  setPicked(c.id)
                  setOut(null)
                }}
                title={c.description ?? c.label}
                className={`rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors ${
                  active
                    ? 'border-cyan-600/60 bg-cyan-500/10 text-cyan-300'
                    : 'border-slate-800 text-slate-400 hover:bg-slate-800/60'
                }`}
              >
                {c.label}
              </button>
            )
          })}
          {commands.length === 0 && !loading && <span className="text-[10px] text-slate-500">暂无可用命令</span>}
        </div>
        <div className="mt-1.5 flex items-center gap-1">
          {needsParam && (
            <input
              value={paramText}
              onChange={(e) => setParamText(e.target.value)}
              aria-label="命令参数"
              className="w-14 rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-200"
            />
          )}
          <button
            type="button"
            onClick={() => void run()}
            disabled={!picked || loading}
            className="flex items-center gap-1 rounded-sm border border-cyan-700/60 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-300 disabled:opacity-50"
          >
            {loading ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />} 执行
          </button>
          <span className="ml-auto truncate font-mono text-[9px] text-slate-600">
            {out?.argv ? out.argv.join(' ') : ''}
          </span>
        </div>
      </div>
      {error && <p className="px-3 py-1 text-[10px] text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1 font-mono text-[10px] leading-relaxed">
        {!out ? (
          <p className="text-slate-500">选一条命令后点「执行」</p>
        ) : (
          <>
            {typeof out.exit_code === 'number' && (
              <p className={out.exit_code === 0 ? 'text-emerald-400' : 'text-rose-400'}>
                exit {out.exit_code}
                {out.timed_out ? '（超时）' : ''}
                {typeof out.duration_ms === 'number' ? ` · ${out.duration_ms}ms` : ''}
              </p>
            )}
            {out.stdout && <pre className="m-0 whitespace-pre-wrap text-slate-300">{out.stdout}</pre>}
            {out.stderr && <pre className="m-0 whitespace-pre-wrap text-amber-300">{out.stderr}</pre>}
            {out.error && <pre className="m-0 whitespace-pre-wrap text-rose-300">{out.error}</pre>}
          </>
        )}
      </div>
    </>
  )
}

/** 实时活动：审计链尾部（云枢正在做什么）+ 进行中的子代理委派 */
function ActivityView() {
  const [rows, setRows] = useState<{ seq: number; ts: string; action: string; subject: string }[]>([])
  const [inflight, setInflight] = useState<{ task_id?: string; subagent?: string; goal?: string }[]>([])
  const [paused, setPaused] = useState(false)
  const [error, setError] = useState('')
  const lastSeq = useRef(0)
  const boxRef = useRef<HTMLDivElement>(null)

  const poll = useCallback(async () => {
    try {
      const r = await hubGet<{
        ok?: boolean
        rows?: { seq: number; ts: string; action: string; subject: string }[]
        inflight?: { task_id?: string; subagent?: string; goal?: string }[]
        error?: string
      }>(`${TERMINAL_ACTIVITY}?limit=60&since_seq=${lastSeq.current}`)
      if (r?.ok === false) {
        setError(String(r.error ?? '活动读取失败'))
        return
      }
      setError('')
      const incoming = Array.isArray(r?.rows) ? r.rows : []
      if (incoming.length) {
        lastSeq.current = incoming[incoming.length - 1].seq
        setRows((prev) => [...prev, ...incoming].slice(-300))
      }
      setInflight(Array.isArray(r?.inflight) ? r.inflight : [])
    } catch (e) {
      setError(`活动不可用：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [])

  useEffect(() => {
    if (paused) return
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    return () => window.clearInterval(timer)
  }, [paused, poll])

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [rows.length])

  return (
    <>
      <Hint>
        审计链尾部（工具调用 / 技能加载 / 调度执行 / UI 写操作）—— 这就是“云枢此刻在做什么”。
      </Hint>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        <button
          type="button"
          onClick={() => setPaused((p) => !p)}
          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-800"
        >
          {paused ? '继续' : '暂停'}
        </button>
        <button
          type="button"
          onClick={() => {
            setRows([])
            lastSeq.current = 0
          }}
          className="rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800"
        >
          清空
        </button>
        <span className="ml-auto font-mono text-[9px] text-slate-600">
          seq {lastSeq.current || '—'} · {rows.length} 条
        </span>
      </div>
      {inflight.length > 0 && (
        <div className="shrink-0 border-b border-slate-800/60 bg-amber-500/5 px-2 py-1">
          <div className="text-[9.5px] uppercase tracking-wider text-amber-300/80">进行中的委派</div>
          {inflight.map((f, i) => (
            <div key={f.task_id ?? i} className="truncate text-[10px] text-amber-200/90" title={f.goal ?? ''}>
              {f.subagent ?? '—'} · {f.goal ?? ''}
            </div>
          ))}
        </div>
      )}
      {error && <p className="px-3 py-1 text-[10px] text-rose-300">{error}</p>}
      <div ref={boxRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1 font-mono text-[9.5px] leading-relaxed">
        {rows.length === 0 ? (
          <p className="text-slate-500">暂无活动（有新执行时会自动出现）</p>
        ) : (
          rows.map((r) => (
            <div key={r.seq} className="flex gap-1.5 py-0.5">
              <span className="shrink-0 text-slate-600">{r.ts.slice(11, 19)}</span>
              <span className="shrink-0 text-cyan-400/80">{r.action}</span>
              <span className="truncate text-slate-400" title={r.subject ?? ''}>
                {r.subject ?? ''}
              </span>
            </div>
          ))
        )}
      </div>
    </>
  )
}
