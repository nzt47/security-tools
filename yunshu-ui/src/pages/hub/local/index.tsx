/**
 * 本地资源 —— 文件浏览 / 工作区 / 进程管控（14 条）
 * ------------------------------------------------------------------
 *   文件浏览 /api/filesystem/list|info|search + read|write(POST)
 *   工作区   /api/workspace + info + write|delete(POST)
 *   进程管控 /api/process/whitelist + start|stop + whitelist/add|remove
 *
 * 【这一页为什么必须"路径显式 + 写前确认"】
 * 它们直接读写本机文件、启动/终止进程 —— 是整套工作台里权限最重的面。
 * 故：
 *   1. 目录浏览永远显示**绝对路径**（abs_path），不接受"看起来像对"的相对路径；
 *   2. 写文件与删除是两步（编辑内容 → 勾确认 → 提交），且后端还会过安全守卫；
 *   3. 进程只能从白名单里起，停止必须给 pid —— 不给"按名字猜着杀"的入口。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  FileText, FolderOpen, HardDrive, Loader2, Play, RefreshCw, Search, Trash2, XOctagon,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  FILESYSTEM_INFO,
  FILESYSTEM_LIST,
  FILESYSTEM_READ,
  FILESYSTEM_SEARCH,
  FILESYSTEM_WRITE,
  PROCESS_START,
  PROCESS_STOP,
  PROCESS_WHITELIST,
  PROCESS_WHITELIST_ADD,
  PROCESS_WHITELIST_REMOVE,
  WORKSPACE_ROOT,
  WORKSPACE_DELETE,
  WORKSPACE_INFO,
  WORKSPACE_WRITE,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface FsItem { name?: string; type?: string; size?: number; modified?: string }

type Tab = 'files' | 'workspace' | 'process'

export default function LocalResources() {
  const [tab, setTab] = useState<Tab>('files')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  // 文件浏览
  const [path, setPath] = useState('.')
  const [absPath, setAbsPath] = useState('')
  const [items, setItems] = useState<FsItem[]>([])
  const [fileInfo, setFileInfo] = useState<Record<string, unknown> | null>(null)
  const [searchPattern, setSearchPattern] = useState('')
  const [searchResults, setSearchResults] = useState<Record<string, unknown> | null>(null)
  const [readPath, setReadPath] = useState('')
  const [readResult, setReadResult] = useState<Record<string, unknown> | null>(null)
  const [writePath, setWritePath] = useState('')
  const [writeText, setWriteText] = useState('')
  const [confirmWrite, setConfirmWrite] = useState(false)

  // 工作区
  const [wsItems, setWsItems] = useState<FsItem[]>([])
  const [wsInfo, setWsInfo] = useState<Record<string, unknown> | null>(null)
  const [wsPath, setWsPath] = useState('')
  const [wsText, setWsText] = useState('')
  const [confirmWsWrite, setConfirmWsWrite] = useState(false)
  const [confirmWsDelete, setConfirmWsDelete] = useState(false)

  // 进程
  const [wl, setWl] = useState<Record<string, unknown> | null>(null)
  const [newProgram, setNewProgram] = useState('')
  const [startProgram, setStartProgram] = useState('')
  const [startArgs, setStartArgs] = useState('')
  const [stopPid, setStopPid] = useState('')
  const [confirmStop, setConfirmStop] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadDir = useCallback(async (p = path) => {
    setBusy('list'); setErr('')
    try {
      const r = await hubGet<{ abs_path?: string; items?: FsItem[] }>(FILESYSTEM_LIST + '?path=' + encodeURIComponent(p))
      setItems(r?.items ?? [])
      setAbsPath(r?.abs_path ?? p)
    } catch (e) { fail('列目录失败', e) } finally { setBusy('') }
  }, [path])

  const loadWs = useCallback(async () => {
    setBusy('ws'); setErr('')
    try {
      const [l, i] = await Promise.all([
        hubGet<{ items?: FsItem[] }>(WORKSPACE_ROOT),
        hubGet<Record<string, unknown>>(WORKSPACE_INFO),
      ])
      setWsItems(l?.items ?? [])
      setWsInfo(i)
    } catch (e) { fail('读取工作区失败', e) } finally { setBusy('') }
  }, [])

  const loadProcess = useCallback(async () => {
    setBusy('proc'); setErr('')
    try {
      const r = await hubGet<Record<string, unknown>>(PROCESS_WHITELIST)
      setWl(r)
    } catch (e) { fail('读取进程白名单失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadDir(); void loadWs(); void loadProcess() }, [loadDir, loadWs, loadProcess])

  const info = async (p: string) => {
    setBusy('info'); setErr(''); setFileInfo(null)
    try {
      const r = await hubGet<Record<string, unknown>>(FILESYSTEM_INFO + '?path=' + encodeURIComponent(p))
      setFileInfo(r)
    } catch (e) { fail('读取文件信息失败', e) } finally { setBusy('') }
  }

  const search = async () => {
    setBusy('search'); setErr(''); setSearchResults(null)
    try {
      const r = await hubGet<Record<string, unknown>>(
        FILESYSTEM_SEARCH + '?pattern=' + encodeURIComponent(searchPattern) + '&root_path=' + encodeURIComponent(path || '.'),
      )
      setSearchResults(r)
    } catch (e) { fail('搜索失败', e) } finally { setBusy('') }
  }

  const readFile = async () => {
    setBusy('read'); setErr(''); setReadResult(null)
    try {
      const r = await hubPost<Record<string, unknown>>(FILESYSTEM_READ, { path: readPath })
      setReadResult(r)
    } catch (e) { fail('读取文件失败', e) } finally { setBusy('') }
  }

  const writeFile = async () => {
    setBusy('write'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; blocked?: boolean; safety?: unknown }>(FILESYSTEM_WRITE, {
        path: writePath, content: writeText,
      })
      if (r?.ok === false) {
        setErr('写入被拒绝：' + (r.error || 'ok=false') + (r.blocked ? '（安全守卫拦截：' + JSON.stringify(r.safety) + '）' : ''))
        return
      }
      setMsg('已写入：' + writePath)
      setConfirmWrite(false)
      await loadDir()
    } catch (e) { fail('写入失败', e) } finally { setBusy('') }
  }

  const wsWrite = async () => {
    setBusy('ws-write'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; blocked?: boolean }>(WORKSPACE_WRITE, { path: wsPath, content: wsText })
      if (r?.ok === false) { setErr('写入工作区被拒绝：' + (r.error || 'ok=false')); return }
      setMsg('已写入工作区：' + wsPath)
      setConfirmWsWrite(false)
      await loadWs()
    } catch (e) { fail('写入工作区失败', e) } finally { setBusy('') }
  }

  const wsDelete = async () => {
    setBusy('ws-del'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(WORKSPACE_DELETE, { path: wsPath })
      if (r?.ok === false) { setErr('删除失败：' + (r.error || 'ok=false')); return }
      setMsg('已删除：' + wsPath)
      setConfirmWsDelete(false)
      await loadWs()
    } catch (e) { fail('删除失败', e) } finally { setBusy('') }
  }

  const startProcess = async () => {
    setBusy('start'); setErr(''); setMsg('')
    try {
      const body: Record<string, unknown> = { program: startProgram.trim() }
      if (startArgs.trim()) body.args = startArgs.trim().split(/\s+/)
      const r = await hubPost<{ ok?: boolean; error?: string; pid?: number }>(PROCESS_START, body)
      if (r?.ok === false) { bad('启动失败', r); return }
      setMsg('已启动 ' + startProgram + (r?.pid ? '（pid=' + r.pid + '）' : ''))
    } catch (e) { fail('启动失败', e) } finally { setBusy('') }
  }

  const stopProcess = async () => {
    setBusy('stop'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(PROCESS_STOP, { pid: Number(stopPid) })
      if (r?.ok === false) { bad('终止失败', r); return }
      setMsg('已终止 pid=' + stopPid)
      setConfirmStop(false)
      setStopPid('')
    } catch (e) { fail('终止失败', e) } finally { setBusy('') }
  }

  const addWl = async () => {
    setBusy('wl-add'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(PROCESS_WHITELIST_ADD, { program: newProgram.trim() })
      if (r?.ok === false) { bad('添加白名单失败', r); return }
      setMsg('已加入白名单：' + newProgram)
      setNewProgram('')
      await loadProcess()
    } catch (e) { fail('添加白名单失败', e) } finally { setBusy('') }
  }

  const removeWl = async (program: string) => {
    setBusy('wl-remove'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(PROCESS_WHITELIST_REMOVE, { program })
      if (r?.ok === false) { bad('移除白名单失败', r); return }
      setMsg('已移出白名单：' + program)
      await loadProcess()
    } catch (e) { fail('移除白名单失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const custom = (wl?.custom ?? []) as string[]
  const all = (wl?.all ?? []) as string[]

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <HardDrive size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">本地资源</h2>
        <span className="text-[11px] text-slate-500">文件浏览 / 工作区 / 进程管控 —— 写与删都需二次确认</span>
        <div className="ml-auto">
          <button type="button" className={BTN} onClick={() => { void loadDir(); void loadWs(); void loadProcess() }} disabled={loading} aria-label="刷新本地资源">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 flex items-center gap-1">
        {([['files', '文件浏览'], ['workspace', '工作区'], ['process', '进程管控']] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-selected={tab === id}
            className={'rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
              tab === id ? 'bg-cyan-500/15 font-medium text-cyan-300' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'files' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <FolderOpen size={12} className="text-cyan-400" />
              <input className={INPUT + ' w-72'} value={path} onChange={(e) => setPath(e.target.value)} aria-label="目录路径" />
              <button type="button" className={BTN} onClick={() => void loadDir()} disabled={loading} aria-label="打开目录">打开</button>
              <span className="font-mono text-[10px] text-slate-500">{absPath}</span>
            </div>
            <div className="flex flex-col gap-1">
              {items.map((it, i) => (
                <div key={(it.name ?? '') + i} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="text-slate-300">{it.name ?? ''}</span>
                  <span className="text-[10px] text-slate-600">{it.type ?? ''} · {it.size ?? 0}B</span>
                  <span className="ml-auto flex items-center gap-1">
                    <button type="button" className={BTN} onClick={() => void info((path || '.') + '/' + (it.name ?? ''))} disabled={loading} aria-label={'查看 ' + (it.name ?? '')}>
                      信息
                    </button>
                    {it.type === 'dir' ? (
                      <button type="button" className={BTN} onClick={() => { const next = (path || '.') + '/' + (it.name ?? ''); setPath(next); void loadDir(next) }} aria-label={'进入 ' + (it.name ?? '')}>
                        进入
                      </button>
                    ) : (
                      <button type="button" className={BTN} onClick={() => setReadPath((path || '.') + '/' + (it.name ?? ''))} aria-label={'选择读取 ' + (it.name ?? '')}>
                        选中
                      </button>
                    )}
                  </span>
                </div>
              ))}
              {items.length === 0 && <div className="text-[11px] text-slate-500">空目录或路径不存在。</div>}
            </div>
            {fileInfo && <pre className={PRE + ' mt-2'}>{JSON.stringify(fileInfo, null, 2)}</pre>}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Search size={12} className="text-cyan-400" />
              <input className={INPUT + ' w-56'} value={searchPattern} onChange={(e) => setSearchPattern(e.target.value)} placeholder="文件名/通配" aria-label="搜索模式" />
              <button type="button" className={BTN} onClick={() => void search()} disabled={loading || searchPattern.trim() === ''} aria-label="搜索文件">搜索</button>
            </div>
            {searchResults && <pre className={PRE}>{JSON.stringify(searchResults, null, 2)}</pre>}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <FileText size={11} className="text-cyan-400" /> 读取
              <input className={INPUT + ' w-80'} value={readPath} onChange={(e) => setReadPath(e.target.value)} placeholder="文件绝对/相对路径" aria-label="读取文件路径" />
              <button type="button" className={BTN_EM} onClick={() => void readFile()} disabled={loading || readPath.trim() === ''} aria-label="读取文件">读取</button>
            </div>
            {readResult && <pre className={PRE}>{JSON.stringify(readResult, null, 2)}</pre>}
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-red-300">
              写入（会覆盖同名文件；后端还会过一次安全守卫）
            </div>
            <input className={INPUT + ' mb-2 w-full'} value={writePath} onChange={(e) => setWritePath(e.target.value)} placeholder="目标文件路径" aria-label="写入文件路径" />
            <textarea className={INPUT + ' h-28 w-full font-mono'} value={writeText} onChange={(e) => setWriteText(e.target.value)} placeholder="文件内容" aria-label="写入文件内容" />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmWrite} onChange={(e) => setConfirmWrite(e.target.checked)} aria-label="确认写入文件" />
                我确认覆盖写入
              </label>
              <button type="button" className={BTN_RED} onClick={() => void writeFile()} disabled={loading || !confirmWrite || writePath.trim() === ''} aria-label="写入文件">
                {busy === 'write' ? <Loader2 size={11} className="animate-spin" /> : null} 写入
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === 'workspace' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
              <FolderOpen size={11} className="text-cyan-400" /> 工作区（{wsItems.length} 项）
            </div>
            {wsInfo && <pre className={PRE + ' mb-2'}>{JSON.stringify(wsInfo, null, 2)}</pre>}
            <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
              {wsItems.map((it, i) => (
                <div key={(it.name ?? '') + i} className="flex items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="text-slate-300">{it.name ?? ''}</span>
                  <span className="text-[10px] text-slate-600">{it.type ?? ''} · {it.size ?? 0}B</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 text-[11px] text-red-300">写入 / 删除工作区文件（相对路径，不能逃出工作区）</div>
            <input className={INPUT + ' mb-2 w-full'} value={wsPath} onChange={(e) => setWsPath(e.target.value)} placeholder="相对路径，如 notes/todo.md" aria-label="工作区路径" />
            <textarea className={INPUT + ' h-24 w-full font-mono'} value={wsText} onChange={(e) => setWsText(e.target.value)} placeholder="内容" aria-label="工作区文件内容" />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmWsWrite} onChange={(e) => setConfirmWsWrite(e.target.checked)} aria-label="确认写入工作区" />
                我确认写入
              </label>
              <button type="button" className={BTN_EM} onClick={() => void wsWrite()} disabled={loading || !confirmWsWrite || wsPath.trim() === ''} aria-label="写入工作区">
                {busy === 'ws-write' ? <Loader2 size={11} className="animate-spin" /> : null} 写入
              </button>
              <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmWsDelete} onChange={(e) => setConfirmWsDelete(e.target.checked)} aria-label="确认删除工作区文件" />
                我确认删除
              </label>
              <button type="button" className={BTN_RED} onClick={() => void wsDelete()} disabled={loading || !confirmWsDelete || wsPath.trim() === ''} aria-label="删除工作区文件">
                <Trash2 size={11} /> 删除
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === 'process' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              <Play size={11} className="text-cyan-400" /> 启动白名单程序
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select className={INPUT} value={startProgram} onChange={(e) => setStartProgram(e.target.value)} aria-label="选择程序">
                <option value="">选择程序</option>
                {all.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <input className={INPUT + ' w-56'} value={startArgs} onChange={(e) => setStartArgs(e.target.value)} placeholder="参数（空格分隔，可空）" aria-label="启动参数" />
              <button type="button" className={BTN_EM} onClick={() => void startProcess()} disabled={loading || startProgram.trim() === ''} aria-label="启动进程">
                {busy === 'start' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 启动
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-red-300">
              <XOctagon size={11} /> 终止进程（必须给 pid，只终止白名单内的）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input className={INPUT + ' w-40'} value={stopPid} onChange={(e) => setStopPid(e.target.value)} placeholder="pid" aria-label="进程 pid" />
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={confirmStop} onChange={(e) => setConfirmStop(e.target.checked)} aria-label="确认终止进程" />
                我确认终止
              </label>
              <button type="button" className={BTN_RED} onClick={() => void stopProcess()} disabled={loading || !confirmStop || stopPid.trim() === ''} aria-label="终止进程">
                {busy === 'stop' ? <Loader2 size={11} className="animate-spin" /> : <XOctagon size={11} />} 终止
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
              白名单（自定义 {custom.length} / 合计 {all.length}）
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <input className={INPUT + ' w-56'} value={newProgram} onChange={(e) => setNewProgram(e.target.value)} placeholder="program（如 git.exe）" aria-label="新增白名单程序" />
              <button type="button" className={BTN} onClick={() => void addWl()} disabled={loading || newProgram.trim() === ''} aria-label="添加白名单">加入</button>
            </div>
            <div className="flex flex-wrap gap-1">
              {custom.map((p) => (
                <span key={p} className="flex items-center gap-1 rounded bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                  {p}
                  <button type="button" className="text-red-400 hover:text-red-300" onClick={() => void removeWl(p)} aria-label={'移除白名单 ' + p}>×</button>
                </span>
              ))}
              {custom.length === 0 && <span className="text-[10px] text-slate-600">还没有自定义条目（默认白名单不可移除）</span>}
            </div>
            <pre className={PRE + ' mt-2'}>{JSON.stringify(wl ?? {}, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
