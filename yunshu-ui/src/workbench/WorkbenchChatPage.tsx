/**
 * WorkbenchChatPage —— 统一工作台"会话任务"页（仿 DSH 会话界面）
 * ------------------------------------------------
 * 双栏布局：
 *  - 左侧 SessionRail：会话列表（新建/搜索/切换/重命名/删除/任务工作空间入口），
 *    选中态 + 相对时间 + 消息数，与后端 /api/sessions 双向同步；
 *  - 右侧对话区：当前会话标题 + ChatPanel（真实 SSE 流式）+ ContextManagerBar；
 *  - 抽屉：HistoryDrawer（历史问话）、WorkspaceDrawer（任务工作空间）。
 *
 * 会话持久化链路（2026-09-07 修复）：
 *  1) 页面激活会话时 store.activeSessionId 同步 → sendMessage 携带 session_id
 *     → 后端 /api/chat/stream 按会话落盘（此前不落盘，刷新即丢）；
 *  2) 流结束后刷新会话列表（标题自动命名/消息数/活跃时间回显）；
 *  3) 当前会话 ID 记忆在 localStorage，重开页面自动恢复；
 *  4) 新建/删除会话直接调后端 API，删除当前会话后自动落到最近会话或新建。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { FolderOpen, History, Lightbulb, MessageSquare, RotateCcw } from 'lucide-react'
import { ChatPanel } from '../components/workbench/panels/ChatPanel'
import { ContextManagerBar } from '../components/workbench/panels/ContextManagerBar'
import { HistoryDrawer } from '../components/workbench/panels/HistoryDrawer'
import { SessionRail } from '../components/workbench/sessions/SessionRail'
import { WorkspaceDrawer } from '../components/workbench/sessions/WorkspaceDrawer'
import {
  pickSessionId,
  sessionApi,
  type SessionGroup,
  type SessionMeta,
} from '../lib/sessionApi'
import { ApiError } from '../lib/apiClient'
import { useLayoutStore } from '../stores/useLayoutStore'
import GenerateRequirementModal from '@/pages/hub/memory/generate-requirement-modal'

/** 最近一次使用的会话（localStorage 记忆，重开页面恢复） */
const LAST_SESSION_KEY = 'yunshu.session.last'

/** 把 API 错误转成可读提示：401（FLASK_API_TOKEN 保护）给出配置指引 */
function toHumanError(e: unknown): string {
  if (e instanceof ApiError && e.status === 401) {
    return '写操作需要 API 令牌（401）：请在「系统组件 → 插件管理 → API 令牌」填入 FLASK_API_TOKEN 后重试'
  }
  return e instanceof Error ? e.message : String(e)
}

function getLastUsedSession(): string | null {
  try {
    return localStorage.getItem(LAST_SESSION_KEY)
  } catch {
    return null
  }
}

function setLastUsedSession(id: string | null) {
  try {
    if (id) localStorage.setItem(LAST_SESSION_KEY, id)
    else localStorage.removeItem(LAST_SESSION_KEY)
  } catch {
    /* localStorage 不可用时静默 */
  }
}

export default function WorkbenchChatPage() {
  const clearConversation = useLayoutStore((s) => s.clearConversation)
  const loadSessionHistory = useLayoutStore((s) => s.loadSessionHistory)
  const setActiveSession = useLayoutStore((s) => s.setActiveSession)
  const messageCount = useLayoutStore((s) => s.messages.length)
  const streaming = useLayoutStore((s) => s.streaming)

  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [sessionId, setSessionId] = useState('')
  const [groups, setGroups] = useState<SessionGroup[]>([])
  const [membership, setMembership] = useState<Record<string, string>>({})
  const [listBusy, setListBusy] = useState(true)
  const [listError, setListError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [wsSession, setWsSession] = useState<SessionMeta | null>(null)
  const [skillGenOpen, setSkillGenOpen] = useState(false)
  const [genIntent, setGenIntent] = useState<string>('')
  const [ctx, setCtx] = useState<{ x: number; y: number; text: string } | null>(null)
  const errorTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const streamRefreshSkippedFirst = useRef(true)

  // 快捷入口预填：最近一条用户消息作为“对话要求”
  const lastUserMsg = useLayoutStore((s) => {
    const msgs = (s.messages ?? []) as { role?: string; content?: string }[]
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      if (msgs[i]?.role === 'user') return msgs[i]?.content ?? ''
    }
    return ''
  })

  useEffect(() => () => {
    if (errorTimer.current) clearTimeout(errorTimer.current)
  }, [])

  /** 瞬态错误提示（显示在会话栏顶部，3 秒后消失） */
  const flashError = useCallback((msg: string) => {
    setListError(msg)
    if (errorTimer.current) clearTimeout(errorTimer.current)
    errorTimer.current = setTimeout(() => setListError(null), 3200)
  }, [])

  /** 拉取分组 + 归属表（分组数据非关键，失败静默，下次窗口再同步） */
  const syncGroupsState = useCallback(async () => {
    try {
      const resp = await sessionApi.groups()
      setGroups(Array.isArray(resp.groups) ? resp.groups : [])
      setMembership(
        resp.membership && typeof resp.membership === 'object' ? resp.membership : {},
      )
    } catch {
      /* 静默 */
    }
  }, [])

  /**
   * 激活会话：本地选中态 + store 归属（决定消息落盘会话）+ 后端当前态同步 +
   * 清空并加载该会话历史。会话不同时触发加载；相同 id 仅幂等刷新归属。
   */
  const activateSession = useCallback(
    (id: string, opts?: { reload?: boolean }) => {
      if (!id) return
      const changed = id !== useLayoutStore.getState().activeSessionId
      setSessionId(id)
      setActiveSession(id)
      setLastUsedSession(id)
      setHistoryOpen(false)
      if (changed || opts?.reload) {
        clearConversation()
        void loadSessionHistory(id)
        void sessionApi.setCurrent(id).catch(() => {})
      }
    },
    [clearConversation, loadSessionHistory, setActiveSession],
  )

  // 挂载：加载会话列表并恢复上次会话（无会话时自动新建一个）
  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      setListBusy(true)
      try {
        const resp = await sessionApi.list()
        if (cancelled) return
        const list = Array.isArray(resp.sessions) ? resp.sessions : []
        setSessions(list)
        setListError(null)
        let target = pickSessionId(resp.current_id, list, getLastUsedSession())
        if (!target) {
          // 后端启动通常会保证至少一个会话；极端空库下自动新建，保证可直接对话
          try {
            const created = await sessionApi.create('新会话')
            if (cancelled) return
            setSessions([created])
            target = created.id
          } catch {
            /* 保持空列表，用户可手动新建 */
          }
        }
        if (cancelled) return
        if (target) {
          setSessionId(target)
          setActiveSession(target)
          setLastUsedSession(target)
          clearConversation()
          void loadSessionHistory(target)
          void sessionApi.setCurrent(target).catch(() => {})
        } else {
          setActiveSession(null)
        }
        void syncGroupsState()
      } catch (e) {
        if (!cancelled) setListError(e instanceof Error ? e.message : '会话列表加载失败')
      } finally {
        if (!cancelled) setListBusy(false)
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [clearConversation, loadSessionHistory, setActiveSession, syncGroupsState])

  // 流结束（流式 → 空闲）后刷新会话列表：标题自动命名 / 消息数 / 活跃时间回显
  useEffect(() => {
    if (streamRefreshSkippedFirst.current) {
      streamRefreshSkippedFirst.current = false
      return
    }
    if (streaming) return
    let cancelled = false
    const sync = async () => {
      try {
        const resp = await sessionApi.list()
        if (!cancelled) setSessions(Array.isArray(resp.sessions) ? resp.sessions : [])
      } catch {
        /* 静默：下次发送/操作会再刷新 */
      }
    }
    void sync()
    return () => {
      cancelled = true
    }
  }, [streaming])

  // ── 会话操作 ──────────────────────────────────────────────

  const handleCreateSession = useCallback(async () => {
    try {
      const created = await sessionApi.create('新会话')
      setSessions((prev) => [created, ...prev.filter((s) => s.id !== created.id)])
      activateSession(created.id)
      void syncGroupsState()
    } catch (e) {
      flashError(toHumanError(e))
    }
  }, [activateSession, flashError, syncGroupsState])

  const handleRenameSession = useCallback(
    async (id: string, title: string) => {
      try {
        await sessionApi.rename(id, title)
      } catch (e) {
        flashError(toHumanError(e))
        return
      }
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title } : s)))
    },
    [flashError],
  )

  const handleDeleteSession = useCallback(
    async (id: string) => {
      if (wsSession?.id === id) setWsSession(null)
      if (id === useLayoutStore.getState().activeSessionId) {
        clearConversation()
        setActiveSession(null)
      }
      try {
        await sessionApi.remove(id)
      } catch (e) {
        flashError(toHumanError(e))
        if (id === useLayoutStore.getState().activeSessionId) setActiveSession(id)
        return
      }
      const remaining = sessions.filter((s) => s.id !== id)
      setSessions(remaining)
      // 后端已清理该会话的分组归属，同步本地 membership
      void syncGroupsState()
      if (id === sessionId) {
        setSessionId('')
        const next = remaining[0]?.id ?? null
        if (next) {
          activateSession(next)
        } else {
          // 全部删光 → 自动新建一个空白会话（保持可对话）
          try {
            const created = await sessionApi.create('新会话')
            setSessions([created])
            activateSession(created.id)
          } catch (e2) {
            flashError(toHumanError(e2))
          }
        }
      }
    },
    [
      activateSession,
      clearConversation,
      flashError,
      sessionId,
      sessions,
      setActiveSession,
      syncGroupsState,
      wsSession,
    ],
  )

  const handleOpenWorkspace = useCallback(
    (id: string) => {
      const target = sessions.find((s) => s.id === id) ?? null
      setWsSession(target)
    },
    [sessions],
  )

  const handleClearConversation = useCallback(async () => {
    const id = useLayoutStore.getState().activeSessionId
    if (!id) return
    try {
      await sessionApi.clearMessages(id)
    } catch (e) {
      flashError(toHumanError(e))
      return
    }
    clearConversation()
  }, [clearConversation, flashError])

  // ── 分组操作 ──────────────────────────────────────────────

  const handleCreateGroup = useCallback(
    async (name: string) => {
      try {
        const g = await sessionApi.createGroup(name)
        setGroups((prev) => (prev.some((x) => x.id === g.id) ? prev : [...prev, g]))
      } catch (e) {
        flashError(toHumanError(e))
      }
    },
    [flashError],
  )

  const handleRenameGroup = useCallback(
    async (id: string, name: string) => {
      try {
        await sessionApi.renameGroup(id, name)
      } catch (e) {
        flashError(toHumanError(e))
        return
      }
      setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, name } : g)))
    },
    [flashError],
  )

  const handleDeleteGroup = useCallback(
    async (id: string) => {
      try {
        await sessionApi.deleteGroup(id)
      } catch (e) {
        flashError(toHumanError(e))
        return
      }
      setGroups((prev) => prev.filter((g) => g.id !== id))
      setMembership((prev) => {
        const next = { ...prev }
        for (const [sid, gid] of Object.entries(next)) {
          if (gid === id) delete next[sid]
        }
        return next
      })
    },
    [flashError],
  )

  const handleAssignGroup = useCallback(
    async (sessionId: string, groupId: string | null) => {
      try {
        await sessionApi.assignSessionGroup(sessionId, groupId)
      } catch (e) {
        flashError(toHumanError(e))
        return
      }
      setMembership((prev) => {
        const next = { ...prev }
        if (groupId) next[sessionId] = groupId
        else delete next[sessionId]
        return next
      })
    },
    [flashError],
  )

  // 右键选中文本 → 「生成技能要求」
  const openFromSelection = () => {
    if (!ctx) return
    setGenIntent(ctx.text.slice(0, 2000))
    setCtx(null)
    setSkillGenOpen(true)
  }
  useEffect(() => {
    if (!ctx) return
    const close = () => setCtx(null)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setCtx(null)
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('scroll', close, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [ctx])

  const activeTitle = sessions.find((s) => s.id === sessionId)?.title ?? (sessionId ? '…' : '未选择会话')

  return (
    <div
      className="relative flex h-full min-h-0 overflow-hidden"
      onContextMenu={(e) => {
        const sel = window.getSelection()?.toString().trim()
        if (sel) {
          e.preventDefault()
          setCtx({ x: e.clientX, y: e.clientY, text: sel })
        }
      }}
    >
      {/* ═══ 左栏：会话列表（仿 DSH，支持分组）═══ */}
      <SessionRail
        sessions={sessions}
        activeId={sessionId || null}
        groups={groups}
        membership={membership}
        busy={listBusy}
        error={listError}
        onSelect={(id) => activateSession(id)}
        onCreate={() => void handleCreateSession()}
        onRename={(id, title) => void handleRenameSession(id, title)}
        onDelete={(id) => void handleDeleteSession(id)}
        onOpenWorkspace={handleOpenWorkspace}
        onCreateGroup={(name) => void handleCreateGroup(name)}
        onRenameGroup={(id, name) => void handleRenameGroup(id, name)}
        onDeleteGroup={(id) => void handleDeleteGroup(id)}
        onAssignGroup={(sid, gid) => void handleAssignGroup(sid, gid)}
      />

      {/* ═══ 右栏：对话区 ═══ */}
      <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        {/* 页头：当前会话标题 + 操作 */}
        <div className="flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-900/40 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <MessageSquare size={13} className="shrink-0 text-cyan-400" />
            <span className="min-w-0 max-w-[260px] truncate text-[12px] font-medium text-slate-200" title={activeTitle}>
              {activeTitle}
            </span>
            <span className="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-[10px] text-slate-500">
              SSE 流式
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {sessionId && (
              <button
                type="button"
                onClick={() => handleOpenWorkspace(sessionId)}
                aria-label="打开任务工作空间"
                title="任务工作空间：查看/打开当前会话的工作目录"
                className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 transition-colors hover:border-amber-500/50 hover:bg-amber-500/10 hover:text-amber-300"
              >
                <FolderOpen size={11} />
                工作空间
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setGenIntent('')
                setSkillGenOpen(true)
              }}
              title="把对话中出现的可用能力要求直接生成技能草稿（自动评审-消化；预填最近一条用户消息）"
              className="flex items-center gap-1.5 rounded-md border border-cyan-700/60 px-2.5 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
            >
              <Lightbulb size={11} />
              技能要求
            </button>
            <button
              type="button"
              onClick={() => setHistoryOpen(true)}
              title="打开历史问话面板（当前会话）"
              aria-expanded={historyOpen}
              className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 transition-colors hover:border-cyan-600/60 hover:bg-slate-800 hover:text-cyan-300"
            >
              <History size={11} />
              历史问话
            </button>
            {messageCount > 0 && (
              <button
                type="button"
                onClick={() => void handleClearConversation()}
                disabled={streaming}
                title="清空当前会话的全部消息（后端同步删除，保留会话与工作空间）"
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200 disabled:opacity-40"
              >
                <RotateCcw size={11} />
                清空对话
              </button>
            )}
          </div>
        </div>

        {/* 流式聊天主体 */}
        <div className="min-h-0 flex-1">
          <ChatPanel />
        </div>

        {/* 上下文管理器（从 legacy 对话移植） */}
        <ContextManagerBar />
      </div>

      {/* 历史问话侧滑面板 */}
      <AnimatePresence>
        {historyOpen && (
          <HistoryDrawer sessionId={sessionId} onClose={() => setHistoryOpen(false)} />
        )}
      </AnimatePresence>

      {/* 任务工作空间抽屉（右侧滑入） */}
      <AnimatePresence>
        {wsSession && <WorkspaceDrawer session={wsSession} onClose={() => setWsSession(null)} />}
      </AnimatePresence>

      {/* 右键菜单：选中文本 → 生成技能要求 */}
      {ctx && (
        <div
          className="fixed z-[80] w-56 rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-2xl"
          style={{
            left: Math.min(ctx.x, window.innerWidth - 232),
            top: Math.min(ctx.y, window.innerHeight - 96),
          }}
          onContextMenu={(e) => e.preventDefault()}
        >
          <div className="px-2 py-1 text-[10px] text-slate-500">对话内容 → 技能要求（选中 {ctx.text.length} 字）</div>
          <button
            type="button"
            onClick={openFromSelection}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11.5px] text-cyan-300 hover:bg-cyan-500/10"
          >
            <Lightbulb size={12} /> 生成技能（自动评审-消化）
          </button>
        </div>
      )}

      {/* 从对话要求生成技能（自动评审-消化） */}
      {skillGenOpen && (
        <GenerateRequirementModal
          initialIntent={genIntent || lastUserMsg}
          onClose={() => {
            setSkillGenOpen(false)
            setGenIntent('')
          }}
          onDone={() => {
            setSkillGenOpen(false)
            setGenIntent('')
          }}
        />
      )}
    </div>
  )
}
