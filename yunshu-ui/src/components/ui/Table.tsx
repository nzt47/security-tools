/**
 * Table —— 通用表格壳（泛型组件）
 * 统一表格容器 / 表头 / loading / 空态，业务只提供列定义与数据。
 * 样式一律语义 Token：容器 border-border + bg-card，表头 bg-muted/50。
 *
 * 【2026-11-19 能力合并】hub 的 DataTable 此前自带一套表格（可拖列宽 + colStore +
 * rowProps + columns.title）。要让它变成**薄封装**而不是"再抄一遍"，先把这些能力补进原语：
 * - `resizable` / `colStore` / `colWidths` / `minWidth`：列宽拖动，状态在
 *   `lib/tableCols`（与技能中心 / 工具集 / 工作流同一份实现）——宽度落 localStorage，
 *   双击手柄恢复**该列**默认值，拖动范围有上下限；
 * - `rowProps`：逐行附加 DOM 属性（"从别处深链过来定位某一行"用，如 `data-focus-tool`）；
 * - `columns.title`：DataTable 的旧字段名，与 `header` 等价（兼容层，新代码用 header）；
 * - `bordered={false}`：外层不画卡片边框（嵌在已有 Card 里时用，避免双层框）。
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import ColResizer from '@/components/ColResizer'
import { useResizableColumns } from '@/lib/tableCols'
import Empty from './Empty'
import Loading from './Loading'

export interface TableColumn<T> {
  /** 列唯一标识（同时作为缺省取值键） */
  key: string
  /** 表头文案（新 API） */
  header?: ReactNode
  /** 表头文案（兼容 hub DataTable 的旧字段名，与 header 等价） */
  title?: ReactNode
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
  /** 逐行附加 DOM 属性（data-* 锚点等），合到 <tr> 上 */
  rowProps?: (record: T, index: number) => Record<string, string>
  /** 列宽可拖动（table-fixed + colgroup）；宽度存 localStorage，双击手柄恢复该列默认值 */
  resizable?: boolean
  /** 列宽的 localStorage 键（**每张表一个**，互不串味） */
  colStore?: string
  /** 逐列默认宽度（px）；不传就按首列宽、其余等宽 */
  colWidths?: Record<string, number>
  /** 表格最小宽度（px）：窄屏时横向滚动，而不是把列挤成不可读 */
  minWidth?: number
  /** 是否画外层卡片边框，默认 true；嵌在已有 Card 里时传 false */
  bordered?: boolean
}

/** 首列默认宽度（px）：名字/标题列总是最需要宽的那个 */
const FIRST_COL_WIDTH = 240
/** 其余列默认宽度（px） */
const OTHER_COL_WIDTH = 160

/** 各列默认宽度：首列宽一点，其余等宽；调用方可用 colWidths 逐列覆盖 */
function defaultColWidths(keys: string[], override?: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {}
  keys.forEach((key, i) => { out[key] = i === 0 ? FIRST_COL_WIDTH : OTHER_COL_WIDTH })
  return { ...out, ...(override ?? {}) }
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
  rowProps,
  resizable = false,
  colStore = 'yunshu.table.cols.v1',
  colWidths,
  minWidth,
  bordered = true,
}: TableProps<T>) {
  const colSpan = columns.length
  const { cols, startDrag, reset } = useResizableColumns(
    defaultColWidths(columns.map((c) => c.key), colWidths),
    colStore,
  )

  return (
    <div
      className={cn(
        'overflow-hidden',
        bordered && 'rounded-lg border border-border bg-card',
        className,
      )}
    >
      <div className="overflow-x-auto">
        <table
          className={cn('w-full text-sm', resizable && 'table-fixed')}
          style={resizable && minWidth ? { minWidth } : undefined}
        >
          {resizable && (
            <colgroup>
              {columns.map((col) => <col key={col.key} style={{ width: cols[col.key] }} />)}
            </colgroup>
          )}
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
                  {col.header ?? col.title}
                  {resizable && <ColResizer onStart={startDrag(col.key)} onReset={() => reset(col.key)} />}
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
                  {...(rowProps ? rowProps(record, index) : {})}
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
