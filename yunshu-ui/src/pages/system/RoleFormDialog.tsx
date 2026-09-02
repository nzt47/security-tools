/**
 * RoleFormDialog —— 新增/编辑角色弹窗（受控组件，基于 ModalBase）
 * role=null 表示新增（name 可编辑）；role 非空表示编辑（name 只读）。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { Button, Input, ModalBase } from '@/components/ui'
import type { RoleItem } from '@/api/role'

interface RoleFormDialogProps {
  open: boolean
  /** 编辑目标（null = 新增） */
  role: RoleItem | null
  /** 提交中（禁用按钮 + loading） */
  saving: boolean
  onSubmit: (values: { name: string; label: string; description: string }) => void
  onCancel: () => void
}

export default function RoleFormDialog({ open, role, saving, onSubmit, onCancel }: RoleFormDialogProps) {
  const [name, setName] = useState('')
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')

  // 打开/切换编辑目标时同步表单
  useEffect(() => {
    if (!open) return
    setName(role?.name ?? '')
    setLabel(role?.label ?? '')
    setDescription(role?.description ?? '')
  }, [open, role])

  const isEdit = role !== null

  const handleSubmit = () => {
    if (!name.trim() || !label.trim()) return
    onSubmit({ name: name.trim(), label: label.trim(), description: description.trim() })
  }

  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={isEdit ? '编辑角色' : '新增角色'}
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={!name.trim() || !label.trim()}
            onClick={handleSubmit}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {isEdit ? '可修改显示名与描述；角色标识不可修改。' : '创建新角色，角色标识唯一。'}
      </p>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          handleSubmit()
        }}
      >
        <Input
          label="角色标识"
          id="rf-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isEdit}
          placeholder="如 manager"
          autoComplete="off"
        />
        <Input
          label="显示名"
          id="rf-label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="如 经理"
          autoComplete="off"
        />
        <div>
          <label htmlFor="rf-desc" className="mb-1.5 block text-sm font-medium text-foreground">
            描述
          </label>
          <textarea
            id="rf-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="角色职责说明（可选）"
            rows={3}
            className="w-full resize-none rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
      </form>
    </ModalBase>
  )
}
