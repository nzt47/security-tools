/**
 * 会话交接文档 —— POST /api/handoff（1 条）
 * ------------------------------------------------------------------
 * 后端把指定会话压缩成 Markdown（LLM 摘要 + 三处脱敏 + 规则降级链），落盘到 OS 临时目录。
 * 这是 generate_handoff 能力**唯一**的 HTTP 入口（此前整条能力不可达）。
 *
 * 【为什么必须显式选会话、不默认"当前"】后端缺省会取"当前会话"，而工作台是个多会话
 * 应用 —— 这里传的是你在列表里点中的那一个，避免生成到别的会话上去。
 * 【为什么结果把"文件路径"和"正文"都显示】路径用于落盘留档，正文用于当次阅读/复制；
 * 只给其一都会让人多跑一次。
 */
import { useState } from 'react'
import { FileText, Loader2 } from 'lucide-react'
import { hubPost } from '../../pages/hub/components/ui'
import { HANDOFF, SESSIONS_FROM_HANDOFF } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-64 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

export default function HandoffPanel({ sessionId }: { sessionId: string }) {
  const [intent, setIntent] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  // 【2026-11-05】用这份交接文档直接开新会话（面板在 65% 提示换会话，这是它的落地动作）
  const [created, setCreated] = useState<{ id: string; title: string } | null>(null)
  const [creating, setCreating] = useState(false)

  const run = async () => {
    if (!sessionId) { setErr('先在左侧选一个会话'); return }
    setBusy(true); setErr(''); setResult(null)
    try {
      const r = await hubPost<Record<string, unknown>>(HANDOFF, {
        session_id: sessionId,
        ...(intent.trim() ? { intent: intent.trim() } : {}),
      })
      if ((r as { error?: string })?.error) { setErr('生成失败：' + String((r as { error?: string }).error)); return }
      setResult(r)
    } catch (e) {
      setErr('生成失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy(false) }
  }

  /** 用已生成的交接文档开一个新会话：后端建会话并把文档作为首条 user 消息。 */
  const createSession = async () => {
    const markdown = typeof result?.markdown === 'string' ? (result.markdown as string) : ''
    if (!markdown.trim()) { setErr('先生成交接文档'); return }
    setCreating(true); setErr(''); setCreated(null)
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; session?: { id?: string; title?: string } }>(
        SESSIONS_FROM_HANDOFF, { markdown },
      )
      if (r?.error) { setErr('建会话失败：' + r.error); return }
      const s = r?.session || {}
      setCreated({ id: String(s.id || ''), title: String(s.title || '') })
    } catch (e) {
      setErr('建会话失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setCreating(false) }
  }

  const markdown = typeof result?.markdown === 'string' ? (result.markdown as string) : ''
  const path = typeof result?.path === 'string' ? (result.path as string) : ''

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <FileText size={12} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">会话交接文档</h3>
        <span className="text-[10px] text-slate-500">把当前会话压缩成 Markdown（含脱敏），落盘到临时目录</span>
      </div>
      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      <div className="flex flex-wrap items-center gap-2">
        <input
          className={INPUT + ' w-72'}
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="下一段会话要做什么（可选，用于技能推荐）"
          aria-label="交接意图"
        />
        <button type="button" className={BTN} onClick={() => void run()} disabled={busy || !sessionId} aria-label="生成交接文档">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />} 生成交接文档
        </button>
        <span className="text-[10px] text-slate-600">会话：{sessionId || '未选中'}</span>
      </div>
      {path && (
        <div className="mt-2 text-[10px] text-slate-400">
          已落盘：<span className="font-mono text-slate-300">{path}</span>
        </div>
      )}
      {markdown && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" className={BTN} onClick={() => void createSession()}
            disabled={creating} aria-label="用交接文档开新会话"
            title="新建一个会话，并把这份交接文档作为它的第一条消息，直接接着干">
            {creating ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />} 用这份文档开新会话
          </button>
          {created && (
            <span className="text-[10px] text-emerald-300">
              已创建：{created.title || created.id}（左侧会话列表可切换）
            </span>
          )}
        </div>
      )}
      {markdown && <pre className={PRE + ' mt-2'}>{markdown.slice(0, 4000)}</pre>}
      {!markdown && result && <pre className={PRE + ' mt-2'}>{JSON.stringify(result, null, 2).slice(0, 4000)}</pre>}
    </div>
  )
}
