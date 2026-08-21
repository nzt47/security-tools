/**
 * Table —— 通用表格壳（泛型组件）
 * 统一表格容器 / 表头 / loading / 空态，业务只提供列定义与数据。
 * 样式一律语义 Token：容器 border-border + bg-card，表头 bg-muted/50。
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import Empty from './Empty'
import Loading from './Loading'

export interface TableColumn<T> {
  /** 列唯一标识（同时作为缺省取值键） */
  key: string
  /** 表头文案 */
  header: ReactNode
  /** 单元格渲染；缺省取 record[key] 直出 */
  render?: (record: T, index: number) => ReactNode
  /** 对齐方式，默认 left */
  align?: 'left' | 'center' | 'right'
  /** 列宽（Tailwind 宽度类），可选 */
  width?: string
}

export interface TableProps<T> {
  columns: TableColumn<T>[]
  dataSource: T[]
  /** 行唯一键 */
  rowKey: (record: T, index: number) => string | number
  /** 加载中：数据区显示 Loading */
  loading?: boolean
  /** 空态文案，默认「暂无数据」 */
  emptyText?: string
  className?: string
}

const ALIGN_CLASS: Record<NonNullable<TableColumn<never>['align']>, string> = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
}

export default function Table<T>({
  columns,
  dataSource,
  rowKey,
  loading = false,
  emptyText,
  className,
}: TableProps<T>) {
  const colSpan = columns.length

  return (
    <div className={cn('overflow-hidden rounded-lg border border-border bg-card', className)}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={cn(
                    'px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground',
                    ALIGN_CLASS[col.align ?? 'left'],
                  )}
                  style={col.width ? { width: col.width } : undefined}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={colSpan}>
                  <Loading />
                </td>
              </tr>
            ) : dataSource.length === 0 ? (
              <tr>
                <td colSpan={colSpan}>
                  <Empty description={emptyText} />
                </td>
              </tr>
            ) : (
              dataSource.map((record, index) => (
                <tr
                  key={rowKey(record, index)}
                  className="border-b border-border/60 last:border-0 hover:bg-muted/30"
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn('px-4 py-3 text-foreground', ALIGN_CLASS[col.align ?? 'left'])}
                    >
                      {col.render ? col.render(record, index) : String(record[col.key as keyof T])}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
