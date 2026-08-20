/**
 * MenuList —— 系统管理 / 菜单管理（菜单树 CRUD）
 * 功能：菜单树展示（缩进层级）/ 新增 / 新增子菜单 / 编辑 / 删除（有子菜单时后端拒绝）
 * 数据源：@/api/menu（request.ts 已解包，直接返回业务数据）
 */
import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import {
  createMenu,
  deleteMenu,
  getMenuTree,
  updateMenu,
  type MenuFormParams,
  type MenuItem,
} from '@/api/menu'
import { toast } from '@/components/Toaster'
import ConfirmDialog from '@/components/ConfirmDialog'
import MenuFormDialog from './MenuFormDialog'

export default function MenuList() {
  const [tree, setTree] = useState<MenuItem[]>([])
  const [loading, setLoading] = useState(false)

  // 新增/编辑弹窗：formMenu=null 表示新增；formParent 记录父菜单（新增子菜单时）
  const [formOpen, setFormOpen] = useState(false)
  const [formMenu, setFormMenu] = useState<MenuItem | null>(null)
  const [formParent, setFormParent] = useState<MenuItem | null>(null)
  const [saving, setSaving] = useState(false)

  // 删除确认
  const [deleteTarget, setDeleteTarget] = useState<MenuItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  /** 拉取菜单树 */
  const fetchTree = useCallback(async () => {
    setLoading(true)
    try {
      setTree(await getMenuTree())
    } catch {
      // 错误提示已由 request.ts 统一处理
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchTree()
  }, [fetchTree])

  /** 打开新增弹窗（可指定父菜单） */
  const openCreate = (parent?: MenuItem) => {
    setFormMenu(null)
    setFormParent(parent ?? null)
    setFormOpen(true)
  }

  /** 打开编辑弹窗 */
  const openEdit = (item: MenuItem) => {
    setFormMenu(item)
    setFormParent(null)
    setFormOpen(true)
  }

  /** 提交新增/编辑（parentId 已由弹窗按「编辑/新增子菜单/顶级」计算） */
  const handleFormSubmit = async (values: MenuFormParams) => {
    setSaving(true)
    try {
      if (formMenu) {
        await updateMenu(formMenu.id, values)
      } else {
        await createMenu(values)
      }
      toast.success(formMenu ? '菜单已更新' : '菜单已创建')
      setFormOpen(false)
      await fetchTree()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setSaving(false)
    }
  }

  /** 删除确认 */
  const handleConfirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteMenu(deleteTarget.id)
      toast.success('菜单已删除')
      setDeleteTarget(null)
      await fetchTree()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setDeleting(false)
    }
  }

  /** 递归渲染菜单行（缩进展示层级） */
  const renderRows = (items: MenuItem[], depth = 0): React.ReactNode =>
    items.map((item) => (
      <FragmentRow
        key={item.id}
        item={item}
        depth={depth}
        onEdit={openEdit}
        onCreate={openCreate}
        onDelete={setDeleteTarget}
      >
        {renderRows(item.children ?? [], depth + 1)}
      </FragmentRow>
    ))

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-4 text-2xl font-semibold text-slate-800">菜单管理</h1>

      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate-500">配置侧边栏菜单树（标题 / 路径 / 图标 / 权限码），数据驱动菜单渲染。</p>
        <button
          type="button"
          onClick={() => openCreate()}
          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500"
        >
          <Plus className="h-4 w-4" />
          新增菜单
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              {['菜单名', '路由路径', '图标', '权限码', '排序', '隐藏', '操作'].map((head) => (
                <th key={head} className="px-4 py-3 text-left font-medium text-slate-500">
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center">
                  <Loader2 className="mx-auto h-6 w-6 animate-spin text-blue-500" />
                </td>
              </tr>
            ) : tree.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-slate-400">
                  暂无数据
                </td>
              </tr>
            ) : (
              renderRows(tree)
            )}
          </tbody>
        </table>
      </div>

      {/* 删除确认弹窗 */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除菜单"
        message={`确定删除菜单「${deleteTarget?.title ?? ''}」吗？存在子菜单时删除失败。`}
        confirmText="删除"
        danger
        loading={deleting}
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 新增/编辑菜单弹窗 */}
      <MenuFormDialog
        open={formOpen}
        menu={formMenu}
        parentId={formParent?.id}
        parentTitle={formParent?.title}
        saving={saving}
        onSubmit={handleFormSubmit}
        onCancel={() => setFormOpen(false)}
      />
    </div>
  )
}

/** 单行渲染（带子行），用于菜单树缩进展示 */
function FragmentRow({
  item,
  depth,
  children,
  onEdit,
  onCreate,
  onDelete,
}: {
  item: MenuItem
  depth: number
  children: React.ReactNode
  onEdit: (item: MenuItem) => void
  onCreate: (item?: MenuItem) => void
  onDelete: (item: MenuItem) => void
}) {
  const hasChildren = (item.children?.length ?? 0) > 0
  return (
    <>
      <tr className="transition hover:bg-slate-50">
        <td className="px-4 py-3">
          <div className="flex items-center gap-1" style={{ paddingLeft: depth * 20 }}>
            {hasChildren ? <ChevronDown className="h-3.5 w-3.5 text-slate-400" /> : <ChevronRight className="h-3.5 w-3.5 text-slate-300" />}
            <span className="font-medium text-slate-700">{item.title}</span>
            {item.hideInMenu && (
              <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">隐藏</span>
            )}
          </div>
        </td>
        <td className="px-4 py-3 font-mono text-xs text-slate-500">{item.path}</td>
        <td className="px-4 py-3 text-slate-500">{item.icon || '-'}</td>
        <td className="px-4 py-3 font-mono text-xs text-slate-500">{item.authority || '-'}</td>
        <td className="px-4 py-3 text-slate-500">{item.order}</td>
        <td className="px-4 py-3 text-slate-500">{item.hideInMenu ? '是' : '否'}</td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onEdit(item)}
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-blue-600 transition hover:bg-blue-50"
            >
              <Pencil className="h-3.5 w-3.5" />
              编辑
            </button>
            <button
              type="button"
              onClick={() => onCreate(item)}
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-violet-600 transition hover:bg-violet-50"
            >
              <Plus className="h-3.5 w-3.5" />
              子菜单
            </button>
            <button
              type="button"
              onClick={() => onDelete(item)}
              className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-red-600 transition hover:bg-red-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              删除
            </button>
          </div>
        </td>
      </tr>
      {children}
    </>
  )
}
