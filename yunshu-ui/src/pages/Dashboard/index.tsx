/**
 * Dashboard —— 仪表盘页面
 * ------------------------------------------------------
 * 职责：展示核心运营指标与数据概览
 *   - 顶部 4 张统计卡片（总用户 / 总订单 / 转化率 / 活跃用户）
 *   - 折线图：近 7 天访问趋势
 *   - 饼图：用户角色分布
 * 数据源：组件内 Mock 数据（后续接入后端时替换为 API 调用）
 * 样式：与 Mosaic Admin 一致 —— 白色卡片 + 细边框 + 轻阴影 + 圆角
 */
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useEffect, useRef, useState } from 'react'
import { Activity, Percent, ShoppingCart, Users, type LucideIcon } from 'lucide-react'

/* ---------------------------------- Mock 数据 ---------------------------------- */

/** 近 7 天访问趋势（单位：次） */
const visitTrend = [
  { day: '08-12', visits: 1860 },
  { day: '08-13', visits: 2130 },
  { day: '08-14', visits: 1980 },
  { day: '08-15', visits: 2650 },
  { day: '08-16', visits: 2420 },
  { day: '08-17', visits: 2890 },
  { day: '08-18', visits: 3120 },
]

/** 用户角色分布（单位：人） */
const roleDistribution = [
  { name: '普通用户', value: 10640 },
  { name: '编辑', value: 1560 },
  { name: '管理员', value: 280 },
]

/** 饼图色板（与模板 slate/indigo 色系保持一致） */
const PIE_COLORS = ['#6366f1', '#38bdf8', '#94a3b8']

/** 统计卡片配置 */
interface StatCardConfig {
  title: string
  value: string
  /** 环比变化文案（正负号与百分比） */
  delta: string
  /** 环比是否向好（决定 delta 的绿/红配色） */
  positive: boolean
  icon: LucideIcon
  /** 图标容器背景色 */
  iconBg: string
  /** 图标颜色 */
  iconColor: string
}

const statCards: StatCardConfig[] = [
  {
    title: '总用户数',
    value: '12,480',
    delta: '+12.5%',
    positive: true,
    icon: Users,
    iconBg: 'bg-indigo-50',
    iconColor: 'text-indigo-600',
  },
  {
    title: '总订单数',
    value: '3,926',
    delta: '+8.2%',
    positive: true,
    icon: ShoppingCart,
    iconBg: 'bg-sky-50',
    iconColor: 'text-sky-600',
  },
  {
    title: '转化率',
    value: '3.42%',
    delta: '-0.3%',
    positive: false,
    icon: Percent,
    iconBg: 'bg-amber-50',
    iconColor: 'text-amber-600',
  },
  {
    title: '活跃用户',
    value: '8,153',
    delta: '+5.7%',
    positive: true,
    icon: Activity,
    iconBg: 'bg-emerald-50',
    iconColor: 'text-emerald-600',
  },
]

/* ---------------------------------- 子组件 ---------------------------------- */

/** 顶部统计卡片：白底卡片 + 左侧大数字 + 右侧圆角图标 */
function StatCard({ config }: { config: StatCardConfig }) {
  const Icon = config.icon
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-slate-500">{config.title}</p>
          <p className="mt-2 text-3xl font-bold tracking-tight text-slate-800">{config.value}</p>
          <p
            className={`mt-2 text-xs font-medium ${
              config.positive ? 'text-emerald-600' : 'text-rose-500'
            }`}
          >
            {config.delta}
            <span className="ml-1 font-normal text-slate-400">较上周期</span>
          </p>
        </div>
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg ${config.iconBg}`}>
          <Icon className={`h-5 w-5 ${config.iconColor}`} />
        </div>
      </div>
    </div>
  )
}

/** 卡片容器：统一白底 / 圆角 / 标题区 */
function ChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-base font-semibold text-slate-800">{title}</h2>
      <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
      <div className="mt-4">{children}</div>
    </div>
  )
}

/** Recharts Tooltip 统一样式（白底卡片，与模板风格一致） */
const tooltipStyle = {
  borderRadius: 8,
  border: '1px solid #e2e8f0',
  boxShadow: '0 4px 12px rgba(15, 23, 42, 0.08)',
  fontSize: 12,
  color: '#1e293b',
}

/**
 * ChartContainer —— 图表尺寸观测 + 渲染日志
 * 【Why】排查 removeChild / 「宽高为 0」告警：
 * 1. ResizeObserver 首次回调可能在布局完成前触发（StrictMode 双挂载尤甚），
 *    此时 contentRect 宽高为 0 属「布局未完成」而非真实故障 —— 用 requestAnimationFrame
 *    在下一帧主动重读兜底，布局完成后再记录真实尺寸。
 * 2. 若重读后仍为 0，warn 中附带 display / offsetParent 诊断信息，区分
 *    「父容器未显示（display:none / 折叠）」与「布局未完成」两种成因。
 */
function ChartContainer({ label, children }: { label: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [mountedAt] = useState(() => Date.now())

  useEffect(() => {
    const el = ref.current
    if (!el) return
    console.info(`[chart] ${label} 已挂载`)

    // 读取并记录容器尺寸；0 时给出诊断（display / offsetParent）并提示下一帧重试
    const readSize = () => {
      const { width, height } = el.getBoundingClientRect()
      if (width === 0 || height === 0) {
        const display = window.getComputedStyle(el).display
        console.warn(
          `[chart] ${label} 容器尺寸为 0（${Math.round(width)}x${Math.round(height)}，display=${display}，offsetParent=${el.offsetParent ? '可见' : 'null'}）` +
            (display === 'none' || el.offsetParent === null
              ? '：父容器未显示（可能被折叠/隐藏），图表暂无法渲染'
              : '：布局尚未完成，下一帧自动重读'),
        )
      } else {
        console.info(`[chart] ${label} 容器尺寸：${Math.round(width)}x${Math.round(height)}px`)
      }
    }

    const ro = new ResizeObserver(readSize)
    ro.observe(el)
    // 布局未完成时首帧尺寸可能为 0，下一帧（浏览器布局完成后）重读兜底
    const rafId = requestAnimationFrame(readSize)
    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()
      console.info(`[chart] ${label} 已卸载（存活 ${Date.now() - mountedAt}ms）`)
    }
  }, [label, mountedAt])

  return (
    <div ref={ref} className="h-72">
      {children}
    </div>
  )
}

/* ---------------------------------- 页面 ---------------------------------- */

export default function Dashboard() {
  return (
    <div className="space-y-4">
      {/* 统计卡片：1 列 → 2 列 → 4 列（小屏自动堆叠） */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {statCards.map((card) => (
          <StatCard key={card.title} config={card} />
        ))}
      </div>

      {/* 核心图表：折线图占 2 列，饼图占 1 列；小屏整列堆叠 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ChartCard title="访问趋势" subtitle="近 7 天访问量（次）">
            <ChartContainer label="访问趋势折线图">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={visitTrend} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 12, fill: '#64748b' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 12, fill: '#64748b' }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(value) => [`${value} 次`, '访问量']} />
                  <Line
                    type="monotone"
                    dataKey="visits"
                    stroke="#6366f1"
                    strokeWidth={2}
                    dot={{ r: 3, fill: '#6366f1', strokeWidth: 0 }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </ChartContainer>
          </ChartCard>
        </div>

        <ChartCard title="用户角色分布" subtitle="各角色人数占比">
          <ChartContainer label="用户角色分布饼图">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={roleDistribution}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={48}
                  outerRadius={80}
                  paddingAngle={3}
                >
                  {roleDistribution.map((entry) => (
                    <Cell key={entry.name} fill={PIE_COLORS[roleDistribution.indexOf(entry) % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} formatter={(value) => [`${value} 人`]} />
                <Legend iconType="circle" iconSize={8} formatter={(name) => <span className="text-xs text-slate-600">{name}</span>} />
              </PieChart>
            </ResponsiveContainer>
          </ChartContainer>
        </ChartCard>
      </div>
    </div>
  )
}
