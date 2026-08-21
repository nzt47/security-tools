/**
 * DataScopeDialog —— 角色数据范围配置弹窗（受控组件，基于 ModalBase）
 * 数据范围：all 全部 / dept 本部门 / self 仅本人。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { Button, ModalBase } from '@/components/ui'
import { cn } from '@/lib/cn'
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

  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={`数据范围：${role?.label ?? ''}`}
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" loading={saving} onClick={() => onSubmit(scope)}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">为角色「{role?.label ?? ''}」配置数据可见范围。</p>
      <div className="space-y-2">
        {SCOPE_OPTIONS.map((opt) => (
          <label
            key={opt.value}
            className={cn(
              'flex cursor-pointer items-start gap-3 rounded-md border p-3 transition',
              scope === opt.value
                ? 'border-primary/50 bg-primary/5'
                : 'border-border hover:bg-muted',
            )}
          >
            <input
              type="radio"
              name="data-scope"
              value={opt.value}
              checked={scope === opt.value}
              onChange={() => setScope(opt.value)}
              className="mt-0.5 h-4 w-4 accent-primary"
            />
            <span>
              <span className="block text-sm font-medium text-foreground">{opt.label}</span>
              <span className="block text-xs text-muted-foreground">{opt.desc}</span>
            </span>
          </label>
        ))}
      </div>
    </ModalBase>
  )
}
