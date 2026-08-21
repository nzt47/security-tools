/**
 * UserList —— 系统管理 / 用户列表（CRUD 表格页，useTablePage 样板）
 * 功能：关键字搜索 / 分页查询 / Loading 态 / 删除确认（ConfirmDialog）
 * 数据源：@/api/user（request.ts 已解包，直接返回业务数据）
 * 结构：useTablePage（query/list/loading）+ PageContainer + Table + Pagination + 弹窗
 */
import { useEffect, useState } from 'react'
import { Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import { createUser, deleteUser, getUserList, updateUser, type UserListItem } from '@/api/user'
import { toast } from '@/components/Toaster'
import ConfirmDialog from '@/components/ConfirmDialog'
import { Button, Card, Input, PageContainer, Pagination, Table, type TableColumn } from '@/components/ui'
import { useTablePage } from '@/hooks/useTablePage'
import UserFormDialog from './UserFormDialog'

const PAGE_SIZE = 10

interface UserQuery {
  page: number
  pageSize: number
  keyword: string
}

const columns: TableColumn<UserListItem>[] = [
  {
    key: 'id',
    header: 'ID',
    render: (r) => <span className="text-muted-foreground">{r.id}</span>,
  },
  {
    key: 'username',
    header: '用户名',
    render: (r) => <span className="font-medium text-foreground">{r.username}</span>,
  },
  { key: 'email', header: '邮箱' },
  {
    key: 'role',
    header: '角色',
    render: (r) => (
      <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
        {r.role}
      </span>
    ),
  },
  {
    key: 'status',
    header: '状态',
    render: (r) =>
      r.status === 1 ? (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-0.5 text-xs font-medium text-success">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          启用
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50" />
          禁用
        </span>
      ),
  },
  { key: 'createdAt', header: '创建时间' },
  {
    key: 'actions',
    header: '操作',
    render: () => null, // 操作列依赖页面内状态，由页面在 Table 外层用 actionRender 提供
  },
]

export default function UserList() {
  // 搜索输入框（受控，点击「查询」后才写入 query）与跳转页码输入框（受控）
  const [keywordInput, setKeywordInput] = useState('')
  const [pageInput, setPageInput] = useState('1')

  const { query, setQuery, list, total, loading, totalPages, fetchList, handleSearch, handleReset, goPage } =
    useTablePage<UserListItem, UserQuery>({
      fetcher: getUserList,
      defaultQuery: { page: 1, pageSize: PAGE_SIZE, keyword: '' },
    })

  // 删除目标（非空时打开确认弹窗）与确认请求中的防重提交标记
  const [deleteTarget, setDeleteTarget] = useState<UserListItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 新增/编辑弹窗状态：formUser=null 表示新增，非空表示编辑目标；saving 防重复提交
  const [formOpen, setFormOpen] = useState(false)
  const [formUser, setFormUser] = useState<UserListItem | null>(null)
  const [saving, setSaving] = useState(false)

  // 页码变化时同步跳转输入框
  useEffect(() => {
    setPageInput(String(query.page))
  }, [query.page])

  /** 删除确认：调用接口成功后刷新；若当前页被删空且非首页则回退一页 */
  const handleConfirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteUser(deleteTarget.id)
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

  /** 新增/编辑弹窗提交：编辑走 PUT（用户名只读），新增走 POST */
  const handleFormSubmit = async (values: {
    username: string
    email: string
    role: 'admin' | 'manager' | 'user'
    status: 0 | 1
  }) => {
    setSaving(true)
    try {
      if (formUser) {
        await updateUser(formUser.id, values)
      } else {
        await createUser(values)
      }
      toast.success(formUser ? '用户已更新' : '用户已创建')
      setFormOpen(false)
      await fetchList()
    } catch {
      // 失败提示已由 request.ts 统一处理，保留弹窗供重试
    } finally {
      setSaving(false)
    }
  }

  // 操作列（依赖页面内状态，故在 render 内构造）
  const actionColumn: TableColumn<UserListItem> = {
    key: 'actions',
    header: '操作',
    render: (r) => (
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="text-primary"
          onClick={() => {
            setFormUser(r)
            setFormOpen(true)
          }}
        >
          <Pencil className="h-3.5 w-3.5" />
          编辑
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
      title="用户管理"
      description="管理系统用户账号、角色与状态"
      actions={
        <Button
          variant="primary"
          onClick={() => {
            setFormUser(null)
            setFormOpen(true)
          }}
        >
          <Plus className="h-4 w-4" />
          新增用户
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
          placeholder="请输入用户名搜索"
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
        title="删除用户"
        message={`确定删除用户「${deleteTarget?.username ?? ''}」吗？删除后不可恢复。`}
        confirmText="删除"
        danger
        loading={deleting}
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* 新增/编辑用户弹窗 */}
      <UserFormDialog
        open={formOpen}
        user={formUser}
        saving={saving}
        onSubmit={handleFormSubmit}
        onCancel={() => setFormOpen(false)}
      />
    </PageContainer>
  )
}
