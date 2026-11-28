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
  FileText, FolderOpen, GitBranch, HardDrive, Loader2, Play, RefreshCw, Search, Trash2, XOctagon,
} from 'lucide-react'
import { Button, Checkbox, CodeBlock, ErrorBox, hubGet, hubPost, Input, ModalBase, Notice, PageTabsHeader, Select, Tabs, Textarea } from '../components/ui'
import {
  FILESYSTEM_INFO,
  FILESYSTEM_LIST,
  FILESYSTEM_READ,
  FILESYSTEM_SEARCH,
  FILESYSTEM_WRITE,
  GIT_WORKTREES,
  GIT_WORKTREES_COMMIT,
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

const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

interface FsItem { name?: string; type?: string; size?: number; modified?: string }

/** E4-5：worktree 只读投影（后端 /api/git/worktrees；默认关） */
interface WorktreeRow {
  name?: string
  path?: string
  branch?: string
  head?: string
  detached?: boolean
  locked?: boolean
  dirty?: boolean
  counts?: Record<string, number>
  diff_stat?: string
  diff_truncated?: boolean
}

interface WorktreeDoc {
  ok?: boolean
  code?: string
  error?: string
  root?: string
  truncated?: boolean
  worktrees?: WorktreeRow[]
  /** E4-5 第六批续：写路径是否已开（B 级开关 CP_WORKTREE_WRITE_ENABLED） */
  write_enabled?: boolean
}

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
  const [wtDoc, setWtDoc] = useState<WorktreeDoc | null>(null)
  // E4-5 第六批续：worktree 写路径（建 worktree / 本地提交；默认关）
  const [wtName, setWtName] = useState('')
  const [commitFor, setCommitFor] = useState<WorktreeRow | null>(null)
  const [commitMsg, setCommitMsg] = useState('')
  const [wtBusy, setWtBusy] = useState(false)
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

  const loadWorktrees = useCallback(async () => {
    setBusy('worktrees'); setErr('')
    try {
      const r = await hubGet<WorktreeDoc>(GIT_WORKTREES)
      setWtDoc(r)
    } catch (e) { fail('读取工作树失败', e) } finally { setBusy('') }
  }, [])

  /** 建 worktree（B 级写路径；后端还会校验两把钥匙与名字白名单） */
  const createWorktree = useCallback(async () => {
    const name = wtName.trim()
    if (!name) return
    setWtBusy(true); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; branch?: string }>(
        GIT_WORKTREES, { name })
      if (r?.ok === false) { bad('创建 worktree 失败', r); return }
      setMsg('已创建 worktree：' + (r?.branch || name))
      setWtName('')
      await loadWorktrees()
    } catch (e) { fail('创建 worktree 失败', e) } finally { setWtBusy(false) }
  }, [wtName, loadWorktrees])

  /** 在 worktree 里本地提交（**不推送**） */
  const commitWorktree = useCallback(async () => {
    const name = String(commitFor?.name || '')
    const message = commitMsg.trim()
    if (!name || !message) return
    setWtBusy(true); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; commit?: string }>(
        GIT_WORKTREES_COMMIT, { name, message })
      if (r?.ok === false) { bad('提交失败', r); return }
      setMsg('已提交 ' + (r?.commit || '') + '（仅本地，未推送）')
      setCommitFor(null); setCommitMsg('')
      await loadWorktrees()
    } catch (e) { fail('提交失败', e) } finally { setWtBusy(false) }
  }, [commitFor, commitMsg, loadWorktrees])

  useEffect(() => {
    void loadDir(); void loadWs(); void loadProcess(); void loadWorktrees()
  }, [loadDir, loadWs, loadProcess, loadWorktrees])

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
    <div className="flex h-full min-h-0 flex-col">
      {/* 【2026-11-21 统一】页头改 PageTabsHeader（页签上移进页头），页签族统一 folder。 */}
      <PageTabsHeader
        icon={<HardDrive size={14} className="text-primary" />}
        title="本地资源"
        hint="文件浏览 / 工作区 / 进程管控 —— 写与删都需二次确认"
        actions={(
          <Button type="button" variant="default" size="xs" onClick={() => { void loadDir(); void loadWs(); void loadProcess() }} disabled={loading} aria-label="刷新本地资源">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </Button>
        )}
        tabs={(
          <Tabs
            variant="folder"
            className="-mb-px"
            size="sm"
            value={tab}
            onChange={(v) => setTab(v as typeof tab)}
            items={([['files', '文件浏览'], ['workspace', '工作区'], ['process', '进程管控']] as const).map(([id, label]) => ({ value: id, label }))}
          />
        )}
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">

      {err && <ErrorBox message={err} />}
      {msg && <Notice tone="success" size="sm" className="mb-2">{msg}</Notice>}

      {/* 页签已上移到 PageTabsHeader（2026-11-21） */}


      {tab === 'files' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <FolderOpen size={12} className="text-cyan-400" />
              <Input size="sm" className="w-72" value={path} onChange={(e) => setPath(e.target.value)} aria-label="目录路径" />
              <Button type="button" variant="default" size="xs" onClick={() => void loadDir()} disabled={loading} aria-label="打开目录">打开</Button>
              <span className="font-mono text-micro text-slate-500">{absPath}</span>
            </div>
            <div className="flex flex-col gap-1">
              {items.map((it, i) => (
                <div key={(it.name ?? '') + i} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-caption">
                  <span className="text-slate-300">{it.name ?? ''}</span>
                  <span className="text-micro text-slate-600">{it.type ?? ''} · {it.size ?? 0}B</span>
                  <span className="ml-auto flex items-center gap-1">
                    <Button type="button" variant="default" size="xs" onClick={() => void info((path || '.') + '/' + (it.name ?? ''))} disabled={loading} aria-label={'查看 ' + (it.name ?? '')}>
                      信息
                    </Button>
                    {it.type === 'dir' ? (
                      <Button type="button" variant="default" size="xs" onClick={() => { const next = (path || '.') + '/' + (it.name ?? ''); setPath(next); void loadDir(next) }} aria-label={'进入 ' + (it.name ?? '')}>
                        进入
                      </Button>
                    ) : (
                      <Button type="button" variant="default" size="xs" onClick={() => setReadPath((path || '.') + '/' + (it.name ?? ''))} aria-label={'选择读取 ' + (it.name ?? '')}>
                        选中
                      </Button>
                    )}
                  </span>
                </div>
              ))}
              {items.length === 0 && <div className="text-caption text-slate-500">空目录或路径不存在。</div>}
            </div>
            {fileInfo && <CodeBlock maxH={256} className="mt-2">{JSON.stringify(fileInfo, null, 2)}</CodeBlock>}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Search size={12} className="text-cyan-400" />
              <Input size="sm" className="w-56" value={searchPattern} onChange={(e) => setSearchPattern(e.target.value)} placeholder="文件名/通配" aria-label="搜索模式" />
              <Button type="button" variant="default" size="xs" onClick={() => void search()} disabled={loading || searchPattern.trim() === ''} aria-label="搜索文件">搜索</Button>
            </div>
            {searchResults && <CodeBlock maxH={256}>{JSON.stringify(searchResults, null, 2)}</CodeBlock>}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-slate-400">
              <FileText size={11} className="text-cyan-400" /> 读取
              <Input size="sm" className="w-80" value={readPath} onChange={(e) => setReadPath(e.target.value)} placeholder="文件绝对/相对路径" aria-label="读取文件路径" />
              <Button type="button" variant="accent" size="xs" onClick={() => void readFile()} disabled={loading || readPath.trim() === ''} aria-label="读取文件">读取</Button>
            </div>
            {readResult && <CodeBlock maxH={256}>{JSON.stringify(readResult, null, 2)}</CodeBlock>}
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-red-300">
              写入（会覆盖同名文件；后端还会过一次安全守卫）
            </div>
            <Input size="sm" className="mb-2 w-full" value={writePath} onChange={(e) => setWritePath(e.target.value)} placeholder="目标文件路径" aria-label="写入文件路径" />
            <Textarea size="sm" className="h-28 w-full font-mono" value={writeText} onChange={(e) => setWriteText(e.target.value)} placeholder="文件内容" aria-label="写入文件内容" />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Checkbox checked={confirmWrite} onChange={(e) => setConfirmWrite(e.target.checked)} label="我确认覆盖写入" aria-label="确认写入文件" />
              <Button type="button" variant="danger-outline" size="xs" onClick={() => void writeFile()} disabled={loading || !confirmWrite || writePath.trim() === ''} aria-label="写入文件">
                {busy === 'write' ? <Loader2 size={11} className="animate-spin" /> : null} 写入
              </Button>
            </div>
          </div>
        </div>
      )}

      {tab === 'workspace' && (
        <div className="space-y-2">
          {/* E4-5：git worktree 只读投影（默认关；只读，不建/不删/不提交） */}
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-slate-400">
              <GitBranch size={11} className="text-cyan-400" /> Git 工作树（只读，{(wtDoc?.worktrees ?? []).length} 个）
              <Button type="button" variant="default" size="xs" onClick={() => void loadWorktrees()} disabled={loading} aria-label="刷新工作树">
                <RefreshCw size={11} className={busy === 'worktrees' ? 'animate-spin' : ''} /> 刷新
              </Button>
              {wtDoc?.truncated && <span className="text-micro text-amber-400/80">只显示前 20 个</span>}
            </div>
            {/* E4-5 第六批续：写路径（Owner 2026-11-28 点头；只建 worktree + 本地提交，不推送） */}
            {wtDoc && wtDoc.ok !== false && (
              wtDoc.write_enabled ? (
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Input
                    size="sm"
                    className="w-56"
                    value={wtName}
                    onChange={(e) => setWtName(e.target.value)}
                    placeholder="新 worktree 名字（小写/数字/连字符）"
                    aria-label="新 worktree 名字"
                    data-testid="worktree-create-name"
                  />
                  <Button
                    type="button"
                    variant="primary"
                    size="xs"
                    disabled={wtBusy || !wtName.trim()}
                    onClick={() => void createWorktree()}
                    data-testid="worktree-create"
                  >
                    建 worktree
                  </Button>
                  <span className="text-micro text-slate-500">只建目录与分支（yunshu/&lt;名字&gt;），不推送</span>
                </div>
              ) : (
                <div className="mb-2 text-micro text-slate-500" data-testid="worktree-write-disabled">
                  worktree 写路径未开启（CP_WORKTREE_WRITE_ENABLED=0）—— 当前只能看
                </div>
              )
            )}
            {wtDoc && wtDoc.ok === false ? (
              <div className="text-micro text-amber-400/90" data-testid="worktree-disabled">
                {wtDoc.error || 'worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）'}
              </div>
            ) : (
              <div className="flex flex-col gap-1" data-testid="worktree-list">
                {(wtDoc?.worktrees ?? []).map((w) => {
                  const counts = w.counts ?? {}
                  const changed = ['M', 'A', 'D', 'R', 'C', 'U']
                    .filter((k) => Number(counts[k] ?? 0) > 0)
                    .map((k) => k + ' ' + counts[k]).join(' · ')
                  return (
                    <div key={w.path || w.name} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-caption">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-micro text-cyan-300">{w.name || '—'}</span>
                        <span className="text-slate-300">{w.detached ? 'detached' : (w.branch || '—')}</span>
                        <span className={'text-micro ' + (w.dirty ? 'text-amber-400' : 'text-emerald-400')}>
                          {w.dirty ? '有改动' : '干净'}
                        </span>
                        {changed && <span className="font-mono text-micro text-slate-500">{changed}</span>}
                        {w.locked && <span className="text-micro text-slate-500">locked</span>}
                        {wtDoc?.write_enabled && w.dirty && (
                          <Button
                            type="button"
                            variant="default"
                            size="xs"
                            data-testid="worktree-commit-open"
                            onClick={() => { setCommitFor(w); setCommitMsg('') }}
                          >
                            提交
                          </Button>
                        )}
                        <span className="ml-auto max-w-[60%] truncate font-mono text-micro text-slate-600" title={w.path}>{w.path}</span>
                      </div>
                      {w.diff_stat && <CodeBlock maxH={96} className="mt-1">{w.diff_stat}</CodeBlock>}
                    </div>
                  )
                })}
                {wtDoc && (wtDoc.worktrees ?? []).length === 0 && (
                  <div className="text-caption text-slate-500">没有 worktree（或还没加载）。</div>
                )}
                {!wtDoc && <div className="text-caption text-slate-500">读取中…</div>}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
              <FolderOpen size={11} className="text-cyan-400" /> 工作区（{wsItems.length} 项）
            </div>
            {wsInfo && <CodeBlock maxH={256} className="mb-2">{JSON.stringify(wsInfo, null, 2)}</CodeBlock>}
            <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
              {wsItems.map((it, i) => (
                <div key={(it.name ?? '') + i} className="flex items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-caption">
                  <span className="text-slate-300">{it.name ?? ''}</span>
                  <span className="text-micro text-slate-600">{it.type ?? ''} · {it.size ?? 0}B</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 text-caption text-red-300">写入 / 删除工作区文件（相对路径，不能逃出工作区）</div>
            <Input size="sm" className="mb-2 w-full" value={wsPath} onChange={(e) => setWsPath(e.target.value)} placeholder="相对路径，如 notes/todo.md" aria-label="工作区路径" />
            <Textarea size="sm" className="h-24 w-full font-mono" value={wsText} onChange={(e) => setWsText(e.target.value)} placeholder="内容" aria-label="工作区文件内容" />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Checkbox checked={confirmWsWrite} onChange={(e) => setConfirmWsWrite(e.target.checked)} label="我确认写入" aria-label="确认写入工作区" />
              <Button type="button" variant="accent" size="xs" onClick={() => void wsWrite()} disabled={loading || !confirmWsWrite || wsPath.trim() === ''} aria-label="写入工作区">
                {busy === 'ws-write' ? <Loader2 size={11} className="animate-spin" /> : null} 写入
              </Button>
              <Checkbox checked={confirmWsDelete} onChange={(e) => setConfirmWsDelete(e.target.checked)} label="我确认删除" aria-label="确认删除工作区文件" />
              <Button type="button" variant="danger-outline" size="xs" onClick={() => void wsDelete()} disabled={loading || !confirmWsDelete || wsPath.trim() === ''} aria-label="删除工作区文件">
                <Trash2 size={11} /> 删除
              </Button>
            </div>
          </div>
        </div>
      )}

      {tab === 'process' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-slate-400">
              <Play size={11} className="text-cyan-400" /> 启动白名单程序
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select
                size="sm"
                className="w-40"
                value={startProgram}
                onChange={(v) => setStartProgram(v)}
                aria-label="选择程序"
                placeholder="选择程序"
                placeholderOptionDisabled={false}
                options={all.map((p) => ({ label: p, value: p }))}
              />
              <Input size="sm" className="w-56" value={startArgs} onChange={(e) => setStartArgs(e.target.value)} placeholder="参数（空格分隔，可空）" aria-label="启动参数" />
              <Button type="button" variant="accent" size="xs" onClick={() => void startProcess()} disabled={loading || startProgram.trim() === ''} aria-label="启动进程">
                {busy === 'start' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 启动
              </Button>
            </div>
          </div>

          <div className="rounded-lg border border-red-900/40 bg-red-500/5 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-red-300">
              <XOctagon size={11} /> 终止进程（必须给 pid，只终止白名单内的）
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Input size="sm" className="w-40" value={stopPid} onChange={(e) => setStopPid(e.target.value)} placeholder="pid" aria-label="进程 pid" />
              <Checkbox checked={confirmStop} onChange={(e) => setConfirmStop(e.target.checked)} label="我确认终止" aria-label="确认终止进程" />
              <Button type="button" variant="danger-outline" size="xs" onClick={() => void stopProcess()} disabled={loading || !confirmStop || stopPid.trim() === ''} aria-label="终止进程">
                {busy === 'stop' ? <Loader2 size={11} className="animate-spin" /> : <XOctagon size={11} />} 终止
              </Button>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-slate-400">
              白名单（自定义 {custom.length} / 合计 {all.length}）
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Input size="sm" className="w-56" value={newProgram} onChange={(e) => setNewProgram(e.target.value)} placeholder="program（如 git.exe）" aria-label="新增白名单程序" />
              <Button type="button" variant="default" size="xs" onClick={() => void addWl()} disabled={loading || newProgram.trim() === ''} aria-label="添加白名单">加入</Button>
            </div>
            <div className="flex flex-wrap gap-1">
              {custom.map((p) => (
                <span key={p} className="flex items-center gap-1 rounded-sm bg-slate-800/70 px-1.5 py-0.5 font-mono text-micro text-slate-300">
                  {p}
                  <Button type="button" variant="ghost" size="icon-xs" className="text-danger hover:text-danger" onClick={() => void removeWl(p)} aria-label={'移除白名单 ' + p}>×</Button>
                </span>
              ))}
              {custom.length === 0 && <span className="text-micro text-slate-600">还没有自定义条目（默认白名单不可移除）</span>}
            </div>
            <CodeBlock maxH={256} className="mt-2">{JSON.stringify(wl ?? {}, null, 2)}</CodeBlock>
          </div>
        </div>
      )}
      </div>

      {/* E4-5 第六批续：worktree 本地提交（B 级；不推送） */}
      <ModalBase
        open={!!commitFor}
        onClose={() => setCommitFor(null)}
        title="在 worktree 里提交"
        width="max-w-lg"
        testId="worktree-commit-dialog"
        closeButton
      >
        <div className="flex flex-col gap-2">
          <p className="text-caption text-muted-foreground">
            目标：<span className="font-mono text-foreground">{commitFor?.name || '—'}</span>
            （{commitFor?.branch || 'detached'}）。提交只发生在该 worktree 的本地分支上，
            <span className="text-foreground">不会推送</span>到任何远程。
          </p>
          <Textarea
            rows={3}
            aria-label="提交信息"
            data-testid="worktree-commit-message"
            placeholder="提交信息（≤200 字符）"
            value={commitMsg}
            onChange={(e) => setCommitMsg(e.target.value)}
          />
          <div className="flex items-center justify-end gap-2">
            <Button type="button" variant="default" size="xs" onClick={() => setCommitFor(null)}>
              取消
            </Button>
            <Button
              type="button"
              variant="primary"
              size="xs"
              data-testid="worktree-commit"
              disabled={wtBusy || !commitMsg.trim()}
              onClick={() => void commitWorktree()}
            >
              提交（仅本地）
            </Button>
          </div>
        </div>
      </ModalBase>
    </div>
  )
}