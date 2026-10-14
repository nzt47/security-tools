/**
 * Dashboard 运营统计模块接口
 * ------------------------------------------------------
 * 对应后端：GET /api/dashboard/summary
 *   - 实现：plugins/admin_api.py::admin_dashboard_summary（@_require_admin）
 *   - 返回：{code: 200, data: {...扁平指标...}, message: "success"}，request.ts 已解包 data
 *
 * 【2026-10-15 契约对齐 · 修「仪表盘空白页」】
 *   该端点的载荷是**扁平**的字段（见 DashboardSummaryData），而本模块此前声明的却是
 *   develop 分支商城演示页的 {stats:{totalUsers,totalOrders,...}, trend, roles}。
 *   于是页面读 data.stats.totalUsers 时 data.stats 为 undefined ⇒ **渲染期 TypeError**
 *   ⇒ 主内容区直接空白（当时 ContentPanel 也没有错误边界兜底）。
 *   现按后端**真实**契约声明，并让校验结果可被页面直接消费（给出「结构不符」说明而不是崩）。
 *
 * 日志约定：
 *   - 请求发起 / 成功 / 失败均经 logger 打印（前缀 [dashboard]）
 */
import { logger } from '@/utils/logger'
import request from '@/utils/request'

/** 运营统计（后端 admin_dashboard_summary 的**真实**载荷） */
export interface DashboardSummaryData {
  /** 用户总数 */
  totalUsers: number
  /** 启用中的用户数 */
  activeUsers: number
  /** 角色数 */
  totalRoles: number
  /** 通知总数 */
  totalNotifications: number
  /** 未读通知数 */
  unreadNotifications: number
  /** 今日审计条数 */
  todayAuditCount: number
  /** 传感器数量 */
  sensorCount: number
  /** 系统状态（healthy / ...） */
  systemStatus: string
}

/** 必须为 number 的字段（校验与展示共用同一份清单，避免两处漂移） */
export const DASHBOARD_NUMERIC_FIELDS = [
  'totalUsers', 'activeUsers', 'totalRoles', 'totalNotifications',
  'unreadNotifications', 'todayAuditCount', 'sensorCount',
] as const

/** 仅 dev 生效：Dashboard 错误模拟开关的 localStorage key（配合 devMock 的 ?mock_error= 参数） */
export const DASHBOARD_MOCK_ERROR_KEY = 'dashboard_mock_error'

/**
 * 校验运营统计返回结构完整性。
 * 返回异常描述列表；空数组表示结构正常。页面据此显示「结构不符」而不是崩溃或空白。
 */
export function validateDashboardSummary(data: unknown): string[] {
  const issues: string[] = []
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    issues.push('data 不是对象（收到：' + (Array.isArray(data) ? 'array' : String(data)) + '）')
    return issues
  }
  const rec = data as Record<string, unknown>
  for (const f of DASHBOARD_NUMERIC_FIELDS) {
    if (typeof rec[f] !== 'number') issues.push('字段 ' + f + ' 缺失或不是 number')
  }
  if (typeof rec.systemStatus !== 'string') issues.push('字段 systemStatus 缺失或不是 string')
  return issues
}

/** 查询入参 */
export interface GetDashboardSummaryOptions {
  /** 仅 dev：错误模拟场景值（business / empty / invalid），由 Header 下拉写入 localStorage 后传入 */
  mockError?: string
}

/** 获取运营统计总览 */
export function getDashboardSummary(options: GetDashboardSummaryOptions = {}): Promise<DashboardSummaryData> {
  logger.info('[dashboard] 请求运营统计总览 GET /dashboard/summary', options.mockError ? { mockError: options.mockError } : {})
  return request<DashboardSummaryData>({
    url: '/dashboard/summary',
    method: 'GET',
    // devMock 拦截器按 mock_error 参数返回对应错误场景；无参数时放行真实后端
    params: options.mockError ? { mock_error: options.mockError } : undefined,
  })
    .then((data) => {
      const issues = validateDashboardSummary(data)
      if (issues.length > 0) {
        logger.warn('[dashboard] 返回数据校验未通过，明细如下', issues)
      } else {
        logger.info('[dashboard] 运营统计返回成功', data)
      }
      return data
    })
    .catch((err) => {
      logger.error('[dashboard] 运营统计请求失败', err)
      throw err
    })
}
