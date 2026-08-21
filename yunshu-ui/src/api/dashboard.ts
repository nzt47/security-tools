/**
 * Dashboard 运营统计模块接口
 * ------------------------------------------------------
 * 对应后端：GET /api/dashboard/summary
 *   - 实现：agent/server_routes/routes_dashboard_summary.py（已就绪）
 *   - 返回：{code: 200, data: {...}, message: "success"}，request.ts 已解包 data
 *
 * 日志约定：
 *   - 请求发起 / 成功 / 失败均经 logger 打印（前缀 [dashboard]）
 *   - 返回数据会先做结构完整性校验，异常字段以 warn 级别暴露，
 *     便于排查"接口返回数据异常"（字段缺失 / 类型错误 / 数值越界）
 */
import { logger } from '@/utils/logger'
import request from '@/utils/request'

/** 顶部统计卡片数据 */
export interface DashboardStats {
  /** 总用户数 */
  totalUsers: number
  /** 总订单数 */
  totalOrders: number
  /** 转化率（百分比数值，如 3.42 表示 3.42%） */
  conversionRate: number
  /** 活跃用户数 */
  activeUsers: number
}

/** 访问趋势单日数据 */
export interface VisitTrendItem {
  /** 日期（MM-DD） */
  day: string
  /** 访问量 */
  visits: number
}

/** 用户角色分布单项 */
export interface RoleDistributionItem {
  /** 角色名称 */
  name: string
  /** 人数 */
  value: number
}

/** Dashboard 聚合接口完整返回 */
export interface DashboardSummaryData {
  stats: DashboardStats
  trend: VisitTrendItem[]
  roles: RoleDistributionItem[]
}

/** 合理范围常量（用于数值越界校验） */
const CONVERSION_RATE_MIN = 0
const CONVERSION_RATE_MAX = 100

/** 仅 dev 生效：Dashboard 错误模拟开关的 localStorage key（配合 devMock 的 ?mock_error= 参数使用） */
export const DASHBOARD_MOCK_ERROR_KEY = 'dashboard_mock_error'

/**
 * 校验运营统计返回结构完整性
 * 返回异常描述列表；空数组表示结构正常。调用方据此记录 warn 日志，便于排查数据异常。
 */
export function validateDashboardSummary(data: DashboardSummaryData | null): string[] {
  const issues: string[] = []
  if (!data) {
    issues.push('data 为 null / undefined')
    return issues
  }

  const { stats, trend, roles } = data

  // stats：四个统计字段必须为 number
  if (
    !stats ||
    typeof stats.totalUsers !== 'number' ||
    typeof stats.totalOrders !== 'number' ||
    typeof stats.conversionRate !== 'number' ||
    typeof stats.activeUsers !== 'number'
  ) {
    issues.push('stats 缺失或字段类型错误（需 totalUsers/totalOrders/conversionRate/activeUsers 均为 number）')
  } else if (stats.conversionRate < CONVERSION_RATE_MIN || stats.conversionRate > CONVERSION_RATE_MAX) {
    issues.push(`conversionRate 超出合理范围 [${CONVERSION_RATE_MIN}, ${CONVERSION_RATE_MAX}]：${stats.conversionRate}`)
  }

  // trend：非空数组，每项含 day(string) / visits(number)
  if (!Array.isArray(trend) || trend.length === 0) {
    issues.push('trend 为空或非数组')
  } else if (trend.some((t) => typeof t.day !== 'string' || typeof t.visits !== 'number')) {
    issues.push('trend 存在字段缺失或类型错误（需 day 为 string、visits 为 number）')
  }

  // roles：非空数组，每项含 name(string) / value(number)
  if (!Array.isArray(roles) || roles.length === 0) {
    issues.push('roles 为空或非数组')
  } else if (roles.some((r) => typeof r.name !== 'string' || typeof r.value !== 'number')) {
    issues.push('roles 存在字段缺失或类型错误（需 name 为 string、value 为 number）')
  }

  return issues
}

/** 查询入参 */
export interface GetDashboardSummaryOptions {
  /** 仅 dev：错误模拟场景值（business / empty / invalid），由 Header 下拉写入 localStorage 后传入，生产构建无意义 */
  mockError?: string
}

/** 获取运营统计总览（汇总 + 近 7 天趋势 + 角色分布，一次拉取） */
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
        logger.info('[dashboard] 运营统计返回成功', {
          stats: data.stats,
          trendCount: data.trend.length,
          rolesCount: data.roles.length,
        })
      }
      return data
    })
    .catch((err) => {
      logger.error('[dashboard] 运营统计请求失败', err)
      throw err
    })
}
