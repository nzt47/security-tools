/**
 * PermissionAssignDialog —— 角色权限分配弹窗（受控组件，基于 ModalBase）
 * 打开时加载权限码列表（getPermissionList），按分组渲染 checkbox，全量勾选保存。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { Button, Loading, ModalBase } from '@/components/ui'
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

  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={`分配权限：${role?.label ?? ''}`}
      width="max-w-lg"
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={loading}
            onClick={() => onSubmit(Array.from(checked))}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        为角色「{role?.label ?? ''}」配置权限码，保存后全量覆盖。
      </p>
      <div className="max-h-80 overflow-y-auto rounded-md border border-border p-4">
        {loading ? (
          <Loading />
        ) : (
          groups.map(({ group, items }) => (
            <div key={group} className="mb-4 last:mb-0">
              <p className="mb-2 text-sm font-semibold text-foreground">{group}</p>
              <div className="space-y-1.5 pl-1">
                {items.map((p) => (
                  <label
                    key={p.code}
                    className="flex cursor-pointer items-center gap-2 text-sm text-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={checked.has(p.code)}
                      onChange={() => toggle(p.code)}
                      className="h-4 w-4 rounded accent-primary"
                    />
                    {p.label}
                    <span className="ml-auto font-mono text-xs text-muted-foreground">{p.code}</span>
                  </label>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </ModalBase>
  )
}
