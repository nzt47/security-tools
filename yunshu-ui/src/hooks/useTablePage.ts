/**
 * useTablePage —— 列表页统一抽象（分页 + 搜索 + 加载 + 竞态防护）
 * ------------------------------------------------------
 * 统一系统管理列表页（UserList/RoleList/AuditList/NotificationCenter）的
 * "query 状态 + 列表拉取 + loading + search/reset/goPage"生命周期，
 * 页面只保留业务列定义与增删改提交逻辑。
 *
 * 竞态防护：请求序号比对，过期响应丢弃（对齐 useLayoutStore 流式竞态处理思路）。
 * 错误提示由 request.ts 拦截器统一处理，本 hook 仅结束 loading。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { logger } from '@/utils/logger'

export interface UseTablePageOptions<T, P> {
  /** 列表请求（须幂等；request.ts 已解包返回 { list, total }） */
  fetcher: (params: P) => Promise<{ list: T[]; total: number }>
  /** 默认查询参数（须含 page/pageSize） */
  defaultQuery: P
  /** 外部依赖（如路由参数）：变化时重置回默认查询并重拉 */
  deps?: unknown[]
}

export interface UseTablePageResult<T, P> {
  query: P
  setQuery: React.Dispatch<React.SetStateAction<P>>
  /** 直接设置列表（消息中心"标记已读"等本地状态更新场景） */
  setList: React.Dispatch<React.SetStateAction<T[]>>
  list: T[]
  total: number
  loading: boolean
  page: number
  pageSize: number
  totalPages: number
  /** 手动刷新（保留当前 query，增删改后调用） */
  fetchList: () => Promise<void>
  /** 搜索：合并筛选字段并重置页码为 1 */
  handleSearch: (filters: Partial<P>) => void
  /** 重置：回到默认查询（可选覆盖默认值） */
  handleReset: (overrides?: Partial<P>) => void
  /** 跳转指定页（非数字/越界回退当前页） */
  goPage: (target: number) => void
}

export function useTablePage<T, P extends { page: number; pageSize: number }>({
  fetcher,
  defaultQuery,
  deps = [],
}: UseTablePageOptions<T, P>): UseTablePageResult<T, P> {
  const [query, setQuery] = useState<P>(defaultQuery)
  const [list, setList] = useState<T[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  // 请求序号：比对新旧响应，过期丢弃（组件卸载后不 setState）
  const seqRef = useRef(0)
  // 【Why】fetcher 经 ref 持有而非进 useCallback 依赖：调用方传入内联函数（如按 page 分流的 lambda）
  // 会导致 fetchList 每次渲染重建 → useEffect 无限重拉。ref 保证 fetchList 仅随 query 变化。
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const page = query.page
  const pageSize = query.pageSize
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const fetchList = useCallback(async () => {
    const seq = ++seqRef.current
    logger.info('[useTablePage] 拉取列表开始', query)
    setLoading(true)
    try {
      const res = await fetcherRef.current(query)
      if (seq !== seqRef.current) {
        logger.warn('[useTablePage] 过期响应已丢弃', { seq, currentSeq: seqRef.current, query })
        return
      }
      setList(res.list)
      setTotal(res.total)
      logger.info('[useTablePage] 列表拉取成功', { listSize: res.list.length, total: res.total, query })
    } catch (err) {
      logger.error('[useTablePage] 列表拉取失败', { query, error: err })
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [query])

  useEffect(() => {
    void fetchList()
  }, [fetchList])

  // 外部依赖变化时重置回默认查询（默认空数组 → 仅挂载时执行一次）
  useEffect(() => {
    setQuery(defaultQuery)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const handleSearch = useCallback((filters: Partial<P>) => {
    logger.info('[useTablePage] 触发搜索，重置页码为 1', filters)
    setQuery((q) => ({ ...q, ...filters, page: 1 }))
  }, [])

  const handleReset = useCallback(
    (overrides?: Partial<P>) => {
      logger.info('[useTablePage] 重置搜索条件', overrides ?? {})
      setQuery({ ...defaultQuery, ...overrides })
    },
    [defaultQuery],
  )

  const goPage = useCallback(
    (target: number) => {
      if (!Number.isInteger(target) || target < 1) {
        logger.warn('[useTablePage] 跳转页码非法，保持当前页', { target })
        return
      }
      const page = Math.min(target, totalPages)
      if (page !== target) {
        logger.info('[useTablePage] 跳转页码超出范围，钳制到末页', { target, page })
      } else {
        logger.info('[useTablePage] 跳转到指定页', { page })
      }
      setQuery((q) => ({ ...q, page }))
    },
    [totalPages],
  )

  return {
    query,
    setQuery,
    setList,
    list,
    total,
    loading,
    page,
    pageSize,
    totalPages,
    fetchList,
    handleSearch,
    handleReset,
    goPage,
  }
}
