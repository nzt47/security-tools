/**
 * RoleList —— 系统管理 / 角色列表（RBAC 角色管理页）
 * 功能：关键字搜索 / 分页查询 / 新增·编辑·删除 / 权限分配（勾选全量覆盖） / 数据范围
 * 数据源：@/api/role（request.ts 已解包，直接返回业务数据）
 * 结构：useTablePage + PageContainer + Table + Pagination + 弹窗
 */
import { useEffect, useState } from 'react'
import { Database, KeyRound, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import {
  assignRolePermissions,
  createRole,
  deleteRole,
  getRoleList,
  updateRole,
  type RoleItem,
} from '@/api/role'
import { updateRoleDataScope, type DataScope } from '@/api/menu'
import { toast } from '@/components/Toaster'
import ConfirmDialog from '@/components/ConfirmDialog'
import { Button, Card, Input, PageContainer, Pagination, Table, type TableColumn } from '@/components/ui'
import { useTablePage } from '@/hooks/useTablePage'
import RoleFormDialog from './RoleFormDialog'
import PermissionAssignDialog from './PermissionAssignDialog'
import DataScopeDialog from './DataScopeDialog'

const PAGE_SIZE = 10

interface RoleQuery {
  page: number
  pageSize: number
  keyword: string
}

const columns: TableColumn<RoleItem>[] = [
  { key: 'id', header: 'ID', render: (r) => <span className="text-muted-foreground">{r.id}</span> },
  { key: 'name', header: '角色标识', render: (r) => <span className="font-medium text-foreground">{r.name}</span> },
  {
    key: 'label',
    header: '显示名',
    render: (r) => (
      <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
        {r.label}
      </span>
    ),
  },
  {
    key: 'description',
    header: '描述',
    render: (r) => <span className="block max-w-[200px] truncate text-muted-foreground">{r.description}</span>,
  },
  { key: 'permissions', header: '权限数', render: (r) => <span className="text-muted-foreground">{r.permissions.length}</span> },
  { key: 'createdAt', header: '创建时间', render: (r) => <span className="text-muted-foreground">{r.createdAt}</span> },
]

export default function RoleList() {
  const [keywordInput, setKeywordInput] = useState('')
  const [pageInput, setPageInput] = useState('1')

  const { query, setQuery, list, total, loading, fetchList, handleSearch, handleReset, goPage } =
    useTablePage<RoleItem, RoleQuery>({
      fetcher: getRoleList,
      defaultQuery: { page: 1, pageSize: PAGE_SIZE, keyword: '' },
    })

  // 删除确认
  const [deleteTarget, setDeleteTarget] = useState<RoleItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 新增/编辑弹窗
  const [formOpen, setFormOpen] = useState(false)
  const [formRole, setFormRole] = useState<RoleItem | null>(null)
  const [saving, setSaving] = useState(false)

  // 权限分配弹窗
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignRole, setAssignRole] = useState<RoleItem | null>(null)
  const [assigning, setAssigning] = useState(false)

  // 数据范围弹窗（M3）
  const [scopeOpen, setScopeOpen] = useState(false)
  const [scopeRole, setScopeRole] = useState<RoleItem | null>(null)
  const [scopeSaving, setScopeSaving] = useState(false)

  useEffect(() => {
    setPageInput(String(query.page))
  }, [query.page])

  /** 删除确认：内置 admin 由后端拒绝（mock 返回业务错误），成功则刷新；末页删空回退一页 */
  const handleConfirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteRole(deleteTarget.id)
      setQuery((q) => {
        const nextPage = list.length === 1 && q.page > 1 ? q.page - 1 : q.page
        return { ...q, page: nextPage }
      })
      setDeleteTarget(null)
    } catch {
      // 失败提示已由 request.ts 处理，保留弹窗供重试
    } finally {
      setDeleting(false)
    }
  }

  /** 新增/编辑弹窗提交 */
  const handleFormSubmit = async (values: { name: string; label: string; description: string }) => {
    setSaving(true)
    try {
      if (formRole) {
        await updateRole(formRole.id, values)
      } else {
        await createRole(values)
      }
      toast.success(formRole ? '角色已更新' : '角色已创建')
      setFormOpen(false)
      await fetchList()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setSaving(false)
    }
  }

  /** 权限分配提交：全量覆盖 */
  const handleAssignSubmit = async (permissions: string[]) => {
    if (!assignRole) return
    setAssigning(true)
    try {
      await assignRolePermissions(assignRole.id, permissions)
      toast.success('权限已更新')
      setAssignOpen(false)
      await fetchList()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setAssigning(false)
    }
  }

  /** 数据范围提交（M3） */
  const handleScopeSubmit = async (scope: DataScope) => {
    if (!scopeRole) return
    setScopeSaving(true)
    try {
      await updateRoleDataScope(scopeRole.id, scope)
      toast.success('数据范围已更新')
      setScopeOpen(false)
      await fetchList()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setScopeSaving(false)
    }
  }

  // 操作列（依赖页面内状态，在 render 内构造）
  const actionColumn: TableColumn<RoleItem> = {
    key: 'actions',
    header: '操作',
    render: (r) => (
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          className="text-primary"
          onClick={() => {
            setFormRole(r)
            setFormOpen(true)
          }}
        >
          <Pencil className="h-3.5 w-3.5" />
          编辑
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setAssignRole(r)
            setAssignOpen(true)
          }}
        >
          <KeyRound className="h-3.5 w-3.5" />
          权限
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          onClick={() => {
            setScopeRole(r)
            setScopeOpen(true)
          }}
        >
          <Database className="h-3.5 w-3.5" />
          数据
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-danger"
          onClick={() => setDeleteTarget(r)}
        >
          <Trash2 className="h-3.5 w-3.5" />
          删除
        </Button>
      </div>
    ),
  }

  return (
    <PageContainer
      title="角色管理"
      description="管理角色、权限分配与数据范围"
      actions={
        <Button
          variant="primary"
          onClick={() => {
            setFormRole(null)
            setFormOpen(true)
          }}
        >
          <Plus className="h-4 w-4" />
          新增角色
        </Button>
      }
    >
      {/* 顶部搜索区 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch({ keyword: keywordInput.trim() })
          }}
          placeholder="请输入角色名或显示名搜索"
          className="w-64"
        />
        <Button variant="primary" onClick={() => handleSearch({ keyword: keywordInput.trim() })}>
          <Search className="h-4 w-4" />
          查询
        </Button>
        <Button
          variant="default"
          onClick={() => {
            setKeywordInput('')
            handleReset()
          }}
        >
          <RefreshCw className="h-4 w-4" />
          重置
        </Button>
      </div>

      {/* 表格区 */}
      <Card>
        <Table
          columns={[...columns, actionColumn]}
          dataSource={list}
          loading={loading}
          rowKey={(r) => r.id}
        />
      </Card>

      {/* 底部分页 + 跳转 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Pagination
          page={query.page}
          pageSize={query.pageSize}
          total={total}
          onChange={(p) => setQuery((q) => ({ ...q, page: p }))}
        />
        <div className="flex items-center gap-1">
          <span className="text-sm text-muted-foreground">跳至</span>
          <Input
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ''))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') goPage(Number(pageInput))
            }}
            className="w-16 text-center"
            aria-label="跳转页码"
          />
          <span className="text-sm text-muted-foreground">页</span>
        </div>
      </div>

      {/* 删除确认弹窗 */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除角色"
        message={`确定删除角色「${deleteTarget?.label ?? ''}」吗？有用户引用的角色将删除失败。`}
        confirmText="删除"
        danger
        loading={deleting}
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 新增/编辑角色弹窗 */}
      <RoleFormDialog
        open={formOpen}
        role={formRole}
        saving={saving}
        onSubmit={handleFormSubmit}
        onCancel={() => setFormOpen(false)}
      />

      {/* 权限分配弹窗 */}
      <PermissionAssignDialog
        open={assignOpen}
        role={assignRole}
        saving={assigning}
        onSubmit={handleAssignSubmit}
        onCancel={() => setAssignOpen(false)}
      />

      {/* 数据范围弹窗（M3） */}
      <DataScopeDialog
        open={scopeOpen}
        role={scopeRole}
        saving={scopeSaving}
        onSubmit={handleScopeSubmit}
        onCancel={() => setScopeOpen(false)}
      />
    </PageContainer>
  )
}
