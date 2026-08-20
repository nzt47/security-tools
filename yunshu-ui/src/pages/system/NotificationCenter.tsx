/**
 * NotificationCenter —— 系统管理 / 消息中心
 * 功能：通知列表（系统公告 / 审计提醒 / 审批任务 / 安全告警）+ 未读筛选 + 已读管理（单条/全部）+ 未读计数 + 分页
 * 数据源：@/api/notification（request.ts 已解包，直接返回业务数据）
 */
import { useCallback, useEffect, useState } from 'react'
import { Bell, BellRing, CheckCheck, Loader2, Search } from 'lucide-react'
import {
  getNotifications,
  getUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
  type NotificationType,
} from '@/api/notification'

const PAGE_SIZE = 10

const TYPE_OPTIONS: Array<{ value: NotificationType | ''; label: string }> = [
  { value: '', label: '全部类型' },
  { value: 'system', label: '系统公告' },
  { value: 'audit', label: '审计提醒' },
  { value: 'approval', label: '审批任务' },
  { value: 'alert', label: '安全告警' },
]

const TYPE_LABEL: Record<NotificationType, string> = {
  system: '系统公告',
  audit: '审计提醒',
  approval: '审批任务',
  alert: '安全告警',
}

/** 类型徽章样式（Tailwind 类，按类型区分色系） */
const TYPE_BADGE: Record<NotificationType, string> = {
  system: 'bg-blue-50 text-blue-600',
  audit: 'bg-purple-50 text-purple-600',
  approval: 'bg-amber-50 text-amber-600',
  alert: 'bg-red-50 text-red-600',
}

export default function NotificationCenter() {
  // 查询参数 —— 列表唯一数据源
  const [query, setQuery] = useState<{
    page: number
    pageSize: number
    type: NotificationType | ''
    unreadOnly: boolean
  }>({ page: 1, pageSize: PAGE_SIZE, type: '', unreadOnly: false })

  const [typeInput, setTypeInput] = useState<NotificationType | ''>('')
  const [unreadOnlyInput, setUnreadOnlyInput] = useState(false)

  const [list, setList] = useState<NotificationItem[]>([])
  const [total, setTotal] = useState(0)
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(false)

  const totalPages = Math.max(1, Math.ceil(total / query.pageSize))

  /** 拉取列表与未读计数 */
  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const [res, countRes] = await Promise.all([
        getNotifications({
          page: query.page,
          pageSize: query.pageSize,
          type: query.type || undefined,
          unreadOnly: query.unreadOnly || undefined,
        }),
        getUnreadCount(),
      ])
      setList(res.list)
      setTotal(res.total)
      setUnread(countRes.unread)
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
    setQuery((q) => ({ ...q, page: 1, type: typeInput, unreadOnly: unreadOnlyInput }))
  }

  const handleReset = () => {
    setTypeInput('')
    setUnreadOnlyInput(false)
    setQuery({ page: 1, pageSize: PAGE_SIZE, type: '', unreadOnly: false })
  }

  /** 单条标记已读：成功后本地刷新该条状态与未读数 */
  const handleMarkRead = async (item: NotificationItem) => {
    if (item.read) return
    try {
      await markNotificationRead(item.id)
      setList((rows) => rows.map((r) => (r.id === item.id ? { ...r, read: true } : r)))
      setUnread((n) => Math.max(0, n - 1))
    } catch {
      // 错误提示已由 request.ts 统一处理
    }
  }

  /** 全部标记已读：成功后本地清空未读态 */
  const handleMarkAllRead = async () => {
    try {
      await markAllNotificationsRead()
      setList((rows) => rows.map((r) => ({ ...r, read: true })))
      setUnread(0)
    } catch {
      // 错误提示已由 request.ts 统一处理
    }
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-slate-800">消息中心</h1>
        <button
          type="button"
          onClick={handleMarkAllRead}
          disabled={unread === 0 || loading}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CheckCheck className="h-4 w-4" />
          全部已读
        </button>
      </div>

      {/* 顶部未读统计 */}
      <div className="mb-4 flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        {unread > 0 ? (
          <BellRing className="h-5 w-5 text-orange-500" />
        ) : (
          <Bell className="h-5 w-5 text-slate-400" />
        )}
        <p className="text-sm text-slate-600">
          {unread > 0 ? (
            <>
              您有 <span className="font-semibold text-orange-600">{unread}</span> 条未读通知
            </>
          ) : (
            '没有未读通知'
          )}
        </p>
      </div>

      {/* 筛选区 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          value={typeInput}
          onChange={(e) => setTypeInput(e.target.value as NotificationType | '')}
          className="w-32 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:border-blue-500 focus:outline-none"
        >
          {TYPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={unreadOnlyInput}
            onChange={(e) => setUnreadOnlyInput(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          仅看未读
        </label>
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
          className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
        >
          重置
        </button>
      </div>

      {/* 通知列表 */}
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        {loading ? (
          <div className="px-4 py-16 text-center">
            <Loader2 className="mx-auto h-6 w-6 animate-spin text-blue-500" />
          </div>
        ) : list.length === 0 ? (
          <div className="px-4 py-16 text-center text-slate-400">暂无数据</div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {list.map((item) => (
              <li key={item.id} className="flex items-start gap-3 px-4 py-4 transition hover:bg-slate-50">
                {/* 未读红点 */}
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.read ? 'bg-transparent' : 'bg-red-500'}`}
                  aria-label={item.read ? '已读' : '未读'}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${TYPE_BADGE[item.type]}`}>
                      {TYPE_LABEL[item.type] ?? item.type}
                    </span>
                    <h3 className="text-sm font-medium text-slate-800">{item.title}</h3>
                    <span className="ml-auto whitespace-nowrap text-xs text-slate-400">{item.createdAt}</span>
                  </div>
                  <p className="mt-1 text-sm text-slate-500">{item.content}</p>
                </div>
                {!item.read && (
                  <button
                    type="button"
                    onClick={() => void handleMarkRead(item)}
                    className="shrink-0 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-500 transition hover:bg-slate-50"
                  >
                    标记已读
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 分页 */}
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
