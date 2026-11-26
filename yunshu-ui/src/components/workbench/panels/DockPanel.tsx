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
import { BookOpen, Bot, Code2, Globe, Loader2, Play, RefreshCw, Send, Terminal } from 'lucide-react'
import { CodeEditorPanel } from './CodeEditorPanel'
import { getEnvelope } from '@/api/envelope'
import { hubGet, hubPost } from '../../../pages/hub/components/ui'
import { Button, Input, Tabs } from '@/components/ui'
import { getApiToken } from '@/lib/apiToken'
import {
  FILESYSTEM_LIST,
  FILESYSTEM_READ,
  SUBAGENT_HISTORY,
  SUBAGENT_LIST,
  TERMINAL_ACTIVITY,
  TERMINAL_CLOSE,
  TERMINAL_COMMANDS,
  TERMINAL_EXEC,
  TERMINAL_INPUT,
  TERMINAL_OUTPUT,
  TERMINAL_SESSIONS,
  TERMINAL_SIGNAL,
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
    <p className="border-b border-slate-800/60 px-3 py-1.5 text-micro leading-relaxed text-slate-500">
      {children}
    </p>
  )
}

export function DockPanel() {
  // 【2026-11-19】默认落在「终端」（Owner：右坞默认打开、显示终端的实时活动）
  const [tab, setTab] = useState<DockTab>('terminal')

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950/40">
      <Tabs
        variant="pill"
        size="xs"
        aria-label="右侧面板"
        className="wb-strip shrink-0 border-b border-slate-800/60 px-1.5"
        value={tab}
        onChange={(v) => setTab(v as typeof tab)}
        items={TABS.map(({ id, label, icon: Icon }) => ({
          value: id,
          label: <><Icon size={11} />{label}</>,
          title: `切换到${label}`,
        }))}
      />

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
        <Button variant="default" size="xs" onClick={() => void loadDir(DOC_ROOT)} title="回到 docs/zh">
          根目录
        </Button>
        <Button
          variant="default"
          size="xs"
          onClick={() => void loadDir(path.replace(/\/[^/]+$/, '') || DOC_ROOT)}
          title="上一级"
        >
          上一级
        </Button>
        <span className="truncate font-mono text-micro text-slate-500" title={path}>
          {path}
        </span>
        {loading && <Loader2 size={11} className="ml-auto animate-spin text-slate-500" />}
      </div>
      {error && <p className="px-3 py-2 text-caption text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {file ? (
          <div className="p-2">
            <div className="mb-1 truncate font-mono text-micro text-cyan-300" title={file.path}>
              {file.path}
            </div>
            <pre className="m-0 whitespace-pre-wrap break-words text-caption leading-relaxed text-slate-300">
              {file.content}
            </pre>
          </div>
        ) : items.length === 0 && !loading ? (
          <p className="px-2 py-3 text-center text-caption text-slate-500">目录为空</p>
        ) : (
          items.map((it) => (
            <Button key={it.name} type="button" onClick={() => (it.type === 'dir' ? void loadDir(`${path}/${it.name}`) : void open(it.name))} className="w-full justify-start text-left" variant="default" size="xs">
              <span className="shrink-0 text-muted-foreground">{it.type === 'dir' ? '📁' : '📄'}</span>
              <span className="truncate">{it.name}</span>
            </Button>
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
        <Button variant="default" size="xs" onClick={() => void load()}>
          {loading ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
          刷新
        </Button>
        <span className="ml-auto text-micro text-slate-500">
          活跃 {rows.length} · 记录 {history.length}
        </span>
      </div>
      {error && <p className="px-3 py-2 text-caption text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {rows.length === 0 ? (
          <p className="py-1 text-caption text-slate-500">暂无活跃分身（可到「装配车间 → 分身创建与组装」创建）</p>
        ) : (
          rows.map((r) => (
            <div key={r.name} className="mb-1 rounded border border-slate-800 bg-slate-950/50 px-2 py-1">
              <div className="flex items-center gap-1.5 text-caption text-slate-200">
                <Bot size={11} className="text-cyan-400" />
                <b className="truncate">{r.name}</b>
                {r.model_id && <span className="ml-auto font-mono text-micro text-slate-500">{r.model_id}</span>}
              </div>
              {r.created_at && (
                <div className="mt-0.5 text-micro text-slate-600">创建 {r.created_at.slice(0, 19).replace('T', ' ')}</div>
              )}
            </div>
          ))
        )}
        <div className="mt-2 mb-1 text-micro uppercase tracking-wider text-slate-600">最近委派</div>
        {history.length === 0 ? (
          <p className="text-caption text-slate-500">暂无委派记录</p>
        ) : (
          history.map((h) => (
            <div key={h.delegation_id ?? `${h.subagent}-${h.created_at}`} className="mb-1 border-l-2 border-slate-800 pl-2">
              <div className="flex items-center gap-1.5 text-micro">
                <span className={h.ok ? 'text-emerald-400' : 'text-rose-400'}>{h.ok ? '成功' : '失败'}</span>
                <span className="truncate text-slate-400">{h.subagent ?? '—'}</span>
              </div>
              <div className="truncate text-micro text-slate-500" title={h.goal ?? ''}>
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
          <Button
            key={m}
            variant={mode === m ? 'primary' : 'default'}
            size="xs"
            onClick={() => switchMode(m)}
            aria-pressed={mode === m}
          >
            {m === 'iframe' ? '内嵌' : '交互'}
          </Button>
        ))}
        <span className="ml-auto truncate text-micro text-slate-600">
          {mode === 'iframe' ? '同源内嵌 · 跨域常被 X-Frame-Options 拒绝' : '受控 Chromium · 截图流 · 内网需 CP_BROWSER_REMOTE_ALLOW_HOSTS'}
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
        <Input
          variant="bare"
          className="min-w-0 flex-1"
          inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-micro text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-label="浏览器地址"
          placeholder="http(s)://…"
        />
        <Button type="submit" variant="default" size="xs">
          打开
        </Button>
        {mode === 'remote' && (
          <>
            <Button variant="default" size="xs" className="px-1" onClick={() => void send({ kind: 'back' })} aria-label="后退">←</Button>
            <Button variant="default" size="xs" className="px-1" onClick={() => void send({ kind: 'forward' })} aria-label="前进">→</Button>
            <Button variant="default" size="xs" className="px-1" onClick={() => void send({ kind: 'reload' })} aria-label="刷新画面">⟳</Button>
          </>
        )}
      </form>
      {mode === 'remote' && (
        <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
          <Input
            variant="bare"
            className="min-w-0 flex-1"
            inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 text-micro text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="键盘输入"
            placeholder="输入文本后回车（发到当前焦点）"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                if (text) { void send({ kind: 'type', text }); setText('') }
              }
            }}
          />
          {['Enter', 'Tab', 'Escape', 'Backspace'].map((k) => (
            <Button
              key={k}
              variant="default"
              size="xs"
              className="px-1 font-mono"
              onClick={() => void send({ kind: 'key', key: k })}
            >
              {k}
            </Button>
          ))}
          <Button
            variant="default"
            size="xs"
            className="border-danger/60 text-danger hover:bg-danger/10"
            onClick={() => void closeRemote()}
          >
            关闭会话
          </Button>
        </div>
      )}
      {error && <p className="px-3 py-1 text-micro text-rose-300">{error}</p>}
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
            <p className="px-3 py-4 text-center text-caption text-slate-500">输入地址后点「打开」</p>
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
          <p className="px-3 py-4 text-center text-caption text-slate-500">
            {busy ? '正在启动受控浏览器…' : '输入公网地址后点「打开」，或切回内嵌模式'}
          </p>
        )}
        {mode === 'remote' && title && (
          <span className="pointer-events-none absolute bottom-1 left-1 rounded bg-slate-950/80 px-1.5 py-0.5 text-micro text-slate-400">
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
  // 【2026-11-19】默认看「实时活动」（审计链尾部 = 云枢此刻在做什么），命令面板仍在旁边一键切
  // 【2026-11-27】新增第三个子视图「交互」= 真 PTY 会话（E3-1 界面侧）；开关默认关
  const [mode, setMode] = useState<'cmd' | 'activity' | 'pty'>('activity')

  return (
    <Pane>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        {(['cmd', 'activity', 'pty'] as const).map((m) => (
          <Button
            key={m}
            variant={mode === m ? 'primary' : 'default'}
            size="xs"
            onClick={() => setMode(m)}
            aria-pressed={mode === m}
          >
            {m === 'cmd' ? '命令' : m === 'activity' ? '实时活动' : '交互'}
          </Button>
        ))}
        <span className="ml-auto text-micro text-slate-600">
          {mode === 'cmd'
            ? '白名单执行 · 不是自由 shell'
            : mode === 'activity'
              ? '审计链尾部 · 3s 轮询'
              : '真 PTY · 800ms 轮询'}
        </span>
      </div>
      {mode === 'cmd' ? <CommandView /> : mode === 'activity' ? <ActivityView /> : <PtyView />}
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
              <Button
                key={c.id}
                variant={active ? 'primary' : 'default'}
                size="xs"
                className="font-mono"
                onClick={() => {
                  setPicked(c.id)
                  setOut(null)
                }}
                title={c.description ?? c.label}
              >
                {c.label}
              </Button>
            )
          })}
          {commands.length === 0 && !loading && <span className="text-micro text-slate-500">暂无可用命令</span>}
        </div>
        <div className="mt-1.5 flex items-center gap-1">
          {needsParam && (
            <Input
              variant="bare"
              className="w-14"
              inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-micro text-slate-200"
              value={paramText}
              onChange={(e) => setParamText(e.target.value)}
              aria-label="命令参数"
            />
          )}
          <Button variant="primary" size="xs" onClick={() => void run()} disabled={!picked || loading}>
            {loading ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />} 执行
          </Button>
          <span className="ml-auto truncate font-mono text-micro text-slate-600">
            {out?.argv ? out.argv.join(' ') : ''}
          </span>
        </div>
      </div>
      {error && <p className="px-3 py-1 text-micro text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1 font-mono text-micro leading-relaxed">
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



/* ───────────────────────── 交互终端（PTY，E3-1） ───────────────────────── */

interface PtySessionRow {
  id: string
  command?: string
  cwd?: string
  alive?: boolean
  age_s?: number
  idle_s?: number
  buffered_chars?: number
  total_chars?: number
}

interface PtySessionsResp {
  ok?: boolean
  enabled?: boolean
  backend?: string
  max_sessions?: number
  sessions?: PtySessionRow[]
}

interface PtyReadResp {
  ok?: boolean
  code?: string
  chunk?: string
  offset?: number
  next_offset?: number | null
  trimmed?: boolean
  trimmed_from?: number
  total_chars?: number
  alive?: boolean
}

/** 轮询间隔 + 界面保留的最大字符数（与 pty_session 的环形窗口同量级） */
const PTY_POLL_MS = 800
const PTY_KEEP_CHARS = 200_000

/** 失败码 → 界面文案。**中文只在界面这一份**：后端只回稳定 code（不回异常派生串）。 */
const PTY_ERROR_TEXT: Record<string, string> = {
  disabled: '终端未启用：到「设置中心」打开 CP_TERMINAL_ENABLED 后重试',
  blocked: '权限预设为受限档：raw PTY 会绕过命令沙箱，已按 fail-closed 拒绝',
  dependency: '缺少 pywinpty：pip install pywinpty（或 pip install .[pty]）',
  max_sessions: '会话已达上限：先关闭一个会话再开',
  bad_cwd: '工作目录不存在或不可访问',
  spawn_failed: '终端启动失败（详见服务日志）',
  no_session: '会话不存在（可能已关闭或被空闲回收）',
  write_failed: '写入失败（会话可能已退出）',
  bad_action: '不支持的动作',
  missing_id: '缺少会话 id',
}

export function ptyErrorText(code?: string): string {
  return (code && PTY_ERROR_TEXT[code]) || '操作失败（详见服务日志）'
}

/**
 * 交互终端：真 PTY 会话（不是白名单命令，也不是审计流）。
 *
 * 三条如实：
 *  - 开关没开就显示「未启用」并给出该开哪个开关（后端 code=disabled，这里映射文案）；
 *  - 环形窗口滑过的部分用 trimmed_from 告知，绝不假装读到了；
 *  - 会话只存服务端进程内存，切走再回来按窗口起点重读（最多 20k 字符/次）。
 */
function PtyView() {
  const [meta, setMeta] = useState<PtySessionsResp | null>(null)
  const [sessions, setSessions] = useState<PtySessionRow[]>([])
  const [sid, setSid] = useState('')
  const [command, setCommand] = useState('')
  const [cwd, setCwd] = useState('')
  const [text, setText] = useState('')
  const [out, setOut] = useState('')
  const [alive, setAlive] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const offsetRef = useRef(0)
  const sidRef = useRef('')
  const boxRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    sidRef.current = sid
  }, [sid])

  const refresh = useCallback(async () => {
    try {
      const r = await hubGet<PtySessionsResp>(TERMINAL_SESSIONS)
      setMeta(r)
      const list = Array.isArray(r?.sessions) ? r.sessions : []
      setSessions(list)
      setError('')
      if (!sidRef.current && list.length) {
        const first = list[0]
        if (first?.id) {
          offsetRef.current = 0
          setOut('')
          setSid(first.id)
          setAlive(Boolean(first.alive))
        }
      }
    } catch (e) {
      setError(`终端状态不可用：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const poll = useCallback(async () => {
    const cur = sidRef.current
    if (!cur) return
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    try {
      const r = await hubGet<PtyReadResp>(
        `${TERMINAL_OUTPUT}?id=${encodeURIComponent(cur)}&offset=${offsetRef.current}`,
      )
      if (r?.ok === false) {
        setError(ptyErrorText(r.code))
        if (r.code === 'no_session') {
          sidRef.current = ''
          setSid('')
          setAlive(false)
        }
        return
      }
      if (r?.trimmed) {
        const from = Number(r?.trimmed_from ?? 0)
        offsetRef.current = from
        setNotice(`输出窗口已滑过：前 ${from} 个字符已被环形缓冲裁掉`)
      }
      const chunk = String(r?.chunk ?? '')
      if (typeof r?.next_offset === 'number') offsetRef.current = r.next_offset
      else if (typeof r?.offset === 'number') offsetRef.current = Number(r.offset) + chunk.length
      if (chunk) {
        setOut((prev) => {
          const next = prev + chunk
          return next.length > PTY_KEEP_CHARS ? next.slice(next.length - PTY_KEEP_CHARS) : next
        })
      }
      if (typeof r?.alive === 'boolean') setAlive(r.alive)
      setError('')
    } catch (e) {
      setError(`读取失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [])

  useEffect(() => {
    if (!sid) {
      setAlive(false)
      return
    }
    void poll()
    const timer = window.setInterval(() => void poll(), PTY_POLL_MS)
    return () => window.clearInterval(timer)
  }, [sid, poll])

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [out])

  const adopt = (id: string, isAlive?: boolean) => {
    offsetRef.current = 0
    sidRef.current = id
    setOut('')
    setNotice('')
    setError('')
    setSid(id)
    setAlive(Boolean(isAlive))
  }

  const open = async () => {
    setBusy(true)
    setError('')
    try {
      const r = await hubPost<{ ok?: boolean; code?: string; id?: string }>(TERMINAL_SESSIONS, {
        command: command.trim(),
        cwd: cwd.trim(),
      })
      if (!r?.ok || !r?.id) {
        setError(ptyErrorText(r?.code))
        return
      }
      adopt(r.id, true)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const send = async () => {
    const cur = sidRef.current
    if (!cur || !text) return
    const payload = text
    setText('')
    try {
      const r = await hubPost<{ ok?: boolean; code?: string }>(TERMINAL_INPUT, { id: cur, text: payload })
      if (r?.ok === false) {
        setError(ptyErrorText(r.code))
        setText(payload)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setText(payload)
    }
  }

  const signal = async (action: 'interrupt' | 'terminate') => {
    const cur = sidRef.current
    if (!cur) return
    try {
      const r = await hubPost<{ ok?: boolean; code?: string }>(TERMINAL_SIGNAL, { id: cur, action })
      if (r?.ok === false) setError(ptyErrorText(r.code))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const close = async () => {
    const cur = sidRef.current
    sidRef.current = ''
    setSid('')
    setAlive(false)
    if (!cur) return
    try {
      const r = await hubPost<{ ok?: boolean; code?: string }>(TERMINAL_CLOSE, { id: cur })
      if (r?.ok === false && r.code !== 'no_session') setError(ptyErrorText(r.code))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    await refresh()
  }

  return (
    <Pane>
      <Hint>
        {meta?.enabled
          ? `真 PTY 会话（${meta.backend ?? 'pty'}）· 上限 ${meta.max_sessions ?? '?'} 个 · 空闲回收由服务端按 CP_TERMINAL_IDLE_SECONDS 执行`
          : '终端未启用：需要 CP_TERMINAL_ENABLED=1（设置中心 A 级开关）；受限档一律拒绝。'}
      </Hint>
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        <Button variant="default" size="xs" onClick={() => void refresh()}>
          {busy ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />} 刷新
        </Button>
        {sessions.map((s) => (
          <Button
            key={s.id}
            variant={s.id === sid ? 'primary' : 'default'}
            size="xs"
            className="font-mono"
            title={s.command ?? ''}
            onClick={() => adopt(s.id, s.alive)}
          >
            {s.id}
            {s.alive === false ? '（已退出）' : ''}
          </Button>
        ))}
        <span className="ml-auto text-micro text-slate-600">
          {meta?.enabled ? `会话 ${sessions.length}/${meta.max_sessions ?? '?'}` : '开关未开'}
          {sid ? (alive ? ' · 运行中' : ' · 已退出（可继续读残余输出）') : ''}
        </span>
      </div>
      <form
        className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1"
        onSubmit={(e) => {
          e.preventDefault()
          void open()
        }}
      >
        <Input
          variant="bare"
          className="w-32 shrink-0"
          inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-micro text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="命令（空=默认 shell）"
          aria-label="启动命令"
        />
        <Input
          variant="bare"
          className="min-w-0 flex-1"
          inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-micro text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          placeholder="工作目录（空=服务端当前目录）"
          aria-label="工作目录"
        />
        <Button type="submit" variant="default" size="xs" disabled={busy}>
          <Play size={10} /> 打开
        </Button>
      </form>
      {notice && <p className="px-3 py-1 text-micro text-amber-300/90">{notice}</p>}
      {error && <p className="px-3 py-1 text-micro text-rose-300">{error}</p>}
      <pre
        ref={boxRef}
        className="m-0 min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words bg-slate-950/60 px-2 py-1 font-mono text-micro leading-relaxed text-slate-300"
      >
        {out || (sid ? '（等待输出…）' : '尚未打开会话')}
      </pre>
      <form
        className="flex shrink-0 items-center gap-1 border-t border-slate-800/60 px-2 py-1"
        onSubmit={(e) => {
          e.preventDefault()
          void send()
        }}
      >
        <Input
          variant="bare"
          className="min-w-0 flex-1"
          inputClassName="rounded border border-slate-800 bg-slate-950/70 px-1.5 py-0.5 font-mono text-micro text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-hidden"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={sid ? '输入后回车发送（自动补回车）' : '先打开一个会话'}
          aria-label="终端输入"
          disabled={!sid}
        />
        <Button type="submit" variant="primary" size="xs" disabled={!sid}>
          <Send size={10} /> 发送
        </Button>
        <Button type="button" variant="default" size="xs" disabled={!sid} onClick={() => void signal('interrupt')}>
          Ctrl-C
        </Button>
        <Button
          type="button"
          variant="default"
          size="xs"
          className="border-danger/60 text-danger hover:bg-danger/10"
          disabled={!sid}
          onClick={() => void close()}
        >
          关闭
        </Button>
      </form>
    </Pane>
  )
}

/** 审计链 ts 是 UTC（+00:00）；必须换算成**浏览器本地时区**再显示。
 *  【不易】原来的 r.ts.slice(11, 19) 直接截 UTC 串：东八区会整整差 8 小时。 */
export function formatActivityClock(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts || '').slice(11, 19)
  const pad = (n: number) => String(n).padStart(2, '0')
  return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds())
}

/** 悬浮提示用：完整本地日期时间（同样的 UTC 串，经浏览器时区换算） */
function activityTitle(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts || '')
  return d.toLocaleString('zh-CN', { hour12: false })
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
    // 【2026-11-14】后进置顶：最新一条在最上面，新行到达时回到顶部（原来是滚到底）
    if (el) el.scrollTop = 0
  }, [rows.length])

  return (
    <>
      <Hint>
        审计链尾部（工具调用 / 技能加载 / 调度执行 / UI 写操作）—— 这就是“云枢此刻在做什么”。
        <span className="text-slate-400">最新的在最上面（后进置顶）。</span>
      </Hint>
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-800/60 px-2 py-1">
        <Button variant="default" size="xs" onClick={() => setPaused((p) => !p)}>
          {paused ? '继续' : '暂停'}
        </Button>
        <Button
          variant="default"
          size="xs"
          onClick={() => {
            setRows([])
            lastSeq.current = 0
          }}
        >
          清空
        </Button>
        <span className="ml-auto font-mono text-micro text-slate-600">
          seq {lastSeq.current || '—'} · {rows.length} 条
        </span>
      </div>
      {inflight.length > 0 && (
        <div className="shrink-0 border-b border-slate-800/60 bg-amber-500/5 px-2 py-1">
          <div className="text-micro uppercase tracking-wider text-amber-300/80">进行中的委派</div>
          {inflight.map((f, i) => (
            <div key={f.task_id ?? i} className="truncate text-micro text-amber-200/90" title={f.goal ?? ''}>
              {f.subagent ?? '—'} · {f.goal ?? ''}
            </div>
          ))}
        </div>
      )}
      {error && <p className="px-3 py-1 text-micro text-rose-300">{error}</p>}
      <div ref={boxRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1 font-mono text-micro leading-relaxed">
        {rows.length === 0 ? (
          <p className="text-slate-500">暂无活动（有新执行时会自动出现）</p>
        ) : (
          rows.slice().reverse().map((r) => (
            <div key={r.seq} className="flex gap-1.5 py-0.5">
              <span className="shrink-0 text-slate-600" title={activityTitle(r.ts)}>
                {formatActivityClock(r.ts)}
              </span>
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
