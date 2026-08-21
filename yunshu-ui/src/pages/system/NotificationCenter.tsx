/**
 * NotificationCenter —— 系统管理 / 消息中心
 * 功能：通知列表（系统公告 / 审计提醒 / 审批任务 / 安全告警）+ 未读筛选 + 已读管理（单条/全部）+ 未读计数 + 分页
 * 数据源：@/api/notification（request.ts 已解包，直接返回业务数据）
 * 结构：useTablePage（列表/筛选/分页）+ 页面内 unread 计数 + 自定义列表渲染
 */
import { useState } from 'react'
import { Bell, BellRing, CheckCheck } from 'lucide-react'
import {
  getNotifications,
  getUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
  type NotificationType,
} from '@/api/notification'
import { Button, Card, Empty, Loading, PageContainer, Pagination, Select } from '@/components/ui'
import { useTablePage } from '@/hooks/useTablePage'

const PAGE_SIZE = 10

interface NotificationQuery {
  page: number
  pageSize: number
  type: NotificationType | ''
  unreadOnly: boolean
}

const TYPE_OPTIONS = [
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

/** 类型徽章样式（语义 Token：安全告警走 danger，系统公告走 primary，其余走 muted） */
const TYPE_BADGE: Record<NotificationType, string> = {
  system: 'bg-primary/10 text-primary',
  audit: 'bg-muted text-muted-foreground',
  approval: 'bg-muted text-foreground',
  alert: 'bg-danger/10 text-danger',
}

export default function NotificationCenter() {
  const [typeInput, setTypeInput] = useState<NotificationType | ''>('')
  const [unreadOnlyInput, setUnreadOnlyInput] = useState(false)
  const [unread, setUnread] = useState(0)

  const { query, setQuery, setList, list, total, loading, handleSearch, handleReset } = useTablePage<
    NotificationItem,
    NotificationQuery
  >({
    // 【Why】列表与未读计数并行拉取；unread 为页面内状态，由 fetcher 闭包同步
    fetcher: async (q) => {
      const [res, countRes] = await Promise.all([
        getNotifications({
          page: q.page,
          pageSize: q.pageSize,
          type: q.type || undefined,
          unreadOnly: q.unreadOnly || undefined,
        }),
        getUnreadCount(),
      ])
      setUnread(countRes.unread)
      return res
    },
    defaultQuery: { page: 1, pageSize: PAGE_SIZE, type: '', unreadOnly: false },
  })

  /** 单条标记已读：成功后本地更新该条状态与未读数（无需重拉） */
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
      setUnread(0)
    } catch {
      // 错误提示已由 request.ts 统一处理
    }
  }

  return (
    <PageContainer
      title="消息中心"
      description="系统通知、审计提醒与安全告警"
      actions={
        <Button variant="default" onClick={() => void handleMarkAllRead()} disabled={unread === 0 || loading}>
          <CheckCheck className="h-4 w-4" />
          全部已读
        </Button>
      }
    >
      {/* 顶部未读统计 */}
      <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-card">
        {unread > 0 ? <BellRing className="h-5 w-5 text-danger" /> : <Bell className="h-5 w-5 text-muted-foreground" />}
        <p className="text-sm text-foreground">
          {unread > 0 ? (
            <>
              您有 <span className="font-semibold text-danger">{unread}</span> 条未读通知
            </>
          ) : (
            '没有未读通知'
          )}
        </p>
      </div>

      {/* 筛选区 */}
      <div className="flex flex-wrap items-center gap-3">
        <Select
          options={TYPE_OPTIONS}
          value={typeInput}
          onChange={(v) => setTypeInput(v as NotificationType | '')}
          className="w-32"
        />
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={unreadOnlyInput}
            onChange={(e) => setUnreadOnlyInput(e.target.checked)}
            className="h-4 w-4 rounded accent-primary"
          />
          仅看未读
        </label>
        <Button
          variant="primary"
          onClick={() => handleSearch({ type: typeInput, unreadOnly: unreadOnlyInput })}
        >
          查询
        </Button>
        <Button
          variant="default"
          onClick={() => {
            setTypeInput('')
            setUnreadOnlyInput(false)
            handleReset()
          }}
        >
          重置
        </Button>
      </div>

      {/* 通知列表 */}
      <Card>
        {loading ? (
          <Loading />
        ) : list.length === 0 ? (
          <Empty />
        ) : (
          <ul className="divide-y divide-border">
            {list.map((item) => (
              <li key={item.id} className="flex items-start gap-3 px-4 py-4 transition hover:bg-muted/30">
                {/* 未读红点 */}
                <span
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.read ? 'bg-transparent' : 'bg-danger'}`}
                  aria-label={item.read ? '已读' : '未读'}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${TYPE_BADGE[item.type]}`}>
                      {TYPE_LABEL[item.type] ?? item.type}
                    </span>
                    <h3 className="text-sm font-medium text-foreground">{item.title}</h3>
                    <span className="ml-auto whitespace-nowrap text-xs text-muted-foreground">{item.createdAt}</span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{item.content}</p>
                </div>
                {!item.read && (
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => void handleMarkRead(item)}
                  >
                    标记已读
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 分页 */}
      <Pagination
        page={query.page}
        pageSize={query.pageSize}
        total={total}
        onChange={(p) => setQuery((q) => ({ ...q, page: p }))}
      />
    </PageContainer>
  )
}
