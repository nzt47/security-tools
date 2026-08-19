/**
 * UserFormDialog —— 新增/编辑用户弹窗（受控组件）
 * user=null 表示新增（用户名可编辑）；user 非空表示编辑（用户名只读）。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
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

const ROLES: Array<{ value: 'admin' | 'manager' | 'user'; label: string }> = [
  { value: 'admin', label: '管理员' },
  { value: 'manager', label: '经理' },
  { value: 'user', label: '普通用户' },
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

  const isEdit = user !== null

  const handleSubmit = () => {
    if (!isEdit && !username.trim()) return
    onSubmit({ username: username.trim(), email: email.trim(), role, status })
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? '编辑用户' : '新增用户'}
      >
        <h3 className="text-base font-semibold text-slate-800">{isEdit ? '编辑用户' : '新增用户'}</h3>
        <p className="mt-1 text-sm text-slate-500">
          {isEdit ? '可修改邮箱、角色与状态；用户名不可修改。' : '创建新账号，用户名唯一。'}
        </p>

        <form
          className="mt-5 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
        >
          <div>
            <label htmlFor="uf-username" className="mb-1.5 block text-sm font-medium text-slate-600">
              用户名
            </label>
            <input
              id="uf-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={isEdit}
              placeholder="请输入用户名"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
            />
          </div>

          <div>
            <label htmlFor="uf-email" className="mb-1.5 block text-sm font-medium text-slate-600">
              邮箱
            </label>
            <input
              id="uf-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="请输入邮箱"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="uf-role" className="mb-1.5 block text-sm font-medium text-slate-600">
                角色
              </label>
              <select
                id="uf-role"
                value={role}
                onChange={(e) => setRole(e.target.value as 'admin' | 'manager' | 'user')}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500"
              >
                {ROLES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="uf-status" className="mb-1.5 block text-sm font-medium text-slate-600">
                状态
              </label>
              <select
                id="uf-status"
                value={status}
                onChange={(e) => setStatus(e.target.value === '0' ? 0 : 1)}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500"
              >
                <option value={1}>启用</option>
                <option value={0}>禁用</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={saving}
              className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={saving || (!isEdit && !username.trim())}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
