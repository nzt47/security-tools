/**
 * AuditList —— 系统管理 / 操作审计
 * 功能：操作人 / 操作类型 / 关键字筛选 + 分页查询 + 结果与详情展示
 * 数据源：@/api/audit（request.ts 已解包，直接返回业务数据）
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Search } from 'lucide-react'
import { getAuditLogs, type AuditAction, type AuditLogItem, type AuditLogParams } from '@/api/audit'

const PAGE_SIZE = 10

const ACTION_OPTIONS: Array<{ value: AuditAction | ''; label: string }> = [
  { value: '', label: '全部类型' },
  { value: 'login', label: '登录' },
  { value: 'create', label: '新增' },
  { value: 'update', label: '更新' },
  { value: 'delete', label: '删除' },
  { value: 'export', label: '导出' },
]

const ACTION_LABEL: Record<string, string> = {
  login: '登录',
  create: '新增',
  update: '更新',
  delete: '删除',
  export: '导出',
  other: '其他',
}

export default function AuditList() {
  // 查询参数 —— 唯一的列表数据源
  const [query, setQuery] = useState<{
    page: number
    pageSize: number
    operator: string
    action: AuditAction | ''
    keyword: string
  }>({ page: 1, pageSize: PAGE_SIZE, operator: '', action: '', keyword: '' })

  const [operatorInput, setOperatorInput] = useState('')
  const [actionInput, setActionInput] = useState<AuditAction | ''>('')
  const [keywordInput, setKeywordInput] = useState('')

  const [list, setList] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  const totalPages = Math.max(1, Math.ceil(total / query.pageSize))

  /** 拉取列表：query 变化即触发 */
  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      // 【Why】action 为 '' 表示全部类型，转 undefined 以符合 AuditLogParams 可选语义
      const params: AuditLogParams = {
        page: query.page,
        pageSize: query.pageSize,
        operator: query.operator,
        action: query.action || undefined,
        keyword: query.keyword,
      }
      const res = await getAuditLogs(params)
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

  const handleSearch = () => {
    setQuery((q) => ({ ...q, page: 1, operator: operatorInput.trim(), action: actionInput, keyword: keywordInput.trim() }))
  }

  const handleReset = () => {
    setOperatorInput('')
    setActionInput('')
    setKeywordInput('')
    setQuery({ page: 1, pageSize: PAGE_SIZE, operator: '', action: '', keyword: '' })
  }

  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="mb-4 text-2xl font-semibold text-slate-800">操作审计</h1>

      {/* 顶部筛选区 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          value={operatorInput}
          onChange={(e) => setOperatorInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch()
          }}
          placeholder="操作人"
          className="w-40 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none"
        />
        <select
          value={actionInput}
          onChange={(e) => setActionInput(e.target.value as AuditAction | '')}
          className="w-32 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-blue-500 focus:outline-none"
        >
          {ACTION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSearch()
          }}
          placeholder="操作对象 / 详情关键字"
          className="w-56 rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none"
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
      </div>

      {/* 中间表格区 */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              {['时间', '操作人', '类型', '操作对象', '结果', '来源 IP', '详情'].map((head) => (
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
                  <td className="whitespace-nowrap px-4 py-3 text-slate-500">{row.createdAt}</td>
                  <td className="px-4 py-3 font-medium text-slate-700">{row.operator}</td>
                  <td className="px-4 py-3">
                    <span className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-600">
                      {ACTION_LABEL[row.action] ?? row.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{row.target}</td>
                  <td className="px-4 py-3">
                    {row.result === 'success' ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-600">
                        <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
                        成功
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-600">
                        <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
                        失败
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-slate-500">{row.ip}</td>
                  <td className="max-w-[240px] truncate px-4 py-3 text-slate-500" title={row.detail}>
                    {row.detail || '-'}
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
        </div>
      </div>
    </div>
  )
}
