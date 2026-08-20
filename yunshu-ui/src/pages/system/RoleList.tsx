/**
 * RoleList —— 系统管理 / 角色列表（RBAC 角色管理页）
 * 功能：关键字搜索 / 分页查询 / 新增·编辑·删除 / 权限分配（勾选全量覆盖）
 * 数据源：@/api/role（request.ts 已解包，直接返回业务数据）
 */
import { useCallback, useEffect, useState } from 'react'
import { KeyRound, Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import {
  assignRolePermissions,
  createRole,
  deleteRole,
  getRoleList,
  updateRole,
  type RoleItem,
} from '@/api/role'
import { toast } from '@/components/Toaster'
import ConfirmDialog from '@/components/ConfirmDialog'
import RoleFormDialog from './RoleFormDialog'
import PermissionAssignDialog from './PermissionAssignDialog'

const PAGE_SIZE = 10

export default function RoleList() {
  // 查询参数（页码 / 每页条数 / 关键字）—— 唯一的列表数据源
  const [query, setQuery] = useState({ page: 1, pageSize: PAGE_SIZE, keyword: '' })
  const [keywordInput, setKeywordInput] = useState('')
  const [pageInput, setPageInput] = useState('1')

  const [list, setList] = useState<RoleItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

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

  const totalPages = Math.max(1, Math.ceil(total / query.pageSize))

  /** 拉取列表：query 变化即触发 */
  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getRoleList(query)
      setList(res.list)
      setTotal(res.total)
    } catch {
      // 错误提示已由 request.ts 统一处理，此处仅结束加载态
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void fetchList()
  }, [fetchList])

  useEffect(() => {
    setPageInput(String(query.page))
  }, [query.page])

  const handleSearch = () => {
    setQuery((q) => ({ ...q, page: 1, keyword: keywordInput.trim() }))
  }

  const handleReset = () => {
    setKeywordInput('')
    setQuery({ page: 1, pageSize: PAGE_SIZE, keyword: '' })
  }

  const handleGoPage = () => {
    const target = Number(pageInput)
    if (!Number.isInteger(target) || target < 1) {
      setPageInput(String(query.page))
      return
    }
    setQuery((q) => ({ ...q, page: Math.min(target, totalPages) }))
  }

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

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-4 text-2xl font-semibold text-slate-800">角色管理</h1>

      {/* 顶部搜索区 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch()
          }}
          placeholder="请输入角色名或显示名搜索"
          className="w-64 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none"
        />
        <button
          type="button"
          onClick={handleSearch}
          className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500"
        >
          <Search className="h-4 w-4" />
          查询
        </button>
        <button
          type="button"
          onClick={handleReset}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
        >
          <RefreshCw className="h-4 w-4" />
          重置
        </button>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => {
            setFormRole(null)
            setFormOpen(true)
          }}
          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500"
        >
          <Plus className="h-4 w-4" />
          新增角色
        </button>
      </div>

      {/* 中间表格区 */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              {['ID', '角色标识', '显示名', '描述', '权限数', '创建时间', '操作'].map((head) => (
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
            ) : list.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-slate-400">
                  暂无数据
                </td>
              </tr>
            ) : (
              list.map((row) => (
                <tr key={row.id} className="transition hover:bg-slate-50">
                  <td className="px-4 py-3 text-slate-500">{row.id}</td>
                  <td className="px-4 py-3 font-medium text-slate-700">{row.name}</td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-600">
                      {row.label}
                    </span>
                  </td>
                  <td className="max-w-[200px] truncate px-4 py-3 text-slate-500">{row.description}</td>
                  <td className="px-4 py-3 text-slate-500">{row.permissions.length}</td>
                  <td className="px-4 py-3 text-slate-500">{row.createdAt}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setFormRole(row)
                          setFormOpen(true)
                        }}
                        className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-blue-600 transition hover:bg-blue-50"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setAssignRole(row)
                          setAssignOpen(true)
                        }}
                        className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-violet-600 transition hover:bg-violet-50"
                      >
                        <KeyRound className="h-3.5 w-3.5" />
                        权限
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(row)}
                        className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-red-600 transition hover:bg-red-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 底部分页 */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500">
          共 <span className="font-medium text-slate-700">{total}</span> 条
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={query.page <= 1 || loading}
            onClick={() => setQuery((q) => ({ ...q, page: q.page - 1 }))}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            上一页
          </button>
          <span className="text-sm text-slate-600">
            {query.page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={query.page >= totalPages || loading}
            onClick={() => setQuery((q) => ({ ...q, page: q.page + 1 }))}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            下一页
          </button>
          <div className="ml-2 flex items-center gap-1">
            <span className="text-sm text-slate-500">跳至</span>
            <input
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value.replace(/\D/g, ''))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleGoPage()
              }}
              className="w-14 rounded-md border border-slate-300 px-2 py-1.5 text-center text-sm text-slate-700 focus:border-blue-500 focus:outline-none"
            />
            <span className="text-sm text-slate-500">页</span>
          </div>
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
    </div>
  )
}
