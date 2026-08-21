/**
 * ConfirmDialog —— 通用确认弹窗（受控组件）
 * 替代原生 window.confirm：支持遮罩/Esc 关闭、危险操作红色按钮、loading 防重复提交。
 * 使用方式：父组件持有 open/loading 状态，确认回调触发异步操作。
 *
 * 外壳统一走 ModalBase（遮罩/Esc/滚动锁定/标题/footer），颜色一律语义 Token。
 */
import { TriangleAlert } from 'lucide-react'
import ModalBase from '@/components/ui/ModalBase'
import { Button } from '@/components/ui'

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
  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={loading}>
            {confirmText}
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <div
          className={
            danger
              ? 'flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-danger/10 text-danger'
              : 'flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary'
          }
        >
          <TriangleAlert className="h-5 w-5" />
        </div>
        <p className="min-w-0 text-sm text-muted-foreground">{message}</p>
      </div>
    </ModalBase>
  )
}
