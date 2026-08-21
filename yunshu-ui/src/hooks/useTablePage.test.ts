/**
 * useTablePage 单元测试
 * - 挂载自动拉取 / loading 生命周期
 * - handleSearch 重置页码 + 合并筛选 / handleReset 回默认
 * - goPage 越界/非法回退
 * - 竞态防护：过期响应丢弃
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useTablePage } from './useTablePage'

interface Item {
  id: number
}

interface Query {
  page: number
  pageSize: number
  keyword: string
}

const DEFAULT_QUERY: Query = { page: 1, pageSize: 10, keyword: '' }

function makeFetcher(list: Item[], total = list.length) {
  return vi.fn(async (_params: Query) => ({ list, total }))
}

describe('useTablePage', () => {
  it('挂载后自动拉取并填充 list / total，结束后 loading 为 false', async () => {
    const fetcher = makeFetcher([{ id: 1 }, { id: 2 }])
    const { result } = renderHook(() => useTablePage<Item, Query>({ fetcher, defaultQuery: DEFAULT_QUERY }))

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.list).toHaveLength(2)
    expect(result.current.total).toBe(2)
    expect(result.current.totalPages).toBe(1)
  })

  it('handleSearch 重置页码为 1 并合并筛选字段', async () => {
    const fetcher = makeFetcher([])
    const { result } = renderHook(() => useTablePage<Item, Query>({ fetcher, defaultQuery: DEFAULT_QUERY }))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.setQuery((q) => ({ ...q, page: 3 }))
    })
    await waitFor(() => expect(result.current.query.page).toBe(3))

    act(() => {
      result.current.handleSearch({ keyword: '张三' })
    })
    expect(result.current.query.page).toBe(1)
    expect(result.current.query.keyword).toBe('张三')
  })

  it('handleReset 回到默认查询', async () => {
    const fetcher = makeFetcher([])
    const { result } = renderHook(() => useTablePage<Item, Query>({ fetcher, defaultQuery: DEFAULT_QUERY }))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => {
      result.current.handleSearch({ keyword: '李四' })
    })
    expect(result.current.query.keyword).toBe('李四')

    act(() => {
      result.current.handleReset()
    })
    expect(result.current.query).toEqual(DEFAULT_QUERY)
  })

  it('goPage 越界时回退为最后一页，非法输入保持当前页', async () => {
    const fetcher = makeFetcher(Array.from({ length: 45 }, (_, i) => ({ id: i })))
    const { result } = renderHook(() => useTablePage<Item, Query>({ fetcher, defaultQuery: DEFAULT_QUERY }))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.totalPages).toBe(5)

    act(() => result.current.goPage(99))
    expect(result.current.query.page).toBe(5)

    const pageBefore = result.current.query.page
    act(() => result.current.goPage(0))
    expect(result.current.query.page).toBe(pageBefore)
  })

  it('竞态防护：先发请求的慢响应不覆盖后发请求的结果', async () => {
    const slow = vi.fn(
      () => new Promise<{ list: Item[]; total: number }>((resolve) => setTimeout(() => resolve({ list: [{ id: 1 }], total: 1 }), 30)),
    )
    const fast = vi.fn(async () => ({ list: [{ id: 2 }], total: 1 }))

    const { result } = renderHook(() =>
      useTablePage<Item, Query>({
        fetcher: (q) => (q.page === 1 ? slow() : fast()),
        defaultQuery: DEFAULT_QUERY,
      }),
    )

    // 触发两次请求：page=1（慢）→ page=2（快）
    await act(async () => {
      result.current.setQuery((q) => ({ ...q, page: 2 }))
      await Promise.resolve()
    })

    await waitFor(() => expect(result.current.loading).toBe(false))
    // 慢响应（page=1 的 {id:1}）应被丢弃，最终展示快响应 {id:2}
    expect(result.current.list).toEqual([{ id: 2 }])
  })
})
