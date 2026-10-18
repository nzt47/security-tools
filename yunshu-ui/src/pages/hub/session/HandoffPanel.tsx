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
import { hubPost } from '../components/ui'
import { HANDOFF } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

export default function HandoffPanel({ sessionId }: { sessionId: string }) {
  const [intent, setIntent] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<Record<string, unknown> | null>(null)

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

  const markdown = typeof result?.markdown === 'string' ? (result.markdown as string) : ''
  const path = typeof result?.path === 'string' ? (result.path as string) : ''

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <FileText size={12} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">会话交接文档</h3>
        <span className="text-[10px] text-slate-500">把当前会话压缩成 Markdown（含脱敏），落盘到临时目录</span>
      </div>
      {err && <div className="mb-2 rounded border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
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
      {markdown && <pre className={PRE + ' mt-2'}>{markdown.slice(0, 4000)}</pre>}
      {!markdown && result && <pre className={PRE + ' mt-2'}>{JSON.stringify(result, null, 2).slice(0, 4000)}</pre>}
    </div>
  )
}
