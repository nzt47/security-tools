/**
 * 健康仪表盘 —— 对齐老根页 templates/health_dashboard.html（React 版）
 * ================================================================
 * 老页（git 历史 f0ebe904^:templates/health_dashboard.html，1211 行）结构：
 *   实时状态条 → 健康总分 + 五层维度卡 → 7 个 Tab
 *   （概览 / 指标 / 稳定性 / 性能 / 质量 / 告警 / 历史）
 *   · 概览：overview 指标卡、健康度趋势、五层探针趋势、近期摘要、心跳资源、计划任务
 *     （五层探针明细 / 运行告警**不**在概览：前者在稳定性·质量 Tab、后者在告警 Tab）
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
import ModeCard from './ModeCard'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  Activity, Bell, Boxes, Download, History, LayoutDashboard,
  RefreshCw, ShieldCheck, Sparkles, UserCog, Zap,
} from 'lucide-react'
import { getDashboardSummary, validateDashboardSummary, type DashboardSummaryData } from '@/api/dashboard'
import { Badge, Card, ErrorBox, Loading, PageHeader, hubGet } from '../components/ui'
import { getEnvelope } from '@/api/envelope'
import { downloadFile } from '../../../utils/system'
import HealthVitals from './health-vitals'
import UnitStatus from './unit-status'
import OpsDiagnostics from './ops-diagnostics'
import {
  DIAGNOSTICS_METRICS,
  HEALTH_PROBE_TREND,
  HEARTBEAT,
  HEARTBEAT_HISTORY,
  HEARTBEAT_STATUS,
  OBSERVABILITY_ALERTS,
  SCHEDULER_HISTORY,
  SCHEDULER_OVERVIEW,
} from '@/api/endpoints'

// ═══════════════════════════════════════════════════════════════
//  响应类型（与实测形状一一对应）
// ═══════════════════════════════════════════════════════════════
export interface ProbeDetail {
  available?: boolean
  score?: number | null
  detail?: string
  /** 结构化采集数值（L1 有：cpu_pct / cpu_cores / mem_available_mb / disk_free_gb …；L5 有：likes / dislikes / suggestions / stale） */
  metrics?: Record<string, number>
}
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
  /** 该规则本轮是否真的求出了值（false = 无数据 / 样本不足 / 需 Prometheus） */
  evaluable?: boolean
  /** 不可求值时的原因（如"无序列: up"） */
  state_reason?: string
  value?: number
  fire_count?: number
}
export interface AlertGroup { name?: string; interval?: string; rules?: AlertRule[] }
export interface AlertsData {
  groups?: AlertGroup[]
  timestamp?: number
  /** 是否拿到了运行态（false = 评估器没跑过，状态列只能是占位） */
  runtime_state?: boolean
}

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

/**
 * 五层探针的**定义 + 数据来源标注**（2026-11-08 Operator 要求：每层都要说清数据从哪来）。
 *
 * `origin` 是给人看的一句话（卡片上的小字），`originDetail` 是悬浮/详情里的完整口径。
 * 两处都只描述**后端探针实际读了什么**（`agent/health/probes.py`），不写"应该是什么"——
 * 口径与实现漂移时，这里必须跟着改，否则卡片会撒谎。
 */
export const PROBE_LAYERS = [
  {
    key: 'l1_process', label: 'L1 进程', icon: '🖥️', stage: 'stability', color: '#22d3ee',
    origin: 'psutil 本机采样',
    originDetail: '本机 psutil：virtual_memory / cpu_percent / Process().threads() / disk_usage；'
      + '健康采集器每 60s 跑一次并写进 /api/health/probe-trend',
  },
  {
    key: 'l2_dependency', label: 'L2 依赖', icon: '🧩', stage: 'stability', color: '#34d399',
    origin: 'configs 核心文件可读性',
    originDetail: '逐个打开 configs/app.yaml、configs/paths.yaml 判断依赖是否可用（可读 N/N）',
  },
  {
    key: 'l3_llm_tool', label: 'L3 LLM/工具', icon: '🤖', stage: 'stability', color: '#fbbf24',
    origin: 'LLM/工具调用错误率 + 熔断器',
    originDetail: 'agent.monitoring.metrics 里 LLM/工具链路的调用计数与错误率；'
      + '没有计数时回落到 circuit_breaker 状态（全关=1.0，有 open=0.1）',
  },
  {
    key: 'l4_business', label: 'L4 业务', icon: '📡', stage: 'quality', color: '#f472b6',
    origin: '业务接口成功率',
    originDetail: 'agent.monitoring.metrics 的 request / error 计数器（count.* → 成功率）；'
      + '最近窗口没有请求就报「无数据」，不假装 100%',
  },
  {
    key: 'l5_semantic', label: 'L5 语义', icon: '💬', stage: 'quality', color: '#a78bfa',
    origin: '反馈库近 7 天 like/dislike',
    originDetail: 'feedback.db 近 7 天的 like / dislike 反馈：满意度 = like ÷ (like + dislike)；'
      + '「建议 / 开发随记」不是满意度信号、不计入分母；样本 < 5 条不评分（显示「无数据」）',
  },
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

/**
 * 告警运行态的中文标签（2026-11-11）。
 * 由报警器本进程规则引擎给出：能算的给 firing/pending/ok，算不了的给 no_data/warming/unsupported。
 * **不给"正常"以外的降级**：无数据就是"无数据"，不是"未触发"。
 */
export const ALERT_STATE_LABEL: Record<string, string> = {
  firing: '触发中',
  pending: '待确认（未满 for）',
  ok: '正常',
  resolved: '已恢复',
  no_data: '无数据（指标不存在）',
  warming: '样本不足（刚启动）',
  unsupported: '需 Prometheus',
  error: '求值异常',
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

/**
 * 维度卡上的「本层实测口径」一行（L3 错误率 / L4 成功率）。
 *
 * 【2026-11-11 合并】概览区原先另有两张指标卡（key=error-rate / task-success），
 * 值与维度卡里的 l3_llm_tool / l4_business **是同一个分数**、标签还逐字相同 ⇒ 同一屏
 * 看两次「L3 LLM/工具」。合并进维度卡后：
 *   · 分数仍只显示一次（维度卡的大数字）；
 *   · 「错误率 / 成功率」这类**原始比率**由后端 probe_details.metrics 提供，前端只做
 *     单位换算，不拆 detail 文本、不重算口径（L5：界面不重算）。
 * 取不到 metrics（老快照 / 探针无数据）⇒ 返回空串，那一行不渲染 —— 不编数字。
 */
export function layerMetricLine(key: string, metrics?: Record<string, number>): string {
  if (!metrics) return ''
  if (key === 'l3_llm_tool' && metrics.error_rate != null) {
    return '错误率 ' + (metrics.error_rate * 100).toFixed(1) + '%'
  }
  if (key === 'l4_business' && metrics.success_rate != null) {
    return '成功率 ' + (metrics.success_rate * 100).toFixed(1) + '%'
  }
  return ''
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
  schedulerTasks: SCHEDULER_OVERVIEW,
  schedulerHistory: SCHEDULER_HISTORY,
}

function errText(reason: unknown): string {
  if (reason instanceof Error) return reason.message
  return String(reason)
}

// ═══════════════════════════════════════════════════════════════
//  概览指标卡（老页 #overview-metrics 的九项；2026-11-11 合并掉两张与维度卡重复的
//  「L4 业务成功率 / L3 LLM/工具」⇒ 现七项，口径见 layerMetricLine）
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
  // 【2026-11-11】「L4 业务成功率」「L3 LLM/工具」两张指标卡**删除**：它们的值与
  //   五层维度卡里的 l4_business / l3_llm_tool 是**同一个分数**（同一屏看两次，
  //   标签还逐字相同 ⇒ 用户无法区分）。合并方式：指标搬到维度卡上（见 layerMetricLine），
  //   数值改由后端 probe_details.metrics 给（probes.py 的 l3/l4），前端不拆 detail 文本。
  // 【2026-11-08】L1 探针现在给结构化数值（cpu_cores / mem_available_mb / disk_free_gb）。
  // 以前这三张卡是**硬编码 MISSING**（心跳端点只给使用率百分比）⇒ 页面上一片「—（端点未返回）」。
  const l1m = latest?.probe_details?.l1_process?.metrics
  // 【2026-11-08】L3 探针现在也带结构化数值：前缀缓存命中率（口径 = 只认 API 上报过
  // 缓存字段的调用）。命中率掉了不该靠人翻记录发现 ⇒ 概览卡直接上屏。
  const l3m = latest?.probe_details?.l3_llm_tool?.metrics
  const L1_SRC = '来源：/api/health/probe-trend → probe_details.l1_process.metrics（L1 探针）'
  const CACHE_SRC = '来源：/api/health/probe-trend → probe_details.l3_llm_tool.metrics（L3 探针）'
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
      value: l1m?.cpu_cores == null ? MISSING : String(l1m.cpu_cores) + ' 核',
      sub: l1m?.cpu_cores == null ? 'L1 探针未返回 cpu_cores' : L1_SRC,
      missing: l1m?.cpu_cores == null,
    },
    {
      key: 'memory', label: '内存使用率', tone: 'info',
      value: sys?.memory == null ? MISSING : sys.memory + '%',
      sub: hb?.checks?.memory?.message,
      missing: sys?.memory == null,
    },
    {
      key: 'mem-available', label: '内存可用量', tone: 'info',
      value: l1m?.mem_available_mb == null ? MISSING : (l1m.mem_available_mb / 1024).toFixed(1) + ' GB',
      sub: l1m?.mem_available_mb == null ? HEARTBEAT + ' 只返回使用率；L1 探针也没给可用量' : L1_SRC,
      missing: l1m?.mem_available_mb == null,
    },
    {
      key: 'disk-available', label: '磁盘可用', tone: 'info',
      value: l1m?.disk_free_gb == null ? MISSING : l1m.disk_free_gb.toFixed(1) + ' GB',
      sub: l1m?.disk_free_gb == null ? HEARTBEAT + ' 未返回 disk；L1 探针也没给' : L1_SRC,
      missing: l1m?.disk_free_gb == null,
    },
    {
      key: 'llm', label: 'LLM', tone: llm?.status === 'ok' ? 'success' : 'warning',
      value: llm ? (llm.status === 'ok' ? (llm.model || '已连接') : '未配置') : MISSING,
      sub: llm?.provider ? llm.provider + ' · 延迟 ' + (llm.latency_ms ?? 0) + 'ms' : undefined,
      missing: !llm,
    },
    {
      key: 'cache-hit', label: 'LLM 缓存命中',
      tone: l3m?.cache_hit_ratio == null
        ? 'info'
        : (l3m.cache_hit_ratio >= 0.8 ? 'success' : 'warning'),
      value: l3m?.cache_hit_ratio == null
        ? MISSING
        : (l3m.cache_hit_ratio * 100).toFixed(1) + '%',
      sub: l3m?.cache_hit_ratio == null
        ? '本进程还没有"API 上报过缓存字段"的调用'
        : '命中 ' + Math.round(l3m.cache_hit_tokens || 0).toLocaleString()
          + ' / 未命中 ' + Math.round(l3m.cache_miss_tokens || 0).toLocaleString()
          + ' token · ' + CACHE_SRC,
      missing: l3m?.cache_hit_ratio == null,
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
  // 2026-10-29：功能单位面板原先是「永远挂在页面上方」的一块，把每个 Tab 都压得很长；
  // 现在独立成 Tab（技能/工具/工作流/调度/记忆… 在不在工作，与健康概览不是同一层级的问题）。
  { key: 'units', label: '功能单位', Icon: Boxes },
  { key: 'metrics', label: '指标', Icon: Activity },
  { key: 'stability', label: '稳定性', Icon: ShieldCheck },
  { key: 'performance', label: '性能', Icon: Zap },
  { key: 'quality', label: '质量', Icon: Sparkles },
  { key: 'alerts', label: '告警', Icon: Bell },
  { key: 'history', label: '历史', Icon: History },
  // 2026-10-29（用户选方案 A）：把「系统管理 · 仪表盘」的**只读汇总**搬进来一个 Tab，
  // 明细（用户/角色/通知列表）仍留在系统管理；数据源同一份 /api/dashboard/summary，
  // 服务端 _require_admin 鉴权，本页只负责"没权限时给明确提示"。
  { key: 'admin', label: '管理概览', Icon: UserCog },
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
  /** 管理概览（/api/dashboard/summary，服务端 _require_admin）：切到该 Tab 才拉一次 */
  const [adminSummary, setAdminSummary] = useState<DashboardSummaryData | null>(null)
  const [adminErr, setAdminErr] = useState('')
  const [adminLoaded, setAdminLoaded] = useState(false)

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
      // 【2026-11-11】计划任务卡改读 /api/scheduler/overview：/api/scheduler/tasks 只列**文件登记**的
      //   system_command 任务（实测 0 条），真正跑 cron/interval 的任务只在 overview 里；该端点带
      //   统一信封（{code,data}）⇒ 解一层 data 再交给卡片。
      {
        key: 'schedulerTasks',
        run: async () => {
          const raw = await hubGet<Record<string, unknown>>(SCHEDULER_OVERVIEW)
          const data = (raw && typeof raw === 'object' && 'data' in raw)
            ? (raw as { data?: { tasks?: unknown[] } }).data
            : (raw as { tasks?: unknown[] })
          return { tasks: (data?.tasks ?? []) } as SchedulerTasks
        },
      },
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

  useEffect(() => {
    if (tab !== 'admin' || adminLoaded) return
    setAdminLoaded(true)
    getDashboardSummary().then((d) => {
      const issues = validateDashboardSummary(d)
      if (issues.length) { setAdminErr('接口返回结构与页面不匹配：' + issues.join('；')); return }
      setAdminSummary(d as DashboardSummaryData)
    }).catch((e: unknown) => {
      setAdminErr(e instanceof Error ? e.message : String(e))
    })
  }, [tab, adminLoaded])
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
              <th className="px-3 py-2 font-medium">数据来源</th>
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
                  <td className="px-3 py-2.5 text-xs text-slate-500" title={l.originDetail}>
                    {l.origin}
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
          {/* ── Tab 导航（老页 7 个 Tab + 本轮拆出的「功能单位」）── */}
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
                const metricLine = layerMetricLine(l.key, d?.metrics)
                return (
                  <div key={l.key} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 text-center" data-testid={'dim-' + l.key}>
                    <div className="text-xl">{l.icon}</div>
                    <div className="mt-1 text-xs text-slate-500">{l.label}</div>
                    {/* 数据来源：回答"这个分数是谁算的/读的什么"（2026-11-08 Operator 要求） */}
                    <div className="mt-1 truncate text-[9px] text-slate-600" title={l.originDetail} data-testid={'origin-' + l.key}>
                      来源：{l.origin}
                    </div>
                    <div className={'mt-1 text-2xl font-semibold ' + (lv === 'excellent' || lv === 'good' ? 'text-emerald-400' : lv === 'fair' || lv === 'warning' ? 'text-amber-400' : lv === 'critical' ? 'text-red-400' : 'text-slate-600')}>
                      {score == null ? NO_PROBE_DATA : score.toFixed(0)}
                    </div>
                    {/* 合并进来的原始比率（L3 错误率 / L4 成功率）：只在这里出现一次 */}
                    {metricLine && (
                      <div className="mt-0.5 text-[10px] text-slate-500" data-testid={'metric-' + l.key}>
                        {metricLine}
                      </div>
                    )}
                    <div className="mt-2 h-1 overflow-hidden rounded-sm bg-slate-800">
                      <div className="h-full rounded-sm" style={{ width: (score ?? 0) + '%', background: l.color }} />
                    </div>
                    {d?.detail && <div className="mt-2 truncate text-[10px] text-slate-600" title={d.detail}>{d.detail}</div>}
                  </div>
                )
              })}
            </div>
          </div>

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
                {cards.map((c) => <MetricTile key={c.key} card={c} />)}
              </div>

              {/* 「—（端点未返回）」不是页面坏了：那个字段后端端点确实没给，前端不重算、不编造。
                  2026-11-08 Operator 问"很多端点未返回是怎么回事" ⇒ 把三处结构性缺口写清楚。 */}
              <p className="mt-2 text-[10px] leading-relaxed text-slate-600" data-testid="missing-note">
                <span className="text-slate-500">「—（端点未返回）」= 那个字段在后端端点里<strong>不存在</strong></span>（不是加载失败）：
                前端只显示真实返回的值，不拿浏览器侧的数去补。已知三处结构性缺口 ——
                ① 心跳端点只有 CPU/内存的<strong className="text-slate-500">使用率</strong>，没有核心数/可用量/磁盘，
                这三项现在改由 <span className="text-slate-500">L1 探针的 metrics</span> 提供；
                ② <span className="text-slate-500">/api/diagnostics/metrics</span> 的 histograms 为空
                （该指标还没产生采样）⇒ P50/P95/P99 与延迟分布只能占位；
                ③ <span className="text-slate-500">/api/observability/alerts</span> 只返回规则定义，
                不含运行态 state/activeAt ⇒ 告警表那两列占位。
              </p>

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

              {/* 概览不重复展示明细（2026-10-29 §11.21-7）：
                  · 「五层探针明细」→ 稳定性 Tab（L1-L3 表）/ 质量 Tab（L4-L5 表）/ 下方「近期摘要」
                  · 「运行告警」→ 告警 Tab（含级别/描述/表达式/状态的完整规则表）
                  同源数据只留一处可交互的落点，概览只负责「现在好不好」。 */}

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
          {tab === 'units' && (
            <div data-testid="panel-units">
              <UnitStatus />
            </div>
          )}

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
                            {/* 实测规则无 state / activeAt：如实标注，不把「有规则」当成「已触发」。
                                不可求值的规则给中文态（无数据 / 样本不足 / 需 Prometheus），
                                悬浮看原因 —— 与 L5 同口径：没测到不等于正常。 */}
                            <td className="px-3 py-2 text-xs text-slate-500" title={r.state_reason || undefined}>
                              {r.state ? (ALERT_STATE_LABEL[r.state] ?? r.state) : MISSING}
                            </td>
                            <td className="px-3 py-2 text-xs text-slate-500">{r.activeAt ? fmtTime(r.activeAt) : MISSING}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="mt-2 text-[11px] text-slate-600">
                共 {alerts?.groups?.length ?? 0} 个告警组 / {alertRules.length} 条规则；
                状态与触发时间来自**报警器的本进程规则引擎**（agent/alarms/rules.py，解析 alerts.yml 的
                PromQL 子集求值）与 alert_evaluator 的运行态（runtime_state={String(alerts?.runtime_state ?? false)}）；
                算不了的规则标「无数据 / 样本不足 / 需 Prometheus」并给原因，**不写成「未触发」**。
              </div>
            </div>
          )}

          {/* ══════════ 管理概览（方案 A：只读汇总，明细仍在系统管理） ══════════ */}
          {tab === 'admin' && (
            <div data-testid="panel-admin">
              <Card title="管理概览（数据源 /api/dashboard/summary · 需管理后台登录或 API 令牌）"
                actions={<span className="text-[11px] text-slate-500">明细（用户/角色/通知/审计）仍在「系统管理 · 仪表盘」</span>}
              >
                {adminErr ? (
                  <ErrorBox message={'取不到管理概览：' + adminErr + '（该接口由服务端 _require_admin 校验，未登录/无权限属正常降级）'} />
                ) : !adminSummary ? <Loading /> : (
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    {([
                      ['用户总数', adminSummary.totalUsers],
                      ['启用用户', adminSummary.activeUsers],
                      ['角色数', adminSummary.totalRoles],
                      ['通知总数', adminSummary.totalNotifications],
                      ['未读通知', adminSummary.unreadNotifications],
                      ['今日审计', adminSummary.todayAuditCount],
                      ['传感器', adminSummary.sensorCount],
                    ] as const).map(([label, value]) => (
                      <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                        <div className="text-xs text-slate-500">{label}</div>
                        <div className="mt-1 text-2xl font-semibold text-slate-200">{value ?? MISSING}</div>
                      </div>
                    ))}
                    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
                      <div className="text-xs text-slate-500">系统状态</div>
                      <div className="mt-1 text-lg font-semibold text-emerald-400">{adminSummary.systemStatus || MISSING}</div>
                    </div>
                  </div>
                )}
              </Card>
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
      <ModeCard />
      <HealthVitals />
      <OpsDiagnostics />
    </div>
  )
}
