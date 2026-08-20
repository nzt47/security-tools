/**
 * RoleFormDialog —— 新增/编辑角色弹窗（受控组件）
 * role=null 表示新增（name 可编辑）；role 非空表示编辑（name 只读）。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
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

  const isEdit = role !== null

  const handleSubmit = () => {
    if (!name.trim() || !label.trim()) return
    onSubmit({ name: name.trim(), label: label.trim(), description: description.trim() })
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? '编辑角色' : '新增角色'}
      >
        <h3 className="text-base font-semibold text-slate-800">{isEdit ? '编辑角色' : '新增角色'}</h3>
        <p className="mt-1 text-sm text-slate-500">
          {isEdit ? '可修改显示名与描述；角色标识不可修改。' : '创建新角色，角色标识唯一。'}
        </p>

        <form
          className="mt-5 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
        >
          <div>
            <label htmlFor="rf-name" className="mb-1.5 block text-sm font-medium text-slate-600">
              角色标识
            </label>
            <input
              id="rf-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isEdit}
              placeholder="如 manager"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
            />
          </div>

          <div>
            <label htmlFor="rf-label" className="mb-1.5 block text-sm font-medium text-slate-600">
              显示名
            </label>
            <input
              id="rf-label"
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="如 经理"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
          </div>

          <div>
            <label htmlFor="rf-desc" className="mb-1.5 block text-sm font-medium text-slate-600">
              描述
            </label>
            <textarea
              id="rf-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="角色职责说明（可选）"
              rows={3}
              className="w-full resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
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
              disabled={saving || !name.trim() || !label.trim()}
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
