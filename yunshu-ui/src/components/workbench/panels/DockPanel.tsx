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
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { BookOpen, Bot, Code2, Globe, Loader2, Play, RefreshCw, Terminal } from 'lucide-react'
import { CodeEditorPanel } from './CodeEditorPanel'
import { getEnvelope } from '@/api/envelope'
import { hubGet, hubPost } from '../../../pages/hub/components/ui'
import {
  FILESYSTEM_LIST,
  FILESYSTEM_READ,
  SUBAGENT_HISTORY,
  SUBAGENT_LIST,
  TERMINAL_COMMANDS,
  TERMINAL_EXEC,
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

function BrowserPane() {
  const [url, setUrl] = useState('http://127.0.0.1:5678/')
  const [current, setCurrent] = useState('')
  const [error, setError] = useState('')
  return (
    <Pane>
      <Hint>
        同源内嵌浏览。跨域站点常因 <code className="text-slate-400">X-Frame-Options</code> /
        CSP 拒绝被嵌入，属浏览器安全策略，不是本页故障。
      </Hint>
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
      </form>
      {error && <p className="px-3 py-1 text-[10px] text-rose-300">{error}</p>}
      <div className="min-h-0 flex-1">
        {current ? (
          <iframe
            key={current}
            title="浏览器"
            src={current}
            className="h-full w-full border-0 bg-white"
            sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-downloads"
          />
        ) : (
          <p className="px-3 py-4 text-center text-[10.5px] text-slate-500">输入地址后点「打开」</p>
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
    // 只在首次挂载拉一次清单（picked 变化不必重拉）
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
    <Pane>
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
    </Pane>
  )
}
