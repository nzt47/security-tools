/**
 * AuditList —— 系统管理 / 操作审计
 * 功能：操作人 / 操作类型 / 关键字筛选 + 分页查询 + 结果与详情展示
 * 数据源：@/api/audit（request.ts 已解包，直接返回业务数据）
 * 结构：useTablePage + PageContainer + Table + Pagination
 */
import { useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { getAuditLogs, type AuditAction, type AuditLogItem, type AuditLogParams } from '@/api/audit'
import { Button, Card, Input, PageContainer, Pagination, Select, Table, type TableColumn } from '@/components/ui'
import { useTablePage } from '@/hooks/useTablePage'

const PAGE_SIZE = 10

interface AuditQuery {
  page: number
  pageSize: number
  operator: string
  action: AuditAction | ''
  keyword: string
}

const ACTION_OPTIONS = [
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

const columns: TableColumn<AuditLogItem>[] = [
  { key: 'createdAt', header: '时间', render: (r) => <span className="whitespace-nowrap text-muted-foreground">{r.createdAt}</span> },
  { key: 'operator', header: '操作人', render: (r) => <span className="font-medium text-foreground">{r.operator}</span> },
  {
    key: 'action',
    header: '类型',
    render: (r) => (
      <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
        {ACTION_LABEL[r.action] ?? r.action}
      </span>
    ),
  },
  { key: 'target', header: '操作对象', render: (r) => <span className="text-foreground">{r.target}</span> },
  {
    key: 'result',
    header: '结果',
    render: (r) =>
      r.result === 'success' ? (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-0.5 text-xs font-medium text-success">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          成功
        </span>
      ) : (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-danger/10 px-2.5 py-0.5 text-xs font-medium text-danger">
          <span className="h-1.5 w-1.5 rounded-full bg-danger" />
          失败
        </span>
      ),
  },
  { key: 'ip', header: '来源 IP', render: (r) => <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">{r.ip}</span> },
  {
    key: 'detail',
    header: '详情',
    render: (r) => (
      <span className="block max-w-[240px] truncate text-muted-foreground" title={r.detail}>
        {r.detail || '-'}
      </span>
    ),
  },
]

export default function AuditList() {
  const [operatorInput, setOperatorInput] = useState('')
  const [actionInput, setActionInput] = useState<AuditAction | ''>('')
  const [keywordInput, setKeywordInput] = useState('')

  const { query, setQuery, list, total, loading, handleSearch, handleReset } = useTablePage<
    AuditLogItem,
    AuditQuery
  >({
    // 【Why】action 为 '' 表示全部类型，转 undefined 以符合 AuditLogParams 可选语义
    fetcher: (q) => {
      const params: AuditLogParams = {
        page: q.page,
        pageSize: q.pageSize,
        operator: q.operator,
        action: q.action || undefined,
        keyword: q.keyword,
      }
      return getAuditLogs(params)
    },
    defaultQuery: { page: 1, pageSize: PAGE_SIZE, operator: '', action: '', keyword: '' },
  })

  const doSearch = () =>
    handleSearch({
      operator: operatorInput.trim(),
      action: actionInput,
      keyword: keywordInput.trim(),
    })

  const doReset = () => {
    setOperatorInput('')
    setActionInput('')
    setKeywordInput('')
    handleReset()
  }

  return (
    <PageContainer title="操作审计" description="记录系统内关键操作行为，支持多维筛选">
      {/* 顶部筛选区 */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={operatorInput}
          onChange={(e) => setOperatorInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') doSearch()
          }}
          placeholder="操作人"
          className="w-40"
        />
        <Select
          options={ACTION_OPTIONS}
          value={actionInput}
          onChange={(v) => setActionInput(v as AuditAction | '')}
          className="w-32"
        />
        <Input
          value={keywordInput}
          onChange={(e) => setKeywordInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') doSearch()
          }}
          placeholder="操作对象 / 详情关键字"
          className="w-56"
        />
        <Button variant="primary" onClick={doSearch}>
          <Search className="h-4 w-4" />
          查询
        </Button>
        <Button variant="default" onClick={doReset}>
          <RefreshCw className="h-4 w-4" />
          重置
        </Button>
      </div>

      {/* 表格区 */}
      <Card>
        <Table columns={columns} dataSource={list} loading={loading} rowKey={(r) => r.id} />
      </Card>

      {/* 底部分页 */}
      <Pagination
        page={query.page}
        pageSize={query.pageSize}
        total={total}
        onChange={(p) => setQuery((q) => ({ ...q, page: p }))}
      />
    </PageContainer>
  )
}
