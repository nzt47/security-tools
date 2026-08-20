/**
 * MenuFormDialog —— 新增/编辑菜单弹窗（受控组件）
 * menu=null 表示新增（可指定 parent 作为子菜单）；menu 非空表示编辑。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
import type { MenuItem } from '@/api/menu'

interface MenuFormDialogProps {
  open: boolean
  /** 编辑目标（null = 新增） */
  menu: MenuItem | null
  /** 父菜单 id（新增子菜单时传入，编辑时忽略） */
  parentId?: number
  /** 父菜单标题（新增子菜单时展示，如「系统管理」；编辑时隐藏） */
  parentTitle?: string
  /** 提交中 */
  saving: boolean
  onSubmit: (values: {
    parentId: number
    title: string
    path: string
    icon: string
    authority: string
    order: number
    hideInMenu: boolean
  }) => void
  onCancel: () => void
}

export default function MenuFormDialog({
  open,
  menu,
  parentId,
  parentTitle,
  saving,
  onSubmit,
  onCancel,
}: MenuFormDialogProps) {
  const [title, setTitle] = useState('')
  const [path, setPath] = useState('')
  const [icon, setIcon] = useState('')
  const [authority, setAuthority] = useState('')
  const [order, setOrder] = useState(0)
  const [hideInMenu, setHideInMenu] = useState(false)

  // 打开/切换编辑目标时同步表单
  useEffect(() => {
    if (!open) return
    setTitle(menu?.title ?? '')
    setPath(menu?.path ?? '')
    setIcon(menu?.icon ?? '')
    setAuthority(menu?.authority ?? '')
    setOrder(menu?.order ?? 0)
    setHideInMenu(menu?.hideInMenu ?? false)
  }, [open, menu])

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

  const handleSubmit = () => {
    if (!title.trim() || !path.trim()) return
    onSubmit({
      // 【Why】编辑沿用原 parentId；新增时取传入的父菜单 id（无则顶级 0）
      parentId: menu?.parentId ?? parentId ?? 0,
      title: title.trim(),
      path: path.trim(),
      icon: icon.trim(),
      authority: authority.trim(),
      order,
      hideInMenu,
    })
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={onCancel} aria-hidden />
      <div
        className="relative w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={menu ? '编辑菜单' : '新增菜单'}
      >
        <h3 className="text-base font-semibold text-slate-800">{menu ? '编辑菜单' : '新增菜单'}</h3>
        <p className="mt-1 text-sm text-slate-500">
          {parentTitle ? `作为「${parentTitle}」的子菜单` : menu ? '修改菜单配置。' : '创建顶级菜单。'}
        </p>

        <form
          className="mt-5 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
        >
          <div>
            <label htmlFor="mf-title" className="mb-1.5 block text-sm font-medium text-slate-600">
              菜单名
            </label>
            <input
              id="mf-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="如 用户列表"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
          </div>

          <div>
            <label htmlFor="mf-path" className="mb-1.5 block text-sm font-medium text-slate-600">
              路由路径
            </label>
            <input
              id="mf-path"
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="如 /system/user"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="mf-icon" className="mb-1.5 block text-sm font-medium text-slate-600">
                图标名
              </label>
              <input
                id="mf-icon"
                type="text"
                value={icon}
                onChange={(e) => setIcon(e.target.value)}
                placeholder="如 Users（可选）"
                autoComplete="off"
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
              />
            </div>
            <div>
              <label htmlFor="mf-order" className="mb-1.5 block text-sm font-medium text-slate-600">
                排序
              </label>
              <input
                id="mf-order"
                type="number"
                value={order}
                onChange={(e) => setOrder(Number(e.target.value) || 0)}
                className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500"
              />
            </div>
          </div>

          <div>
            <label htmlFor="mf-authority" className="mb-1.5 block text-sm font-medium text-slate-600">
              权限码
            </label>
            <input
              id="mf-authority"
              type="text"
              value={authority}
              onChange={(e) => setAuthority(e.target.value)}
              placeholder="如 system:user:view（可选）"
              autoComplete="off"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 outline-none transition focus:border-blue-500"
            />
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={hideInMenu}
              onChange={(e) => setHideInMenu(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            在菜单中隐藏
          </label>

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
              disabled={saving || !title.trim() || !path.trim()}
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
