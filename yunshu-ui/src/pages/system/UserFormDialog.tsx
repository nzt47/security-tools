/**
 * UserFormDialog —— 新增/编辑用户弹窗（受控组件，基于 ModalBase）
 * user=null 表示新增（用户名可编辑）；user 非空表示编辑（用户名只读）。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { Button, Input, ModalBase, Select } from '@/components/ui'
import type { UserListItem } from '@/api/user'

interface UserFormDialogProps {
  /** 是否显示 */
  open: boolean
  /** 编辑目标（null = 新增） */
  user: UserListItem | null
  /** 提交中（禁用按钮 + loading） */
  saving: boolean
  /** 提交（新增时 username 必填） */
  onSubmit: (values: { username: string; email: string; role: 'admin' | 'manager' | 'user'; status: 0 | 1 }) => void
  onCancel: () => void
}

const ROLES = [
  { value: 'admin', label: '管理员' },
  { value: 'manager', label: '经理' },
  { value: 'user', label: '普通用户' },
]

const STATUS_OPTIONS = [
  { value: '1', label: '启用' },
  { value: '0', label: '禁用' },
]

export default function UserFormDialog({ open, user, saving, onSubmit, onCancel }: UserFormDialogProps) {
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'admin' | 'manager' | 'user'>('user')
  const [status, setStatus] = useState<0 | 1>(1)

  // 打开/切换编辑目标时同步表单
  useEffect(() => {
    if (!open) return
    setUsername(user?.username ?? '')
    setEmail(user?.email ?? '')
    setRole((user?.role as 'admin' | 'manager' | 'user') ?? 'user')
    setStatus(user?.status === 0 ? 0 : 1)
  }, [open, user])

  const isEdit = user !== null

  const handleSubmit = () => {
    if (!isEdit && !username.trim()) return
    onSubmit({ username: username.trim(), email: email.trim(), role, status })
  }

  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={isEdit ? '编辑用户' : '新增用户'}
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={!isEdit && !username.trim()}
            onClick={handleSubmit}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {isEdit ? '可修改邮箱、角色与状态；用户名不可修改。' : '创建新账号，用户名唯一。'}
      </p>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          handleSubmit()
        }}
      >
        <Input
          label="用户名"
          id="uf-username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          disabled={isEdit}
          placeholder="请输入用户名"
          autoComplete="off"
        />
        <Input
          label="邮箱"
          id="uf-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="请输入邮箱"
          autoComplete="off"
        />
        <div className="grid grid-cols-2 gap-4">
          <Select
            label="角色"
            id="uf-role"
            options={ROLES}
            value={role}
            onChange={(v) => setRole(v as 'admin' | 'manager' | 'user')}
          />
          <Select
            label="状态"
            id="uf-status"
            options={STATUS_OPTIONS}
            value={String(status)}
            onChange={(v) => setStatus(v === '0' ? 0 : 1)}
          />
        </div>
      </form>
    </ModalBase>
  )
}
