/**
 * MenuFormDialog —— 新增/编辑菜单弹窗（受控组件，基于 ModalBase）
 * menu=null 表示新增（可指定 parent 作为子菜单）；menu 非空表示编辑。
 * 提交异步由父组件控制（saving 防重复提交），错误提示由 request.ts 拦截器统一处理。
 */
import { useEffect, useState } from 'react'
import { Button, Input, ModalBase } from '@/components/ui'
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

  return (
    <ModalBase
      open={open}
      onClose={onCancel}
      title={menu ? '编辑菜单' : '新增菜单'}
      footer={
        <>
          <Button variant="default" onClick={onCancel} disabled={saving}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={!title.trim() || !path.trim()}
            onClick={handleSubmit}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
        </>
      }
    >
      <p className="mb-4 text-sm text-muted-foreground">
        {parentTitle ? `作为「${parentTitle}」的子菜单` : menu ? '修改菜单配置。' : '创建顶级菜单。'}
      </p>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          handleSubmit()
        }}
      >
        <Input
          label="菜单名"
          id="mf-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="如 用户列表"
          autoComplete="off"
        />
        <Input
          label="路由路径"
          id="mf-path"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="如 /system/user"
          autoComplete="off"
        />
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="图标名"
            id="mf-icon"
            value={icon}
            onChange={(e) => setIcon(e.target.value)}
            placeholder="如 Users（可选）"
            autoComplete="off"
          />
          <Input
            label="排序"
            id="mf-order"
            type="number"
            value={order}
            onChange={(e) => setOrder(Number(e.target.value) || 0)}
          />
        </div>
        <Input
          label="权限码"
          id="mf-authority"
          value={authority}
          onChange={(e) => setAuthority(e.target.value)}
          placeholder="如 system:user:view（可选）"
          autoComplete="off"
        />
        <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={hideInMenu}
            onChange={(e) => setHideInMenu(e.target.checked)}
            className="h-4 w-4 rounded accent-primary"
          />
          在菜单中隐藏
        </label>
      </form>
    </ModalBase>
  )
}
