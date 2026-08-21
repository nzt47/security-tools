/**
 * 系统通知与消息中心接口
 * 契约：
 *   GET  /api/notification/list        分页查询通知（支持类型 / 未读筛选）
 *   GET  /api/notification/unread-count 未读数量
 *   POST /api/notification/:id/read    单条标记已读
 *   POST /api/notification/read-all    全部标记已读
 * 说明：真实后端接口未实现，由 devMock 先行提供完整实现，后端补齐后无缝切换。
 */
import request from '@/utils/request'

/** 通知类型 */
export type NotificationType = 'system' | 'audit' | 'approval' | 'alert'

/** 通知项 */
export interface NotificationItem {
  id: number
  /** 消息类型：system 系统公告 / audit 操作审计 / approval 审批任务 / alert 安全告警 */
  type: NotificationType
  title: string
  content: string
  /** 是否已读 */
  read: boolean
  createdAt: string
}

/** 通知查询入参 */
export interface NotificationParams {
  page: number
  pageSize: number
  /** 消息类型（缺省表示全部） */
  type?: NotificationType
  /** 仅看未读（缺省表示全部） */
  unreadOnly?: boolean
}

/** 通知分页返回 */
export interface NotificationResult {
  list: NotificationItem[]
  total: number
}

/** 获取通知列表（分页 + 筛选） */
export function getNotifications(params: NotificationParams): Promise<NotificationResult> {
  return request<NotificationResult>({
    url: '/notification/list',
    method: 'GET',
    params,
  })
}

/** 获取未读通知数量 */
export function getUnreadCount(): Promise<{ unread: number }> {
  return request<{ unread: number }>({
    url: '/notification/unread-count',
    method: 'GET',
  })
}

/** 单条通知标记已读 */
export function markNotificationRead(id: number): Promise<null> {
  return request<null>({
    url: `/notification/${id}/read`,
    method: 'POST',
  })
}

/** 全部通知标记已读 */
export function markAllNotificationsRead(): Promise<null> {
  return request<null>({
    url: '/notification/read-all',
    method: 'POST',
  })
}
