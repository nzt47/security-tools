/**
 * MenuList —— 系统管理 / 菜单管理（菜单树 CRUD）
 * 功能：菜单树展示（缩进层级）/ 新增 / 新增子菜单 / 编辑 / 删除（有子菜单时后端拒绝）
 * 数据源：@/api/menu（request.ts 已解包，直接返回业务数据）
 * 结构：树形页（无分页）→ 自建递归渲染 + Card/Empty/Loading + PageContainer + 弹窗
 */
import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Pencil, Plus, Trash2 } from 'lucide-react'
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
import { Button, Card, Empty, Loading, PageContainer } from '@/components/ui'
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
    <PageContainer
      title="菜单管理"
      description="配置侧边栏菜单树（标题 / 路径 / 图标 / 权限码），数据驱动菜单渲染。"
      actions={
        <Button variant="primary" onClick={() => openCreate()}>
          <Plus className="h-4 w-4" />
          新增菜单
        </Button>
      }
    >
      <Card>
        {loading ? (
          <Loading />
        ) : tree.length === 0 ? (
          <Empty />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  {['菜单名', '路由路径', '图标', '权限码', '排序', '隐藏', '操作'].map((head) => (
                    <th key={head} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>{renderRows(tree)}</tbody>
            </table>
          </div>
        )}
      </Card>

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
    </PageContainer>
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
      <tr className="border-b border-border/60 last:border-0 transition hover:bg-muted/30">
        <td className="px-4 py-3">
          <div className="flex items-center gap-1" style={{ paddingLeft: depth * 20 }}>
            {hasChildren ? (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
            )}
            <span className="font-medium text-foreground">{item.title}</span>
            {item.hideInMenu && (
              <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">隐藏</span>
            )}
          </div>
        </td>
        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{item.path}</td>
        <td className="px-4 py-3 text-muted-foreground">{item.icon || '-'}</td>
        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{item.authority || '-'}</td>
        <td className="px-4 py-3 text-muted-foreground">{item.order}</td>
        <td className="px-4 py-3 text-muted-foreground">{item.hideInMenu ? '是' : '否'}</td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" className="text-primary" onClick={() => onEdit(item)}>
              <Pencil className="h-3.5 w-3.5" />
              编辑
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onCreate(item)}>
              <Plus className="h-3.5 w-3.5" />
              子菜单
            </Button>
            <Button variant="ghost" size="sm" className="text-danger" onClick={() => onDelete(item)}>
              <Trash2 className="h-3.5 w-3.5" />
              删除
            </Button>
          </div>
        </td>
      </tr>
      {children}
    </>
  )
}
