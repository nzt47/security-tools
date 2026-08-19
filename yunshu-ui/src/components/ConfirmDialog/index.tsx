/**
 * ConfirmDialog —— 通用确认弹窗（受控组件）
 * 替代原生 window.confirm：支持遮罩/Esc 关闭、危险操作红色按钮、loading 防重复提交。
 * 使用方式：父组件持有 open/loading 状态，确认回调触发异步操作。
 */
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, TriangleAlert } from 'lucide-react'

interface ConfirmDialogProps {
  /** 是否显示弹窗 */
  open: boolean
  /** 标题 */
  title: string
  /** 提示内容 */
  message: string
  /** 确认按钮文案，默认「确认」 */
  confirmText?: string
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string
  /** 确认操作进行中：禁用按钮并显示加载态，防重复提交 */
  loading?: boolean
  /** 危险操作（如删除）：确认按钮显示为红色 */
  danger?: boolean
  /** 点击确认 */
  onConfirm: () => void
  /** 点击取消 / 遮罩 / Esc */
  onCancel: () => void
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  loading = false,
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKeydown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKeydown)
    return () => window.removeEventListener('keydown', onKeydown)
  }, [open, onCancel])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* 遮罩：点击关闭 */}
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-sm rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="flex items-start gap-3">
          <div
            className={
              danger
                ? 'flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-100 text-red-600'
                : 'flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600'
            }
          >
            <TriangleAlert className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-slate-800">{title}</h3>
            <p className="mt-1 text-sm text-slate-500">{message}</p>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cancelText}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={
              danger
                ? 'inline-flex items-center gap-1.5 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60'
                : 'inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60'
            }
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
