/**
 * DataScopeDialog —— 角色数据范围配置弹窗（受控组件）
 * 数据范围：all 全部 / dept 本部门 / self 仅本人。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
import type { DataScope } from '@/api/menu'
import type { RoleItem } from '@/api/role'

interface DataScopeDialogProps {
  open: boolean
  /** 配置目标角色（非空时打开） */
  role: RoleItem | null
  /** 提交中 */
  saving: boolean
  /** 保存数据范围 */
  onSubmit: (scope: DataScope) => void
  onCancel: () => void
}

const SCOPE_OPTIONS: Array<{ value: DataScope; label: string; desc: string }> = [
  { value: 'all', label: '全部数据', desc: '可访问系统内全部数据' },
  { value: 'dept', label: '本部门数据', desc: '仅可访问本部门及下级部门数据' },
  { value: 'self', label: '仅本人数据', desc: '仅可访问本人创建的数据' },
]

export default function DataScopeDialog({ open, role, saving, onSubmit, onCancel }: DataScopeDialogProps) {
  const [scope, setScope] = useState<DataScope>('self')

  // 打开时同步角色当前数据范围
  useEffect(() => {
    if (!open) return
    setScope(role?.dataScope ?? 'self')
  }, [open, role])

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
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={`数据范围：${role?.label ?? ''}`}
      >
        <h3 className="text-base font-semibold text-slate-800">数据范围</h3>
        <p className="mt-1 text-sm text-slate-500">为角色「{role?.label ?? ''}」配置数据可见范围。</p>

        <div className="mt-4 space-y-2">
          {SCOPE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className="flex cursor-pointer items-start gap-3 rounded-md border border-slate-200 p-3 transition hover:bg-slate-50"
            >
              <input
                type="radio"
                name="data-scope"
                value={opt.value}
                checked={scope === opt.value}
                onChange={() => setScope(opt.value)}
                className="mt-0.5 h-4 w-4 border-slate-300 text-blue-600 focus:ring-blue-500"
              />
              <span>
                <span className="block text-sm font-medium text-slate-700">{opt.label}</span>
                <span className="block text-xs text-slate-400">{opt.desc}</span>
              </span>
            </label>
          ))}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSubmit(scope)}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
