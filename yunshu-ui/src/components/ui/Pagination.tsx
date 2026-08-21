/**
 * Pagination —— 分页（受控组件）
 * 统一「共 N 条 + 上一页/下一页 + page/totalPages」结构，替代各列表页重复实现。
 */
import { Button } from '@/components/ui'

export interface PaginationProps {
  /** 当前页码（从 1 开始） */
  page: number
  /** 每页条数（仅用于总页数计算） */
  pageSize: number
  /** 总条数 */
  total: number
  /** 页码变化回调 */
  onChange: (page: number) => void
}

export default function Pagination({ page, pageSize, total, onChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="flex items-center justify-between gap-4 pt-4">
      <p className="text-sm text-muted-foreground">共 {total} 条</p>
      <div className="flex items-center gap-3">
        <p className="text-sm text-muted-foreground">
          {page} / {totalPages}
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="default"
            disabled={page <= 1}
            onClick={() => onChange(page - 1)}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="default"
            disabled={page >= totalPages}
            onClick={() => onChange(page + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </div>
  )
}
