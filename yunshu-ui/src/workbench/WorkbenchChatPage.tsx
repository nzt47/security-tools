/**
 * WorkbenchChatPage —— 统一工作台"会话任务"页
 * ------------------------------------------------
 * 直接复用 workbench 的 ChatPanel（真实 SSE 流式对话）：
 * - 主内容区：流式渲染 Markdown / 代码块（Framer Motion 平滑入场）
 * - 支持停止生成（AbortController 中断 SSE）
 * - 消息/思考状态由 useLayoutStore 统一管理（与右侧思考面板共享）
 * - 会话持久化：挂载时加载当前会话历史（刷新不丢对话），支持切换历史会话
 * - 历史问话：头部按钮展开右侧滑出面板（HistoryDrawer）——
 *   搜索/复制/删除/点击跳转定位，数据范围为当前选中的会话
 */
import { useEffect, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { ChatPanel } from '../components/workbench/panels/ChatPanel'
import { ContextManagerBar } from '../components/workbench/panels/ContextManagerBar'
import { HistoryDrawer } from '../components/workbench/panels/HistoryDrawer'
import { useLayoutStore } from '../stores/useLayoutStore'
import { History, RotateCcw, MessageSquare, Lightbulb } from 'lucide-react'
import GenerateRequirementModal from '@/pages/hub/memory/generate-requirement-modal'

interface SessionMeta {
  id: string
  title: string
}

export default function WorkbenchChatPage() {
  const clearConversation = useLayoutStore((s) => s.clearConversation)
  const loadSessionHistory = useLayoutStore((s) => s.loadSessionHistory)
  const messageCount = useLayoutStore((s) => s.messages.length)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [sessionId, setSessionId] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [skillGenOpen, setSkillGenOpen] = useState(false)

  // 快捷入口预填：最近一条用户消息作为“对话要求”
  const lastUserMsg = useLayoutStore((s) => {
    const msgs = (s.messages ?? []) as { role?: string; content?: string }[]
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      if (msgs[i]?.role === 'user') return msgs[i]?.content ?? ''
    }
    return ''
  })

  // 挂载：加载会话列表 + 当前会话历史
  useEffect(() => {
    let cancelled = false
    fetch('/api/sessions')
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data?.sessions) ? data.sessions as SessionMeta[] : []
        setSessions(list)
        const current = data?.current_id ?? list[0]?.id ?? ''
        setSessionId(current)
        if (current) void loadSessionHistory(current)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [loadSessionHistory])

  // 切换会话：清空当前 + 加载目标会话（历史面板若开着一并关闭）
  const switchSession = (id: string) => {
    if (!id || id === sessionId) return
    setSessionId(id)
    setHistoryOpen(false)
    clearConversation()
    void loadSessionHistory(id)
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden">
      {/* 页头：会话切换 + 流式对话 + 历史问话 + 清空 */}
      <div className="flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-900/40 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare size={13} className="shrink-0 text-cyan-400" />
          <select
            value={sessionId}
            onChange={(e) => switchSession(e.target.value)}
            className="min-w-0 max-w-[220px] truncate rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-[11.5px] text-slate-300 outline-none focus:border-cyan-600"
            title="切换历史会话"
          >
            {sessions.length === 0 && <option value="">无历史会话</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>{s.title}</option>
            ))}
          </select>
          <span className="shrink-0 rounded-full bg-slate-800 px-2 py-0.5 text-[10px] text-slate-500">
            SSE 流式
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => setSkillGenOpen(true)}
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
              onClick={clearConversation}
              className="flex shrink-0 items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
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

      {/* 历史问话侧滑面板 */}
      <AnimatePresence>
        {historyOpen && (
          <HistoryDrawer
            sessionId={sessionId}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </AnimatePresence>

      {/* 从对话要求生成技能（自动评审-消化） */}
      {skillGenOpen && (
        <GenerateRequirementModal
          initialIntent={lastUserMsg}
          onClose={() => setSkillGenOpen(false)}
          onDone={() => setSkillGenOpen(false)}
        />
      )}
    </div>
  )
}
