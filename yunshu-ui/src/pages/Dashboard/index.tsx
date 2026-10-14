/**
 * Dashboard —— 系统管理 · 仪表盘
 * ------------------------------------------------------
 * 职责：展示核心运营指标（用户 / 角色 / 通知 / 审计 / 传感器 / 系统状态）
 * 数据源：后端 GET /api/dashboard/summary（见 src/api/dashboard.ts）
 *
 * 【2026-10-15 修「打开是空白页」】本页原是从 develop 分支搬来的商城演示页：
 *   按 {stats:{totalUsers,totalOrders,conversionRate,activeUsers}, trend, roles} 取数，
 *   而真实接口只返回**扁平**指标 ⇒ data.stats 为 undefined ⇒ 渲染期 TypeError ⇒
 *   主内容区整块空白（ContentPanel 当时也无错误边界）。
 *   现按真实契约渲染；结构不符时给出**明确的「结构不符」说明**，绝不空白或崩溃。
 *   原先那些商城图表（总订单/转化率/访问趋势/角色分布）后端从未提供数据，已随之移除。
 */
import { useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Activity, AlertTriangle, Bell, BellRing, ClipboardList,
  Loader2, Server, ShieldCheck, UserCog, Users,
} from 'lucide-react'
import { getDashboardSummary, validateDashboardSummary, type DashboardSummaryData } from '@/api/dashboard'
import { logger } from '@/utils/logger'

/** 统计卡片展示配置 */
interface StatCardConfig {
  title: string
  value: string
  icon: LucideIcon
  iconBg: string
  iconColor: string
}

/** 顶部统计卡片：白底卡片 + 左侧大数字 + 右侧圆角图标（与系统管理其余页面风格一致） */
function StatCard({ config }: { config: StatCardConfig }) {
  const Icon = config.icon
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-slate-500">{config.title}</p>
          <p className="mt-2 text-3xl font-bold tracking-tight text-slate-800">{config.value}</p>
        </div>
        <div className={'flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ' + config.iconBg}>
          <Icon className={'h-5 w-5 ' + config.iconColor} />
        </div>
      </div>
    </div>
  )
}

/** 结构不符：把校验明细摊开，别让人对着空白页猜（本仓纪律：失败必须说得出为什么） */
function ShapeMismatch({ issues }: { issues: string[] }) {
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-5">
      <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
        <AlertTriangle size={16} />
        接口返回结构与页面不匹配，已停止渲染以免空白页
      </div>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-700">
        {issues.map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-amber-700">
        后端：GET /api/dashboard/summary（plugins/admin_api.py::admin_dashboard_summary）。
        若接口有意改字段，请同步 src/api/dashboard.ts 的 DashboardSummaryData 与本页。
      </p>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardSummaryData | null>(null)
  const [issues, setIssues] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getDashboardSummary()
      .then((res) => {
        if (cancelled) return
        setIssues(validateDashboardSummary(res))
        setData(res)
      })
      .catch(() => {
        // 错误提示已由 request.ts 统一处理（toast + reject），此处记录日志便于定位
        logger.warn('[Dashboard] 仪表盘数据加载失败（详见上方 [dashboard] 请求错误日志）')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-slate-400">
        数据加载失败，请稍后重试
      </div>
    )
  }

  if (issues.length > 0) {
    return <ShapeMismatch issues={issues} />
  }

  const cards: StatCardConfig[] = [
    { title: '用户总数', value: data.totalUsers.toLocaleString(), icon: Users, iconBg: 'bg-indigo-50', iconColor: 'text-indigo-600' },
    { title: '活跃用户', value: data.activeUsers.toLocaleString(), icon: Activity, iconBg: 'bg-emerald-50', iconColor: 'text-emerald-600' },
    { title: '角色数', value: data.totalRoles.toLocaleString(), icon: UserCog, iconBg: 'bg-sky-50', iconColor: 'text-sky-600' },
    { title: '今日审计', value: data.todayAuditCount.toLocaleString(), icon: ClipboardList, iconBg: 'bg-amber-50', iconColor: 'text-amber-600' },
    { title: '传感器', value: data.sensorCount.toLocaleString(), icon: Server, iconBg: 'bg-violet-50', iconColor: 'text-violet-600' },
    { title: '通知总数', value: data.totalNotifications.toLocaleString(), icon: Bell, iconBg: 'bg-slate-100', iconColor: 'text-slate-600' },
    { title: '未读通知', value: data.unreadNotifications.toLocaleString(), icon: BellRing, iconBg: 'bg-rose-50', iconColor: 'text-rose-600' },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <StatCard key={card.title} config={card} />
        ))}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-slate-800">系统状态</h2>
        <p className="mt-0.5 text-xs text-slate-400">来自 /api/dashboard/summary 的 systemStatus</p>
        <div className="mt-3 flex items-center gap-2">
          <ShieldCheck size={16} className={data.systemStatus === 'healthy' ? 'text-emerald-600' : 'text-amber-600'} />
          <span className="text-sm text-slate-700">{data.systemStatus}</span>
        </div>
      </div>
    </div>
  )
}
