/**
 * PermissionAssignDialog —— 角色权限分配弹窗（受控组件）
 * 打开时加载权限码列表（getPermissionList），按分组渲染 checkbox，全量勾选保存。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
import { getPermissionList, type PermissionItem, type RoleItem } from '@/api/role'

interface PermissionAssignDialogProps {
  open: boolean
  /** 分配目标角色（非空时打开） */
  role: RoleItem | null
  /** 提交中（禁用按钮 + loading） */
  saving: boolean
  /** 保存（全量权限码集合） */
  onSubmit: (permissions: string[]) => void
  onCancel: () => void
}

export default function PermissionAssignDialog({
  open,
  role,
  saving,
  onSubmit,
  onCancel,
}: PermissionAssignDialogProps) {
  const [permissions, setPermissions] = useState<PermissionItem[]>([])
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)

  // 打开时加载权限码列表并同步角色已分配权限
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    getPermissionList()
      .then((list) => {
        if (cancelled) return
        setPermissions(list)
        setChecked(new Set(role?.permissions ?? []))
      })
      .catch(() => {
        // 错误提示已由 request.ts 统一处理
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
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

  /** 按 group 分组（保持权限码列表原始顺序） */
  const groups = Array.from(new Set(permissions.map((p) => p.group))).map((group) => ({
    group,
    items: permissions.filter((p) => p.group === group),
  }))

  const toggle = (code: string) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-lg rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={`分配权限：${role?.label ?? ''}`}
      >
        <h3 className="text-base font-semibold text-slate-800">分配权限</h3>
        <p className="mt-1 text-sm text-slate-500">为角色「{role?.label ?? ''}」配置权限码，保存后全量覆盖。</p>

        <div className="mt-4 max-h-80 overflow-y-auto rounded-md border border-slate-200 p-4">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
            </div>
          ) : (
            groups.map(({ group, items }) => (
              <div key={group} className="mb-4 last:mb-0">
                <p className="mb-2 text-sm font-semibold text-slate-700">{group}</p>
                <div className="space-y-1.5 pl-1">
                  {items.map((p) => (
                    <label key={p.code} className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
                      <input
                        type="checkbox"
                        checked={checked.has(p.code)}
                        onChange={() => toggle(p.code)}
                        className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                      />
                      {p.label}
                      <span className="ml-auto font-mono text-xs text-slate-400">{p.code}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))
          )}
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
            onClick={() => onSubmit(Array.from(checked))}
            disabled={saving || loading}
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
