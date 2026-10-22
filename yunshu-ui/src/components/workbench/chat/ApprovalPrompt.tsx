/**
 * ApprovalPrompt —— 对话内联审批弹窗（2026-10-13）
 * ------------------------------------------------------------------
 * 后端在工具循环里把"等待人工审批 / 被否决"作为独立 SSE approval 事件外发；
 * useLayoutStore 据此设置 pendingApproval，本组件据此弹窗。点「批准」/「拒绝」即调用
 * approvalActions 走完整审批链，成功后**自动替你发一句续跑指令**——不必再去审批面板，
 * 也不必手打「继续」。这就是"审批/决策自动弹窗、点头即执行"的落点。
 */
import { useState } from 'react'
import { ShieldCheck, ShieldX } from 'lucide-react'
import { useLayoutStore } from '../../../stores/useLayoutStore'
import { approveRecord, rejectRecord } from '../../../lib/approvalActions'

export function ApprovalPrompt() {
  const pending = useLayoutStore((s) => s.pendingApproval)
  const setPending = useLayoutStore((s) => s.setPendingApproval)
  const sendMessage = useLayoutStore((s) => s.sendMessage)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (!pending) return null

  const decide = async (approve: boolean) => {
    if (busy) return
    if (!approve && !note.trim()) {
      setErr('驳回必须填写原因（审计要求）')
      return
    }
    setBusy(true)
    setErr('')
    try {
      if (approve) {
        await approveRecord(pending.approvalId, note.trim())
      } else {
        await rejectRecord(pending.approvalId, note.trim())
      }
      setPending(null)
      const resume = approve
        ? `已批准审批单 ${pending.approvalId}（工具：${pending.tool}），请继续执行刚才被拦下的操作。`
        : `已拒绝审批单 ${pending.approvalId}（工具：${pending.tool}）：${note.trim()}。请改用不需要该审批的做法继续。`
      setNote('')
      // 自动续跑：把"重发一次"交给界面，用户只需点头
      await sendMessage(resume)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-120 flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-xs"
      data-testid="approval-prompt"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-[520px] max-w-full rounded-2xl border border-amber-600/50 bg-slate-900 p-4 shadow-2xl">
        <div className="mb-2 flex items-center gap-2">
          <ShieldCheck size={16} className="text-amber-400" />
          <h3 className="text-sm font-semibold text-slate-100">需要你确认这次工具调用</h3>
        </div>
        <div className="space-y-1 text-xs text-slate-400">
          <div>工具：<span className="font-mono text-slate-200">{pending.tool}</span></div>
          {pending.approvalId && (
            <div>审批单：<span className="font-mono text-slate-300">{pending.approvalId}</span></div>
          )}
          {pending.message && (
            <div className="rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px] leading-relaxed text-slate-400">
              {pending.message}
            </div>
          )}
        </div>

        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="备注（批准可留空；驳回必填原因）"
          data-testid="approval-note"
          className="mt-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 placeholder-slate-600 outline-hidden focus:border-amber-600"
        />
        {err && <div className="mt-2 text-[11px] text-red-400">{err}</div>}

        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void decide(false)}
            data-testid="approval-reject"
            className="flex items-center gap-1 rounded-lg border border-red-800/70 px-3 py-1.5 text-xs text-red-300 hover:bg-red-950/40 disabled:opacity-40"
          >
            <ShieldX size={12} /> 拒绝
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void decide(true)}
            data-testid="approval-approve"
            className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            <ShieldCheck size={12} /> {busy ? '提交中…' : '批准并继续'}
          </button>
        </div>
      </div>
    </div>
  )
}
