/**
 * 健康仪表盘 —— 对齐老根页 templates/health_dashboard.html（React 版）
 * ================================================================
 * 老页（git 历史 f0ebe904^:templates/health_dashboard.html，1211 行）结构：
 *   实时状态条 → 健康总分 + 五层维度卡 → 7 个 Tab
 *   （概览 / 指标 / 稳定性 / 性能 / 质量 / 告警 / 历史）
 *   · 概览：overview 指标卡、健康度趋势、五层探针趋势、近期摘要
 *   · 指标：/api/diagnostics/metrics 全量 + 类型/搜索筛选
 *   · 稳定性：L1/L2/L3 探针明细表   · 质量：L4/L5 探针明细表
 *   · 性能：P50/P95/P99/吞吐 + 延迟分布（老页的 canvas 从未实现，这里补上）
 *   · 告警：/api/observability/alerts 规则表
 *   · 历史：probe-trend 历史表 + CSV 导出
 * 另参考 /legacy 全景页「系统健康 · 心跳检测」卡（templates/index.html:932-964 +
 * static/js/panorama.js:298-454）：6 个心跳指标块 + 最近 60 次 CPU/内存折线 +
 * 计划任务 / 执行历史 —— 已并入本页概览 Tab。
 *
 * 【端点契约 —— 2026-10-15 对 127.0.0.1:5678 实测，
 *   运行中服务为主仓库旧代码，仅用于确认响应形状】
 *   · GET /api/heartbeat            带 X-Envelope: v2
 *       data = {status, timestamp, checks:{llm{provider,model,status,latency_ms},
 *       memory{message,status}, scheduler{running,status,tasks},
 *       system{cpu,memory,status}, threads{status,total,alive[]}}}
 *   · GET /api/heartbeat/status     带 X-Envelope: v2
 *       data = {status, timestamp, total_checks, healthy_checks, latest}
 *   · GET /api/heartbeat/history    带 X-Envelope: v2
 *       data = {history:[{cpu,memory,llm_latency_ms,status,timestamp}], total, limit, offset}
 *   · GET /api/health/probe-trend   无信封（jsonify）
 *       = {hours, points:[{timestamp,overall(0~1),dimensions{l1..l5:0~1|null},
 *          probe_details{l1..l5:{available,score(0~1|null),detail}}}], total_records}
 *   · GET /api/diagnostics/metrics  无信封
 *       = {counters:{name:number}, histograms:{name:{count,sum,avg,min,max,p50,p95,p99}},
 *          generated_at, timestamp}   —— **实测无 gauges 键、histograms 为空**
 *   · GET /api/observability/alerts 无信封
 *       = {groups:[{name,interval,rules:[{alert,expr,for,
 *          labels{severity,category,service},annotations{summary,description,action,impact,
 *          runbook_url}}]}], timestamp}  —— **实测规则无 state / activeAt**
 *   · GET /api/scheduler/tasks      无信封 = {tasks:[], scheduler_tasks:[], scheduler_updated_at}
 *   · GET /api/scheduler/history    无信封 = {history:[{name,status,duration_ms,start_time,
 *          end_time,task_id,type,error,output}], limit, offset}
 *
 * 【纪律】后端没有返回的字段一律显示 MISSING，**不在前端重算/编造**（老页用
 * navigator.hardwareConcurrency 兜 CPU 核心数、用 probe 分冒充 error-rate，本页不沿用）。
 * 任何数据源失败都上屏（顶栏数据源故障卡 + 各分区内联错误），不用空数组静默吞掉。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  Activity, Bell, Download, History, LayoutDashboard,
  RefreshCw, Server, ShieldCheck, Sparkles, Zap,
} from 'lucide-react'
import { Badge, Card, ErrorBox, Loading, PageHeader, hubGet } from '../components/ui'
import { getEnvelope } from '@/api/envelope'
import { downloadFile } from '../../../utils/system'
import HealthVitals from './health-vitals'
import OpsDiagnostics from './ops-diagnostics'
import {
  DIAGNOSTICS_METRICS,
  HEALTH_PROBE_TREND,
  HEARTBEAT,
  HEARTBEAT_HISTORY,
  HEARTBEAT_STATUS,
  OBSERVABILITY_ALERTS,
  SCHEDULER_HISTORY,
  SCHEDULER_TASKS,
} from '@/api/endpoints'

// ═══════════════════════════════════════════════════════════════
//  响应类型（与实测形状一一对应）
// ═══════════════════════════════════════════════════════════════
export interface ProbeDetail { available?: boolean; score?: number | null; detail?: string }
export interface ProbePoint {
  timestamp?: string
  overall?: number | null
  dimensions?: Record<string, number | null>
  probe_details?: Record<string, ProbeDetail>
}
export interface ProbeTrend { hours?: number; points?: ProbePoint[]; total_records?: number }

export interface HeartbeatSystem { cpu?: number; memory?: number; status?: string }
export interface HeartbeatData {
  status?: string
  timestamp?: string
  checks?: {
    system?: HeartbeatSystem
    llm?: { status?: string; provider?: string; model?: string; latency_ms?: number }
    memory?: { status?: string; message?: string }
    scheduler?: { status?: string; running?: boolean; tasks?: number }
    threads?: { status?: string; total?: number; alive?: string[] }
  }
}
export interface HeartbeatStatus {
  status?: string; timestamp?: string; total_checks?: number; healthy_checks?: number
}
export interface HeartbeatHistoryItem {
  cpu?: number; memory?: number; llm_latency_ms?: number; status?: string; timestamp?: string
}
export interface HeartbeatHistory { history?: HeartbeatHistoryItem[]; total?: number; limit?: number; offset?: number }

export interface HistogramStats {
  count?: number; sum?: number; avg?: number; min?: number; max?: number
  p50?: number; p95?: number; p99?: number
}
export interface MetricsData {
  counters?: Record<string, number>
  gauges?: Record<string, number>
  histograms?: Record<string, HistogramStats>
  generated_at?: number
  timestamp?: number
}
export interface AlertRule {
  alert?: string
  expr?: string
  for?: string
  labels?: { severity?: string; category?: string; service?: string }
  annotations?: { summary?: string; description?: string; action?: string; impact?: string; runbook_url?: string }
  // 实测未返回：老页的「状态 / 触发时间」两列只能标注端点未返回
  state?: string
  activeAt?: string
}
export interface AlertGroup { name?: string; interval?: string; rules?: AlertRule[] }
export interface AlertsData { groups?: AlertGroup[]; timestamp?: number }

export interface SchedulerTask { name?: string; type?: string; interval_sec?: number; enabled?: boolean }
export interface SchedulerTasks { tasks?: SchedulerTask[]; scheduler_tasks?: SchedulerTask[] }
export interface SchedulerHistoryItem {
  name?: string; status?: string; duration_ms?: number
  start_time?: string; end_time?: string; error?: string; type?: string
}
export interface SchedulerHistory { history?: SchedulerHistoryItem[] }

// ═══════════════════════════════════════════════════════════════
//  常量与格式化
// ═══════════════════════════════════════════════════════════════
/** 后端未返回该字段时的统一占位（不造数、不猜、不用浏览器侧值冒充） */
export const MISSING = '—（端点未返回）'
/** 探针采集到但该层无数据（probe_details.available=false / score=null） */
export const NO_PROBE_DATA = '无数据'

export const PROBE_LAYERS = [
  { key: 'l1_process', label: 'L1 进程', icon: '🖥️', stage: 'stability', color: '#22d3ee' },
  { key: 'l2_dependency', label: 'L2 依赖', icon: '🧩', stage: 'stability', color: '#34d399' },
  { key: 'l3_llm_tool', label: 'L3 LLM/工具', icon: '🤖', stage: 'stability', color: '#fbbf24' },
  { key: 'l4_business', label: 'L4 业务', icon: '📡', stage: 'quality', color: '#f472b6' },
  { key: 'l5_semantic', label: 'L5 语义', icon: '💬', stage: 'quality', color: '#a78bfa' },
] as const
export type ProbeLayer = (typeof PROBE_LAYERS)[number]
export type ProbeLayerKey = ProbeLayer['key']

export type Level = 'excellent' | 'good' | 'fair' | 'warning' | 'critical'
export const LEVEL_TEXT: Record<Level, string> = {
  excellent: '🏆 优秀', good: '✅ 良好', fair: '⚠️ 一般', warning: '⚡ 警告', critical: '🔴 危险',
}
const LEVEL_BADGE: Record<Level, 'green' | 'amber' | 'red' | 'slate'> = {
  excellent: 'green', good: 'green', fair: 'amber', warning: 'amber', critical: 'red',
}

/** 百分制分数 → 等级（老页 getDimLevel 的同一套阈值，纯展示分级、非后端判定） */
export function levelOf(scorePercent: number): Level {
  if (scorePercent >= 90) return 'excellent'
  if (scorePercent >= 70) return 'good'
  if (scorePercent >= 50) return 'fair'
  if (scorePercent >= 30) return 'warning'
  return 'critical'
}

/** 0~1 的探针分 → 百分制字符串；null/undefined → 「无数据」 */
export function probeScoreText(score?: number | null): string {
  return score == null ? NO_PROBE_DATA : (score * 100).toFixed(0)
}

export function fmtTime(ts?: string): string {
  if (!ts) return MISSING
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString()
}

function fmtClock(ts?: string): string {
  if (!ts) return '-'
  const d = new Date(ts)
  return Number.isNaN(d.getTime())
    ? ts
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** 秒 → 毫秒字符串（diagnostics/metrics 的直方图单位是秒） */
export function secondsToMs(v?: number | null): string {
  return v == null || Number.isNaN(v) ? MISSING : (v * 1000).toFixed(0) + 'ms'
}

/** 心跳 status → 中文（与 /legacy 全景页 panorama.js 同一口径） */
export function heartbeatStatusText(status?: string): string {
  if (status === 'healthy') return '健康'
  if (status === 'degraded') return '亚健康'
  if (status === 'unhealthy' || status === 'error') return '异常'
  return status || '未知'
}
export function heartbeatStatusColor(status?: string): 'green' | 'amber' | 'red' | 'slate' {
  if (status === 'healthy') return 'green'
  if (status === 'degraded') return 'amber'
  if (status === 'unhealthy' || status === 'error') return 'red'
  return 'slate'
}

// ═══════════════════════════════════════════════════════════════
//  纯函数（导出以便单测，不依赖 React）
// ═══════════════════════════════════════════════════════════════
/** 指标表行：/api/diagnostics/metrics 的 counters / gauges / histograms 全量展开 */
export interface MetricRow { name: string; type: 'counter' | 'gauge' | 'histogram'; value: string; desc: string }

export function metricRows(metrics?: MetricsData): MetricRow[] {
  const rows: MetricRow[] = []
  for (const [name, val] of Object.entries(metrics?.counters ?? {})) {
    rows.push({ name, type: 'counter', value: String(val), desc: '累计计数' })
  }
  for (const [name, val] of Object.entries(metrics?.gauges ?? {})) {
    rows.push({ name, type: 'gauge', value: typeof val === 'number' ? val.toFixed(2) : String(val), desc: '瞬时值' })
  }
  for (const [name, stats] of Object.entries(metrics?.histograms ?? {})) {
    rows.push({
      name, type: 'histogram', value: secondsToMs(stats?.avg),
      desc: 'avg ' + secondsToMs(stats?.avg) + ' · p50 ' + secondsToMs(stats?.p50)
        + ' · p95 ' + secondsToMs(stats?.p95) + ' · p99 ' + secondsToMs(stats?.p99),
    })
  }
  return rows.sort((a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name))
}

/** 历史表 / CSV 的一行（老页 mapProbePoint + history 行的等价物） */
export interface HistoryRow {
  timestamp: string
  overall: number | null
  overallText: string
  level: Level
  levelText: string
  dims: Record<string, number | null>
}

export function historyRows(points: ProbePoint[]): HistoryRow[] {
  return points.map((p) => {
    const overallPercent = p.overall == null ? null : p.overall * 100
    const level = levelOf(overallPercent ?? 0)
    const dims: Record<string, number | null> = {}
    for (const l of PROBE_LAYERS) {
      const d = p.probe_details?.[l.key]
      const score = d?.score ?? p.dimensions?.[l.key]
      dims[l.key] = score == null ? null : Math.round(score * 1000) / 10
    }
    return {
      timestamp: p.timestamp ?? '',
      overall: overallPercent,
      overallText: overallPercent == null ? NO_PROBE_DATA : overallPercent.toFixed(1),
      level,
      levelText: LEVEL_TEXT[level],
      dims,
    }
  })
}

/** 历史 CSV（老页 exportHistory 的同一列结构；补齐表头与逗号/引号转义） */
export function buildHistoryCsv(points: ProbePoint[]): string {
  const head = ['时间', '综合得分', '等级', 'L1 进程', 'L2 依赖', 'L3 LLM/工具', 'L4 业务', 'L5 语义']
  const lines = [head.join(',')]
  for (const row of historyRows(points)) {
    const cells = [
      row.timestamp || NO_PROBE_DATA,
      row.overallText,
      row.levelText.replace(/^[^ ]+ /, ''),
      ...PROBE_LAYERS.map((l) => (row.dims[l.key] == null ? NO_PROBE_DATA : String(row.dims[l.key]))),
    ]
    lines.push(cells.map((c) => (/[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c)).join(','))
  }
  return lines.join('\n')
}

/** 折线图降采样（纯展示：点数 > cap 时按均匀步长抽稀，CSV/表格不受影响） */
export function downsample<T>(rows: T[], cap = 240): T[] {
  if (rows.length <= cap) return rows
  const step = Math.ceil(rows.length / cap)
  const out: T[] = []
  for (let i = 0; i < rows.length; i += step) out.push(rows[i])
  if (out[out.length - 1] !== rows[rows.length - 1]) out.push(rows[rows.length - 1])
  return out
}

// ═══════════════════════════════════════════════════════════════
//  数据源登记（失败必须上屏）
// ═══════════════════════════════════════════════════════════════
export type SourceKey =
  | 'heartbeat' | 'heartbeatStatus' | 'heartbeatHistory' | 'probeTrend'
  | 'metrics' | 'alerts' | 'schedulerTasks' | 'schedulerHistory'

// 展示用标签**引用端点常量**，不重复写路径字面量：
//   ① 路径只有一处真值（改后端路径时不会漏改标签）；
//   ② 静态盘点（scripts/audit/route_ui_coverage.py）里的「前端引用」来自真实调用点，
//      不会被展示文案里的路径串污染。
export const SOURCE_LABELS: Record<SourceKey, string> = {
  heartbeat: HEARTBEAT,
  heartbeatStatus: HEARTBEAT_STATUS,
  heartbeatHistory: HEARTBEAT_HISTORY,
  probeTrend: HEALTH_PROBE_TREND,
  metrics: DIAGNOSTICS_METRICS,
  alerts: OBSERVABILITY_ALERTS,
  schedulerTasks: SCHEDULER_TASKS,
  schedulerHistory: SCHEDULER_HISTORY,
}

function errText(reason: unknown): string {
  if (reason instanceof Error) return reason.message
  return String(reason)
}

// ═══════════════════════════════════════════════════════════════
//  概览指标卡（老页 #overview-metrics 的九项）
// ═══════════════════════════════════════════════════════════════
export interface OverviewCard {
  key: string
  label: string
  value: string
  sub?: string
  tone: 'info' | 'success' | 'warning' | 'error'
  /** 取不到真实字段 ⇒ true（上屏 MISSING，且标题注明来源缺失） */
  missing?: boolean
}

export function overviewCards(
  hb: HeartbeatData | undefined,
  status: HeartbeatStatus | undefined,
  metrics: MetricsData | undefined,
  latest: ProbePoint | undefined,
): OverviewCard[] {
  const sys = hb?.checks?.system
  const llm = hb?.checks?.llm
  const sched = hb?.checks?.scheduler
  const hist = metrics?.histograms?.['latency.digital_life.chat']
  const requests = metrics?.counters?.['count.chat.total']
  const l4 = latest?.probe_details?.l4_business?.score
  const l3 = latest?.probe_details?.l3_llm_tool?.score
  return [
    {
      key: 'sys-status', label: '系统状态', tone: 'info',
      value: hb ? heartbeatStatusText(hb.status) : MISSING,
      sub: hb ? fmtTime(hb.timestamp) : SOURCE_LABELS.heartbeat + ' 加载失败',
      missing: !hb,
    },
    {
      key: 'check-count', label: '检查次数', tone: 'info',
      value: status?.total_checks == null ? MISSING : status.total_checks + ' 次',
      sub: status?.healthy_checks == null ? undefined : '健康 ' + status.healthy_checks + ' 次',
      missing: status?.total_checks == null,
    },
    {
      key: 'cpu', label: 'CPU 使用率', tone: 'info',
      value: sys?.cpu == null ? MISSING : sys.cpu + '%',
      missing: sys?.cpu == null,
    },
    {
      key: 'cpu-cores', label: 'CPU 核心数', tone: 'info',
      value: MISSING, sub: HEARTBEAT + ' 未返回 cpu_cores', missing: true,
    },
    {
      key: 'memory', label: '内存使用率', tone: 'info',
      value: sys?.memory == null ? MISSING : sys.memory + '%',
      sub: hb?.checks?.memory?.message,
      missing: sys?.memory == null,
    },
    {
      key: 'mem-available', label: '内存可用量', tone: 'info',
      value: MISSING, sub: HEARTBEAT + ' 只返回使用率，无可用量字段', missing: true,
    },
    {
      key: 'disk-available', label: '磁盘可用', tone: 'info',
      value: MISSING, sub: HEARTBEAT + ' 未返回 disk；disk_free 仅出现在 L1 探针明细文本', missing: true,
    },
    {
      key: 'llm', label: 'LLM', tone: llm?.status === 'ok' ? 'success' : 'warning',
      value: llm ? (llm.status === 'ok' ? (llm.model || '已连接') : '未配置') : MISSING,
      sub: llm?.provider ? llm.provider + ' · 延迟 ' + (llm.latency_ms ?? 0) + 'ms' : undefined,
      missing: !llm,
    },
    {
      key: 'scheduler', label: '调度器', tone: sched?.running ? 'success' : 'warning',
      value: sched ? (sched.running ? '运行中' : '已停止') : MISSING,
      sub: sched?.tasks == null ? undefined : '任务 ' + sched.tasks + ' 个',
      missing: !sched,
    },
    {
      key: 'requests', label: '请求总数', tone: 'success',
      value: requests == null ? MISSING : String(requests),
      sub: requests == null ? 'counters.count.chat.total' : undefined,
      missing: requests == null,
    },
    {
      key: 'task-success', label: 'L4 业务成功率', tone: 'success',
      value: l4 == null ? NO_PROBE_DATA : probeScoreText(l4) + '%',
      sub: '来源：probe_details.l4_business.score',
      missing: l4 == null,
    },
    {
      key: 'latency', label: '平均延迟', tone: 'warning',
      value: hist?.avg == null ? MISSING : secondsToMs(hist.avg),
      sub: 'latency.digital_life.chat.avg',
      missing: hist?.avg == null,
    },
    {
      key: 'p99', label: 'P99 延迟', tone: 'warning',
      value: hist?.p99 == null ? MISSING : secondsToMs(hist.p99),
      sub: 'latency.digital_life.chat.p99',
      missing: hist?.p99 == null,
    },
    {
      key: 'error-rate', label: 'L3 LLM/工具', tone: 'error',
      value: l3 == null ? NO_PROBE_DATA : probeScoreText(l3),
      sub: '来源：probe_details.l3_llm_tool.score',
      missing: l3 == null,
    },
  ]
}

// ═══════════════════════════════════════════════════════════════
//  小组件
// ═══════════════════════════════════════════════════════════════
const TONE_CLASS: Record<OverviewCard['tone'], string> = {
  info: 'text-cyan-400', success: 'text-emerald-400', warning: 'text-amber-400', error: 'text-red-400',
}

function MetricTile({ card }: { card: OverviewCard }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4" data-testid={'ov-' + card.key}>
      <div className="text-xs text-slate-500">{card.label}</div>
      <div className={'mt-2 text-xl font-semibold ' + (card.missing ? 'text-slate-500' : TONE_CLASS[card.tone])}>
        {card.value}
      </div>
      {card.sub && <div className="mt-1 truncate text-[11px] text-slate-600" title={card.sub}>{card.sub}</div>}
    </div>
  )
}

function InlineError({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-400">
      加载失败：{text}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-8 text-center text-sm text-slate-600">{text}</div>
}

const CHART_TOOLTIP = {
  contentStyle: { background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 },
}
const AXIS = { stroke: '#64748b', fontSize: 11 }

const TABS = [
  { key: 'overview', label: '概览', Icon: LayoutDashboard },
  { key: 'metrics', label: '指标', Icon: Activity },
  { key: 'stability', label: '稳定性', Icon: ShieldCheck },
  { key: 'performance', label: '性能', Icon: Zap },
  { key: 'quality', label: '质量', Icon: Sparkles },
  { key: 'alerts', label: '告警', Icon: Bell },
  { key: 'history', label: '历史', Icon: History },
] as const
type TabKey = (typeof TABS)[number]['key']

// ═══════════════════════════════════════════════════════════════
//  主组件
// ═══════════════════════════════════════════════════════════════
export default function PanoramaHealth() {
  const [data, setData] = useState<Partial<Record<SourceKey, unknown>>>({})
  const [errors, setErrors] = useState<Partial<Record<SourceKey, string>>>({})
  const [loading, setLoading] = useState(true)
  const [apiLatencyMs, setApiLatencyMs] = useState<number | null>(null)
  const [collectedAt, setCollectedAt] = useState('')
  const [hours, setHours] = useState(1)
  const [tab, setTab] = useState<TabKey>('overview')
  const [metricSearch, setMetricSearch] = useState('')
  const [metricType, setMetricType] = useState('')
  const [historyLevel, setHistoryLevel] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    const t0 = Date.now()
    const tasks: { key: SourceKey; run: () => Promise<unknown> }[] = [
      // 心跳三件套：后端已带 X-Envelope: v2 ⇒ 显式信封解析（与 engine/heartbeat.tsx 同口径）
      { key: 'heartbeat', run: () => getEnvelope<HeartbeatData>(HEARTBEAT) },
      { key: 'heartbeatStatus', run: () => getEnvelope<HeartbeatStatus>(HEARTBEAT_STATUS) },
      { key: 'heartbeatHistory', run: () => getEnvelope<HeartbeatHistory>(HEARTBEAT_HISTORY + '?limit=60') },
      // 其余端点实测无信封 ⇒ hubGet 原始 JSON
      { key: 'probeTrend', run: () => hubGet<ProbeTrend>(HEALTH_PROBE_TREND + '?hours=' + hours) },
      { key: 'metrics', run: () => hubGet<MetricsData>(DIAGNOSTICS_METRICS) },
      { key: 'alerts', run: () => hubGet<AlertsData>(OBSERVABILITY_ALERTS) },
      { key: 'schedulerTasks', run: () => hubGet<SchedulerTasks>(SCHEDULER_TASKS) },
      { key: 'schedulerHistory', run: () => hubGet<SchedulerHistory>(SCHEDULER_HISTORY + '?limit=20') },
    ]
    const settled = await Promise.allSettled(tasks.map((t) => t.run()))
    const next: Partial<Record<SourceKey, unknown>> = {}
    const errs: Partial<Record<SourceKey, string>> = {}
    settled.forEach((r, i) => {
      const key = tasks[i].key
      if (r.status === 'fulfilled') next[key] = r.value
      else errs[key] = errText(r.reason)
    })
    setData(next)
    setErrors(errs)
    setApiLatencyMs(Date.now() - t0)
    setCollectedAt(new Date().toLocaleTimeString())
    setLoading(false)
  }, [hours])

  useEffect(() => { load() }, [load])
  // 监控场景 60s 自动刷新（保留原页行为）
  useEffect(() => {
    const timer = setInterval(() => { load() }, 60000)
    return () => clearInterval(timer)
  }, [load])

  const hb = data.heartbeat as HeartbeatData | undefined
  const hbStatus = data.heartbeatStatus as HeartbeatStatus | undefined
  const hbHistory = data.heartbeatHistory as HeartbeatHistory | undefined
  const probeTrend = data.probeTrend as ProbeTrend | undefined
  const metrics = data.metrics as MetricsData | undefined
  const alerts = data.alerts as AlertsData | undefined
  const schedulerTasks = data.schedulerTasks as SchedulerTasks | undefined
  const schedulerHistory = data.schedulerHistory as SchedulerHistory | undefined

  const points = useMemo(() => {
    const raw = Array.isArray(probeTrend?.points) ? probeTrend.points : []
    // 时间升序（老页假定最后一条最新；这里显式排序，避免后端存储顺序变化导致「最新」取错）
    return [...raw].sort((a, b) => String(a.timestamp ?? '').localeCompare(String(b.timestamp ?? '')))
  }, [probeTrend])
  const latest = points.length ? points[points.length - 1] : undefined

  const alertRules = useMemo(
    () => (alerts?.groups ?? []).flatMap((g) => (g.rules ?? []).map((r) => ({ ...r, group: g.name }))),
    [alerts],
  )
  const cards = useMemo(
    () => overviewCards(hb, hbStatus, metrics, latest),
    [hb, hbStatus, metrics, latest],
  )
  const allMetrics = useMemo(() => metricRows(metrics), [metrics])
  const filteredMetrics = useMemo(() => {
    const q = metricSearch.trim().toLowerCase()
    return allMetrics.filter((r) => {
      if (metricType && r.type !== metricType) return false
      if (!q) return true
      return (r.name + ' ' + r.desc).toLowerCase().includes(q)
    })
  }, [allMetrics, metricSearch, metricType])

  const hbChart = useMemo(
    () => [...(hbHistory?.history ?? [])].reverse().map((h) => ({
      time: fmtClock(h.timestamp), cpu: h.cpu ?? null, memory: h.memory ?? null,
      llm: h.llm_latency_ms ?? null,
    })),
    [hbHistory],
  )
  const probeChart = useMemo(
    () => downsample(points).map((p) => {
      const row: Record<string, unknown> = { time: fmtClock(p.timestamp), overall: p.overall == null ? null : p.overall * 100 }
      for (const l of PROBE_LAYERS) {
        const s = p.probe_details?.[l.key]?.score
        row[l.key] = s == null ? null : s * 100
      }
      return row
    }),
    [points],
  )
  const histName = metrics?.histograms ? Object.keys(metrics.histograms)[0] : undefined
  const hist = histName ? metrics?.histograms?.[histName] : undefined
  const latencyBars = hist
    ? [
        { name: 'avg', value: hist.avg == null ? null : Number((hist.avg * 1000).toFixed(1)) },
        { name: 'p50', value: hist.p50 == null ? null : Number((hist.p50 * 1000).toFixed(1)) },
        { name: 'p95', value: hist.p95 == null ? null : Number((hist.p95 * 1000).toFixed(1)) },
        { name: 'p99', value: hist.p99 == null ? null : Number((hist.p99 * 1000).toFixed(1)) },
      ]
    : []

  const historyAll = useMemo(() => historyRows(points), [points])
  const historyFiltered = useMemo(
    () => (historyLevel ? historyAll.filter((r) => r.level === historyLevel) : historyAll),
    [historyAll, historyLevel],
  )

  const generatedAt = metrics?.generated_at ? new Date(metrics.generated_at * 1000).toISOString() : ''
  const throughputGauge = metrics?.gauges?.['throughput.current']
  const throughputValue = throughputGauge == null ? MISSING : String(throughputGauge)
  const planTasks = (schedulerTasks?.tasks ?? []).filter((t) => t.type !== 'heartbeat')
  const execHistory = schedulerHistory?.history ?? []

  const exportCsv = () => {
    // 导出**筛选后的全量**（不受表格分页显示上限影响）
    const csv = buildHistoryCsv(historyLevel ? points.filter((p) => levelOf((p.overall ?? 0) * 100) === historyLevel) : points)
    downloadFile('health-history-' + new Date().toISOString().slice(0, 10) + '.csv', csv, 'text/csv;charset=utf-8')
  }

  const failed = Object.keys(errors) as SourceKey[]
  const stabLayers = PROBE_LAYERS.filter((l) => l.stage === 'stability')
  const qualLayers = PROBE_LAYERS.filter((l) => l.stage === 'quality')

  const renderProbeTable = (layers: readonly ProbeLayer[], testid: string) => {
    if (errors.probeTrend) return <InlineError text={errors.probeTrend} />
    if (!latest) return <Empty text="暂无探针数据（/api/health/probe-trend 未返回数据点）" />
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid={testid}>
          <thead>
            <tr className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
              <th className="px-3 py-2 font-medium">探针层</th>
              <th className="px-3 py-2 font-medium">得分</th>
              <th className="px-3 py-2 font-medium">状态</th>
              <th className="px-3 py-2 font-medium">采集明细</th>
            </tr>
          </thead>
          <tbody>
            {layers.map((l) => {
              const d = latest.probe_details?.[l.key]
              const score = d?.score ?? latest.dimensions?.[l.key]
              const available = d?.available
              return (
                <tr key={l.key} className="border-b border-slate-800/50">
                  <td className="px-3 py-2.5 text-slate-300">{l.icon} {l.label}</td>
                  <td className="px-3 py-2.5 font-mono text-slate-200">
                    {score == null ? NO_PROBE_DATA : probeScoreText(score)}
                  </td>
                  <td className="px-3 py-2.5">
                    <Badge color={available ? 'green' : 'amber'}>{available ? '正常采集' : '无数据'}</Badge>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-slate-400">{d?.detail || '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  const overallPercent = latest?.overall == null ? null : latest.overall * 100
  const overallLevel = levelOf(overallPercent ?? 0)

  return (
    <div className="p-6">
      <PageHeader
        title="健康仪表盘"
        description="综合健康度 · 五层探针 · 心跳资源 · 指标 / 性能 / 告警 / 历史（对齐根页 health_dashboard.html）"
        actions={
          <button
            onClick={load}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            <RefreshCw size={12} /> 刷新
          </button>
        }
      />

      {/* ── 顶部系统状态条（老页 .realtime-bar）── */}
      <div
        data-testid="health-realtime-bar"
        className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-2.5 text-xs text-slate-400"
      >
        <div className="flex flex-wrap items-center gap-5">
          <span className="flex items-center gap-2">
            <span className={'inline-block h-2 w-2 rounded-full ' + (hb?.status === 'healthy' ? 'animate-pulse bg-emerald-400' : hb?.status === 'degraded' ? 'bg-amber-400' : hb ? 'bg-red-400' : 'bg-slate-600')} />
            {errors.heartbeat ? '心跳不可用：' + errors.heartbeat : hb ? heartbeatStatusText(hb.status) + ' · 数据实时更新中' : '等待心跳数据'}
          </span>
          <span>上次更新: <span data-testid="health-last-update" className="text-slate-300">{fmtTime(hb?.timestamp)}</span></span>
          <span>采集时间: <span className="text-slate-300">{collectedAt || '-'}</span></span>
        </div>
        <div className="flex flex-wrap items-center gap-5">
          <span>API 响应: <span data-testid="health-api-latency" className="text-slate-300">{apiLatencyMs == null ? '-' : apiLatencyMs + 'ms'}</span></span>
          <span>历史数据: <span data-testid="health-history-count" className="text-slate-300">{hbStatus?.total_checks == null ? MISSING : hbStatus.total_checks}</span> 条</span>
        </div>
      </div>

      {/* 数据源故障必须上屏 */}
      {failed.length > 0 && (
        <div className="mb-4 space-y-1" data-testid="health-source-errors">
          {failed.map((k) => (
            <ErrorBox key={k} message={SOURCE_LABELS[k] + '：' + (errors[k] ?? '')} />
          ))}
        </div>
      )}

      {loading && points.length === 0 && Object.keys(data).length === 0 ? <Loading /> : (
        <>
          {/* ── 健康总分 + 五层维度卡（老页 .health-overview）── */}
          <div className="mb-4 grid gap-4 lg:grid-cols-[200px_1fr]">
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 text-center">
              <div className={'text-5xl font-bold ' + (!latest ? 'text-slate-600' : overallLevel === 'excellent' || overallLevel === 'good' ? 'text-emerald-400' : overallLevel === 'fair' || overallLevel === 'warning' ? 'text-amber-400' : 'text-red-400')}>
                {overallPercent == null ? '--' : overallPercent.toFixed(0)}
              </div>
              <div className="mt-2">
                <Badge color={latest ? LEVEL_BADGE[overallLevel] : 'slate'}>
                  {latest ? LEVEL_TEXT[overallLevel] : '无数据'}
                </Badge>
              </div>
              <div className="mt-3 text-[11px] text-slate-500" data-testid="health-assess-time">
                评估时间: {fmtTime(latest?.timestamp)}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" data-testid="health-dimensions">
              {PROBE_LAYERS.map((l) => {
                const d = latest?.probe_details?.[l.key]
                const score = d?.score == null ? null : d.score * 100
                const lv = score == null ? null : levelOf(score)
                return (
                  <div key={l.key} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-center" data-testid={'dim-' + l.key}>
                    <div className="text-xl">{l.icon}</div>
                    <div className="mt-1 text-xs text-slate-500">{l.label}</div>
                    <div className={'mt-1 text-2xl font-semibold ' + (lv === 'excellent' || lv === 'good' ? 'text-emerald-400' : lv === 'fair' || lv === 'warning' ? 'text-amber-400' : lv === 'critical' ? 'text-red-400' : 'text-slate-600')}>
                      {score == null ? NO_PROBE_DATA : score.toFixed(0)}
                    </div>
                    <div className="mt-2 h-1 overflow-hidden rounded bg-slate-800">
                      <div className="h-full rounded" style={{ width: (score ?? 0) + '%', background: l.color }} />
                    </div>
                    {d?.detail && <div className="mt-2 truncate text-[10px] text-slate-600" title={d.detail}>{d.detail}</div>}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ── Tab 导航（老页 7 个 Tab）── */}
          <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-800 pb-3" data-testid="health-tabs">
            {TABS.map(({ key, label, Icon }) => (
              <button
                key={key}
                data-testid={'tab-' + key}
                onClick={() => setTab(key)}
                className={'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs ' + (tab === key ? 'border-cyan-700 bg-slate-900 text-cyan-400' : 'border-transparent text-slate-400 hover:bg-slate-900')}
              >
                <Icon size={12} /> {label}
              </button>
            ))}
          </div>

          {/* ══════════ 概览 ══════════ */}
          {tab === 'overview' && (
            <div data-testid="panel-overview">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
                {cards.map((c) => <MetricTile key={c.key} card={c} />)}
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Card title="综合健康度趋势（按所选区间）"
                  actions={
                    <select
                      data-testid="health-range"
                      value={hours}
                      onChange={(e) => setHours(Number(e.target.value))}
                      className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300"
                    >
                      <option value={1}>最近1小时</option>
                      <option value={6}>最近6小时</option>
                      <option value={24}>最近24小时</option>
                      <option value={168}>最近7天</option>
                    </select>
                  }
                >
                  {errors.probeTrend ? <InlineError text={errors.probeTrend} /> : probeChart.length === 0 ? (
                    <Empty text="暂无探针趋势数据" />
                  ) : (
                    <div style={{ width: '100%', height: 250 }}>
                      <ResponsiveContainer>
                        <AreaChart data={probeChart}>
                          <defs>
                            <linearGradient id="overallGrad" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.4} />
                              <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="time" {...AXIS} />
                          <YAxis domain={[0, 100]} {...AXIS} />
                          <Tooltip {...CHART_TOOLTIP} />
                          <Area type="monotone" dataKey="overall" name="综合健康" stroke="#22d3ee" fill="url(#overallGrad)" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  <div className="mt-1 text-[11px] text-slate-600">
                    共 {points.length} 个数据点；图表点数超过 240 时按均匀步长抽稀（导出与表格不受影响）。
                  </div>
                </Card>

                <Card title="健康评分 + 五层探针（L1~L5）变化">
                  {errors.probeTrend ? <InlineError text={errors.probeTrend} /> : probeChart.length === 0 ? (
                    <Empty text="暂无探针数据" />
                  ) : (
                    <div style={{ width: '100%', height: 250 }}>
                      <ResponsiveContainer>
                        <LineChart data={probeChart}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="time" {...AXIS} />
                          <YAxis domain={[0, 100]} {...AXIS} />
                          <Tooltip {...CHART_TOOLTIP} />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          {PROBE_LAYERS.map((l) => (
                            <Line key={l.key} type="monotone" dataKey={l.key} name={l.label} stroke={l.color} dot={false} connectNulls strokeWidth={1.5} />
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Card>
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                {/* 保留原有「五层探针明细」卡 */}
                <Card title="五层探针明细">
                  {!latest ? <Empty text="暂无探针数据" /> : (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                        <span className="text-sm text-slate-300">综合健康度</span>
                        <span className={'font-mono text-lg font-semibold ' + ((latest.overall ?? 0) >= 0.8 ? 'text-emerald-400' : (latest.overall ?? 0) >= 0.5 ? 'text-amber-400' : 'text-red-400')}>
                          {latest.overall != null ? (latest.overall * 100).toFixed(0) + '%' : NO_PROBE_DATA}
                        </span>
                      </div>
                      {PROBE_LAYERS.map((l) => {
                        const pd = latest.probe_details?.[l.key]
                        const score = pd?.score ?? latest.dimensions?.[l.key]
                        return (
                          <div key={l.key} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2 text-sm text-slate-300">
                                <span className="h-2 w-2 rounded-full" style={{ background: l.color }} />
                                {l.label}
                                {pd && <Badge color={pd.available ? 'green' : 'slate'}>{pd.available ? '可用' : '不可用'}</Badge>}
                              </div>
                              {pd?.detail && <div className="mt-0.5 truncate text-xs text-slate-500" title={pd.detail}>{pd.detail}</div>}
                            </div>
                            <span className="shrink-0 font-mono text-sm text-slate-300">
                              {score == null ? NO_PROBE_DATA : probeScoreText(score) + '%'}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </Card>

                {/* 保留原有「运行告警」卡（形状修正：真实是 groups[].rules[]，不是 groups[].alerts[]） */}
                <Card title={'运行告警（' + alertRules.length + '）'}>
                  {errors.alerts ? <InlineError text={errors.alerts} /> : alertRules.length === 0 ? (
                    <div className="flex flex-col items-center gap-2 py-8 text-slate-600">
                      <Server size={22} />
                      <span className="text-sm">当前无告警规则</span>
                    </div>
                  ) : (
                    <div className="max-h-[380px] space-y-2 overflow-y-auto">
                      {alertRules.map((a, i) => {
                        const severity = a.labels?.severity ?? 'info'
                        return (
                          <div key={(a.group ?? '') + (a.alert ?? '') + i} className="flex items-start justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
                            <div className="min-w-0">
                              <div className="text-sm text-slate-200">{a.alert ?? '(未命名规则)'}</div>
                              <div className="mt-0.5 truncate text-xs text-slate-500" title={a.annotations?.summary ?? a.expr ?? ''}>
                                {a.annotations?.summary ?? a.expr ?? ''}
                              </div>
                            </div>
                            <Badge color={severity === 'critical' ? 'red' : severity === 'warning' ? 'amber' : 'cyan'}>{severity}</Badge>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </Card>
              </div>

              {/* 近期摘要（老页 #summary-table） */}
              <div className="mt-4">
                <Card title="近期摘要（最近一次评估的五层探针明细）">
                  {!latest ? <Empty text="暂无探针数据" /> : (
                    <table className="w-full text-left text-sm" data-testid="health-summary-table">
                      <thead>
                        <tr className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500">
                          <th className="px-3 py-2 font-medium">类型</th>
                          <th className="px-3 py-2 font-medium">消息</th>
                          <th className="px-3 py-2 font-medium">时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {PROBE_LAYERS.map((l) => {
                          const d = latest.probe_details?.[l.key]
                          return (
                            <tr key={l.key} className="border-b border-slate-800/50">
                              <td className="px-3 py-2 text-slate-300">🔬 {l.label}</td>
                              <td className="px-3 py-2 text-slate-400">
                                {d?.score == null ? NO_PROBE_DATA : probeScoreText(d.score) + '分'} — {d?.detail || ''}
                              </td>
                              <td className="px-3 py-2 text-xs text-slate-500">{fmtTime(latest.timestamp)}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </Card>
              </div>

              {/* 心跳资源（/legacy 全景页「系统健康 · 心跳检测」卡） */}
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Card title="资源趋势（最近 60 次心跳 · CPU / 内存）">
                  {errors.heartbeatHistory ? <InlineError text={errors.heartbeatHistory} /> : hbChart.length < 2 ? (
                    <Empty text={'心跳历史不足 2 条（当前 ' + hbChart.length + ' 条）'} />
                  ) : (
                    <div style={{ width: '100%', height: 180 }}>
                      <ResponsiveContainer>
                        <LineChart data={hbChart}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="time" {...AXIS} />
                          <YAxis domain={[0, 100]} {...AXIS} />
                          <Tooltip {...CHART_TOOLTIP} />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          <Line type="monotone" dataKey="cpu" name="CPU %" stroke="#58a6ff" dot={false} strokeWidth={1.5} />
                          <Line type="monotone" dataKey="memory" name="内存 %" stroke="#bc8cff" dot={false} strokeWidth={1.5} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Card>

                <Card title={'计划任务（' + planTasks.length + '）'}>
                  {errors.schedulerTasks ? <InlineError text={errors.schedulerTasks} /> : planTasks.length === 0 ? (
                    <Empty text="暂无计划任务（/api/scheduler/tasks 返回空列表）" />
                  ) : (
                    <div className="space-y-1">
                      {planTasks.map((t, i) => (
                        <div key={(t.name ?? '') + i} className="flex items-center justify-between border-b border-slate-800/50 px-1 py-1.5 text-xs">
                          <span className="text-slate-300">{t.name || '(未命名)'}</span>
                          <span className="text-slate-500">{t.interval_sec ? t.interval_sec + 's' : ''}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </div>

              <div className="mt-4">
                <Card title={'执行历史（最近 ' + execHistory.length + ' 条）'}>
                  {errors.schedulerHistory ? <InlineError text={errors.schedulerHistory} /> : execHistory.length === 0 ? (
                    <Empty text="暂无执行记录" />
                  ) : (
                    <div className="max-h-72 space-y-1 overflow-y-auto">
                      {execHistory.map((h, i) => (
                        <div key={(h.start_time ?? '') + i} className="flex items-center justify-between border-b border-slate-800/50 px-1 py-1.5 text-xs">
                          <span className="text-slate-300">{h.name || '(未命名)'}</span>
                          <span className="flex items-center gap-3">
                            <span className="text-slate-500">{fmtTime(h.start_time)}</span>
                            <span className="text-slate-500">{h.duration_ms == null ? '-' : h.duration_ms + 'ms'}</span>
                            <Badge color={h.status === 'success' ? 'green' : h.status === 'failed' ? 'red' : 'slate'}>{h.status || '?'}</Badge>
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </Card>
              </div>
            </div>
          )}

          {/* ══════════ 指标 ══════════ */}
          {tab === 'metrics' && (
            <div data-testid="panel-metrics">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <input
                  data-testid="metric-search"
                  value={metricSearch}
                  onChange={(e) => setMetricSearch(e.target.value)}
                  placeholder="搜索指标名称..."
                  className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600"
                />
                <select
                  data-testid="metric-type-filter"
                  value={metricType}
                  onChange={(e) => setMetricType(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300"
                >
                  <option value="">所有类型</option>
                  <option value="counter">计数器</option>
                  <option value="gauge">仪表</option>
                  <option value="histogram">直方图</option>
                </select>
                <span className="text-xs text-slate-500">共 {allMetrics.length} 条，筛选后 {filteredMetrics.length} 条</span>
              </div>
              {errors.metrics ? <InlineError text={errors.metrics} /> : allMetrics.length === 0 ? (
                <Empty text="暂无指标数据（/api/diagnostics/metrics 返回的 counters/gauges/histograms 均为空）" />
              ) : (
                <div className="overflow-x-auto rounded-xl border border-slate-800">
                  <table className="w-full text-left text-sm" data-testid="metrics-table">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-900/80 text-xs uppercase tracking-wider text-slate-500">
                        <th className="px-3 py-2 font-medium">指标名称</th>
                        <th className="px-3 py-2 font-medium">类型</th>
                        <th className="px-3 py-2 font-medium">当前值</th>
                        <th className="px-3 py-2 font-medium">描述</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredMetrics.map((r) => (
                        <tr key={r.type + r.name} className="border-b border-slate-800/50">
                          <td className="px-3 py-2 font-mono text-xs text-slate-300">{r.name}</td>
                          <td className="px-3 py-2">
                            <Badge color={r.type === 'counter' ? 'cyan' : r.type === 'gauge' ? 'green' : 'amber'}>{r.type}</Badge>
                          </td>
                          <td className="px-3 py-2 font-mono text-xs text-slate-200">{r.value}</td>
                          <td className="px-3 py-2 text-xs text-slate-500">{r.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {filteredMetrics.length === 0 && <Empty text="没有匹配的指标" />}
                </div>
              )}
              <div className="mt-2 text-[11px] text-slate-600">
                gauges 键实测未返回（新老后端均只给 counters/histograms）；缺失不补零，如实呈现端点现状。
                {generatedAt ? ' 生成时间: ' + fmtTime(generatedAt) : ''}
              </div>
            </div>
          )}

          {/* ══════════ 稳定性 ══════════ */}
          {tab === 'stability' && (
            <div data-testid="panel-stability">
              <Card title="运行稳定性探针（L1 进程 / L2 依赖 / L3 LLM·工具）">
                {renderProbeTable(stabLayers, 'stab-probe-table')}
              </Card>
            </div>
          )}

          {/* ══════════ 性能 ══════════ */}
          {tab === 'performance' && (
            <div data-testid="panel-performance">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {[
                  { label: 'P50 延迟', value: secondsToMs(hist?.p50), tone: 'text-cyan-400' },
                  { label: 'P95 延迟', value: secondsToMs(hist?.p95), tone: 'text-amber-400' },
                  { label: 'P99 延迟', value: secondsToMs(hist?.p99), tone: 'text-red-400' },
                  {
                    label: '吞吐量',
                    value: throughputValue,
                    tone: 'text-emerald-400',
                  },
                ].map((c) => (
                  <div key={c.label} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                    <div className="text-xs text-slate-500">{c.label}</div>
                    <div className={'mt-2 text-xl font-semibold ' + (c.value === MISSING ? 'text-slate-500' : c.tone)}>{c.value}</div>
                  </div>
                ))}
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <Card title={'延迟分布' + (histName ? '（' + histName + '）' : '')}>
                  {errors.metrics ? <InlineError text={errors.metrics} /> : !hist ? (
                    <Empty text={'延迟分布不可用：' + MISSING + '（/api/diagnostics/metrics 的 histograms 为空）'} />
                  ) : (
                    <div style={{ width: '100%', height: 220 }}>
                      <ResponsiveContainer>
                        <BarChart data={latencyBars}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="name" {...AXIS} />
                          <YAxis {...AXIS} />
                          <Tooltip {...CHART_TOOLTIP} />
                          <Bar dataKey="value" name="延迟(ms)" fill="#22d3ee" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Card>

                <Card title="LLM 延迟趋势（最近 60 次心跳 · llm_latency_ms）">
                  {errors.heartbeatHistory ? <InlineError text={errors.heartbeatHistory} /> : hbChart.length < 2 ? (
                    <Empty text="心跳历史不足 2 条" />
                  ) : (
                    <div style={{ width: '100%', height: 220 }}>
                      <ResponsiveContainer>
                        <LineChart data={hbChart}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                          <XAxis dataKey="time" {...AXIS} />
                          <YAxis {...AXIS} />
                          <Tooltip {...CHART_TOOLTIP} />
                          <Line type="monotone" dataKey="llm" name="LLM 延迟 ms" stroke="#fbbf24" dot={false} strokeWidth={1.5} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </Card>
              </div>
            </div>
          )}

          {/* ══════════ 质量 ══════════ */}
          {tab === 'quality' && (
            <div data-testid="panel-quality">
              <Card title="输出质量探针（L4 业务 / L5 语义）">
                {renderProbeTable(qualLayers, 'qual-probe-table')}
              </Card>
            </div>
          )}

          {/* ══════════ 告警 ══════════ */}
          {tab === 'alerts' && (
            <div data-testid="panel-alerts">
              {errors.alerts ? <InlineError text={errors.alerts} /> : alertRules.length === 0 ? (
                <Empty text="暂无告警规则" />
              ) : (
                <div className="overflow-x-auto rounded-xl border border-slate-800">
                  <table className="w-full text-left text-sm" data-testid="alerts-table">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-900/80 text-xs uppercase tracking-wider text-slate-500">
                        <th className="px-3 py-2 font-medium">级别</th>
                        <th className="px-3 py-2 font-medium">规则名称</th>
                        <th className="px-3 py-2 font-medium">描述</th>
                        <th className="px-3 py-2 font-medium">表达式</th>
                        <th className="px-3 py-2 font-medium">状态</th>
                        <th className="px-3 py-2 font-medium">触发时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {alertRules.map((r, i) => {
                        const severity = r.labels?.severity ?? 'info'
                        return (
                          <tr key={(r.group ?? '') + (r.alert ?? '') + i} className="border-b border-slate-800/50">
                            <td className="px-3 py-2">
                              <Badge color={severity === 'critical' ? 'red' : severity === 'warning' ? 'amber' : 'cyan'}>{severity}</Badge>
                            </td>
                            <td className="px-3 py-2 text-slate-200">{r.alert ?? '-'}</td>
                            <td className="px-3 py-2 text-xs text-slate-400">
                              {r.annotations?.summary || r.annotations?.description || '-'}
                              {r.annotations?.impact && <div className="text-[11px] text-slate-600">影响：{r.annotations.impact}</div>}
                            </td>
                            <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{r.expr || '-'}</td>
                            {/* 实测规则无 state / activeAt：如实标注，不把「有规则」当成「已触发」 */}
                            <td className="px-3 py-2 text-xs text-slate-500">{r.state ? (r.state === 'firing' ? '触发' : r.state) : MISSING}</td>
                            <td className="px-3 py-2 text-xs text-slate-500">{r.activeAt ? fmtTime(r.activeAt) : MISSING}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="mt-2 text-[11px] text-slate-600">
                共 {alerts?.groups?.length ?? 0} 个告警组 / {alertRules.length} 条规则；状态与触发时间字段端点未返回。
              </div>
            </div>
          )}

          {/* ══════════ 历史 ══════════ */}
          {tab === 'history' && (
            <div data-testid="panel-history">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <select
                  data-testid="history-level-filter"
                  value={historyLevel}
                  onChange={(e) => setHistoryLevel(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300"
                >
                  <option value="">所有等级</option>
                  <option value="excellent">优秀</option>
                  <option value="good">良好</option>
                  <option value="fair">一般</option>
                  <option value="warning">警告</option>
                  <option value="critical">危险</option>
                </select>
                <button
                  data-testid="history-export"
                  onClick={exportCsv}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                >
                  <Download size={12} /> 导出CSV
                </button>
                <span className="text-xs text-slate-500">
                  共 {historyFiltered.length} 条（区间 {hours}h）{historyFiltered.length > 200 ? '，表格仅显示最近 200 条' : ''}
                </span>
              </div>
              {errors.probeTrend ? <InlineError text={errors.probeTrend} /> : historyFiltered.length === 0 ? (
                <Empty text="暂无历史数据" />
              ) : (
                <div className="overflow-x-auto rounded-xl border border-slate-800">
                  <table className="w-full text-left text-sm" data-testid="history-table">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-900/80 text-xs uppercase tracking-wider text-slate-500">
                        <th className="px-3 py-2 font-medium">时间</th>
                        <th className="px-3 py-2 font-medium">综合得分</th>
                        <th className="px-3 py-2 font-medium">等级</th>
                        <th className="px-3 py-2 font-medium">L1 进程</th>
                        <th className="px-3 py-2 font-medium">L2 依赖</th>
                        <th className="px-3 py-2 font-medium">L3 LLM/工具</th>
                        <th className="px-3 py-2 font-medium">L4 业务</th>
                        <th className="px-3 py-2 font-medium">L5 语义</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historyFiltered.slice(-200).reverse().map((r, i) => (
                        <tr key={r.timestamp + i} className="border-b border-slate-800/50">
                          <td className="px-3 py-2 text-xs text-slate-400">{fmtTime(r.timestamp)}</td>
                          <td className="px-3 py-2 font-mono text-slate-200">{r.overallText}</td>
                          <td className="px-3 py-2"><Badge color={LEVEL_BADGE[r.level]}>{r.levelText}</Badge></td>
                          {PROBE_LAYERS.map((l) => (
                            <td key={l.key} className="px-3 py-2 font-mono text-xs text-slate-300">
                              {r.dims[l.key] == null ? NO_PROBE_DATA : r.dims[l.key]}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
      <HealthVitals />
      <OpsDiagnostics />
    </div>
  )
}
