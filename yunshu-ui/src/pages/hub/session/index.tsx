/**
 * 会话任务 —— Hub 默认页
 * 直接复用现有聊天界面（App.tsx 的聊天逻辑由 useChatApp 提供）。
 * 此处用轻量实现：展示现有聊天核心（消息流 + 输入框），API 走 /api/chat。
 */
import { useEffect, useRef, useState } from 'react'
import { Send, Loader2 } from 'lucide-react'
import { hubGet, hubPost } from '../components/ui'

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
}

interface Session {
  id: string
  title: string
}

export default function SessionPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionId, setSessionId] = useState<string>('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    hubGet('/api/sessions').then((resp) => {
      const raw = resp as Record<string, unknown>
      // /api/sessions 返回 {current_id, sessions: [...]} 结构
      const data = (raw.sessions as Session[] | undefined) ?? (raw as unknown as Session[])
      const list = Array.isArray(data) ? data : []
      setSessions(list)
      if (list.length > 0) setSessionId(list[0].id)
      else if (raw.current_id) setSessionId(String(raw.current_id))
    }).catch(() => {})
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setMessages((m) => [...m, { id: `u-${Date.now()}`, role: 'user', content: text }])
    setLoading(true)
    try {
      const resp = await hubPost('/api/chat', { message: text, session_id: sessionId || undefined })
      const r = resp as Record<string, unknown>
      const reply = (r as { response?: string }).response ?? (r as { data?: { response?: string } }).data?.response ?? '（无响应）'
      setMessages((m) => [...m, { id: `a-${Date.now()}`, role: 'assistant', content: String(reply) }])
    } catch (e) {
      setMessages((m) => [...m, { id: `a-${Date.now()}`, role: 'assistant', content: `请求失败：${e instanceof Error ? e.message : e}` }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <h1 className="text-lg font-semibold text-white">会话任务</h1>
        <select
          value={sessionId}
          onChange={(e) => setSessionId(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-300"
        >
          {sessions.length === 0 && <option value="">无历史会话</option>}
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>{s.title}</option>
          ))}
        </select>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-600">
            <div className="text-4xl">🤖</div>
            <p className="text-sm">开始与云枢对话吧</p>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm ${
              m.role === 'user'
                ? 'bg-blue-600 text-white'
                : 'border border-slate-800 bg-slate-900 text-slate-200'
            }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-slate-500">
            <Loader2 size={14} className="animate-spin" />
            <span className="text-xs">云枢思考中…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-slate-800 px-6 py-4">
        <div className="flex items-center gap-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="输入消息，Enter 发送"
            className="flex-1 rounded-xl border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 text-sm font-medium text-white transition-colors hover:bg-blue-500 disabled:opacity-40"
          >
            <Send size={15} />
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
