/**
 * 操作审计日志接口
 * 契约：GET /api/audit/logs（后端 agent/audit/logger.py 已提供基础查询；
 *       分页/操作人/关键字筛选由 devMock 先行实现，真实后端补齐参数后无缝切换）。
 */
import request from '@/utils/request'

/** 操作类型 */
export type AuditAction = 'login' | 'create' | 'update' | 'delete' | 'export' | 'other'

/** 审计日志项 */
export interface AuditLogItem {
  id: number
  /** 链路追踪 ID */
  traceId: string
  /** 操作人 */
  operator: string
  /** 操作类型 */
  action: AuditAction
  /** 操作对象（如 删除用户 user02） */
  target: string
  /** 结果：success 成功 / fail 失败 */
  result: 'success' | 'fail'
  /** 来源 IP */
  ip?: string
  /** 详情（请求参数/错误信息摘要） */
  detail?: string
  createdAt: string
}

/** 审计日志查询入参 */
export interface AuditLogParams {
  page: number
  pageSize: number
  /** 操作人（模糊匹配） */
  operator?: string
  /** 操作类型 */
  action?: AuditAction
  /** 关键字（匹配操作对象/详情） */
  keyword?: string
}

/** 审计日志分页返回 */
export interface AuditLogResult {
  list: AuditLogItem[]
  total: number
}

/** 获取审计日志（分页 + 筛选） */
export function getAuditLogs(params: AuditLogParams): Promise<AuditLogResult> {
  return request<AuditLogResult>({
    url: '/audit/logs',
    method: 'GET',
    params,
  })
}
