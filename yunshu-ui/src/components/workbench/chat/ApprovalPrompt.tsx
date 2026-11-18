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
import { Button, Input, ModalBase, Notice } from '@/components/ui'
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
    // 【2026-11-18 封装收口】外壳交给 ui/ModalBase —— 但它是**阻塞式确认**：
    // 不允许点暗底 / 按 Esc "走开"（否则会留下一条没人裁决的挂单），所以 closeOnMask/closeOnEsc 都关掉。
    <ModalBase
      open
      onClose={() => {}}
      closeOnMask={false}
      closeOnEsc={false}
      testId="approval-prompt"
      className="w-[520px] max-w-full border-warning/50"
    >
        <div className="mb-2 flex items-center gap-2">
          <ShieldCheck size={16} className="text-warning" />
          <h3 className="text-sm font-semibold text-foreground">需要你确认这次工具调用</h3>
        </div>
        <div className="space-y-1 text-xs text-slate-400">
          <div>工具：<span className="font-mono text-slate-200">{pending.tool}</span></div>
          {pending.approvalId && (
            <div>审批单：<span className="font-mono text-slate-300">{pending.approvalId}</span></div>
          )}
          {pending.message && (
            <div className="rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-caption leading-relaxed text-slate-400">
              {pending.message}
            </div>
          )}
        </div>

        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（批准可留空；驳回必填原因）" data-testid="approval-note" size="xs" className="mt-3" />
        {err && <Notice tone="danger" size="sm" className="mt-2">{err}</Notice>}

        <div className="mt-3 flex items-center justify-end gap-2">
          <Button type="button" disabled={busy} onClick={() => void decide(false)} data-testid="approval-reject" variant="danger-outline" size="xs">
            <ShieldX size={12} /> 拒绝
          </Button>
          <Button type="button" disabled={busy} onClick={() => void decide(true)} data-testid="approval-approve" variant="primary" size="xs">
            <ShieldCheck size={12} /> {busy ? '提交中…' : '批准并继续'}
          </Button>
        </div>
    </ModalBase>
  )
}