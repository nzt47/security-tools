/**
 * UserList —— 系统管理 / 用户列表（CRUD 表格页模板）
 * 功能：关键字搜索 / 分页查询 / Loading 态 / 删除确认（ConfirmDialog 封装）
 * 数据源：@/api/user（request.ts 已解包，直接返回业务数据）
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import { createUser, deleteUser, getUserList, updateUser, type UserListItem } from '@/api/user'
import { toast } from '@/components/Toaster'
import ConfirmDialog from '@/components/ConfirmDialog'
import { notify } from '@/utils/request'
import UserFormDialog from './UserFormDialog'

const PAGE_SIZE = 10

export default function UserList() {
  // 查询参数（页码 / 每页条数 / 关键字）—— 唯一的列表数据源
  const [query, setQuery] = useState({ page: 1, pageSize: PAGE_SIZE, keyword: '' })
  // 搜索输入框（受控，点击「查询」后才写入 query）
  const [keywordInput, setKeywordInput] = useState('')
  // 跳转页码输入框（受控）
  const [pageInput, setPageInput] = useState('1')

  const [list, setList] = useState<UserListItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  // 删除目标（非空时打开确认弹窗）与确认请求中的防重提交标记
  const [deleteTarget, setDeleteTarget] = useState<UserListItem | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 新增/编辑弹窗状态：formUser=null 表示新增，非空表示编辑目标；saving 防重复提交
  const [formOpen, setFormOpen] = useState(false)
  const [formUser, setFormUser] = useState<UserListItem | null>(null)
  const [saving, setSaving] = useState(false)

  const totalPages = Math.max(1, Math.ceil(total / query.pageSize))

  /** 拉取列表：query 变化即触发 */
  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getUserList(query)
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

  // 页码变化时同步跳转输入框
  useEffect(() => {
    setPageInput(String(query.page))
  }, [query.page])

  /** 查询：重置页码为 1 后重新请求 */
  const handleSearch = () => {
    setQuery((q) => ({ ...q, page: 1, keyword: keywordInput.trim() }))
  }

  /** 重置：清空关键字并回到第一页 */
  const handleReset = () => {
    setKeywordInput('')
    setQuery({ page: 1, pageSize: PAGE_SIZE, keyword: '' })
  }

  /** 跳转指定页：非数字/越界回退为当前页 */
  const handleGoPage = () => {
    const target = Number(pageInput)
    if (!Number.isInteger(target) || target < 1) {
      setPageInput(String(query.page))
      return
    }
    setQuery((q) => ({ ...q, page: Math.min(target, totalPages) }))
  }

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

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-4 text-2xl font-semibold text-slate-800">用户管理</h1>

      {/* 顶部搜索区 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch()
          }}
          placeholder="请输入用户名搜索"
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
            setFormUser(null)
            setFormOpen(true)
          }}
          className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500"
        >
          <Plus className="h-4 w-4" />
          新增用户
        </button>
      </div>

      {/* 中间表格区 */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              {['ID', '用户名', '邮箱', '角色', '状态', '创建时间', '操作'].map((head) => (
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
                  <td className="px-4 py-3 font-medium text-slate-700">{row.username}</td>
                  <td className="px-4 py-3 text-slate-500">{row.email}</td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-600">
                      {row.role}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {row.status === 1 ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-600">
                        <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                        启用
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-500">
                        <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                        禁用
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">{row.createdAt}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          setFormUser(row)
                          setFormOpen(true)
                        }}
                        className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-medium text-blue-600 transition hover:bg-blue-50"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        编辑
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
    </div>
  )
}
