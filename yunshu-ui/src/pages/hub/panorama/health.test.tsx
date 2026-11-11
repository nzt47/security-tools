/**
 * 健康仪表盘测试（对齐老根页 health_dashboard.html 的恢复验收）
 * ================================================================
 * 【为什么打桩 fetch 而不是 hubGet/getEnvelope】
 * 本页同时走两条真实链路：心跳三件套用 getEnvelope（要求 X-Envelope: v2 头 +
 * {code,data,message} 体），其余端点用 hubGet（原始 JSON）。只打桩 fetch 才能
 * 覆盖「哪条链路认哪种形态」这条契约 —— 打桩 hubGet 会把信封判断整段绕过去。
 *
 * 【使用的响应样本】直接抄自 2026-10-15 对 127.0.0.1:5678 的实测响应（见 health.tsx 头注），
 * 包括实测的缺字段现状（无 gauges、histograms 为空、告警规则无 state/activeAt），
 * 以便把「缺失如实标注」这条也钉进用例。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

import PanoramaHealth, {
  AlarmCoverageBlock,
  MISSING, NO_PROBE_DATA, PROBE_LAYERS, SOURCE_LABELS,
  buildHistoryCsv, downsample, heartbeatStatusText, historyRows, layerMetricLine, levelOf,
  metricRows, overviewCards, probeScoreText, secondsToMs,
  type AlertsData, type HeartbeatData, type HeartbeatHistory, type HeartbeatStatus,
  type MetricsData, type ProbeTrend,
} from './health'

// ── 实测样本（截取，形状一致）──────────────────────────────────
const HB: HeartbeatData = {
  status: 'healthy',
  timestamp: '2026-10-15T19:04:55.058920',
  checks: {
    llm: { latency_ms: 0, model: 'deepseek-flash', provider: 'deepseek', status: 'ok' },
    memory: { message: 'MemoryManager 正常运行', status: 'ok' },
    scheduler: { running: true, status: 'ok', tasks: 7 },
    system: { cpu: 24.3, memory: 86.6, status: 'ok' },
    threads: { alive: ['task-scheduler'], status: 'ok', total: 30 },
  },
}
const HB_STATUS: HeartbeatStatus = {
  status: 'healthy', timestamp: '2026-10-15T19:04:55.058920',
  total_checks: 1440, healthy_checks: 1412,
}
const HB_HISTORY: HeartbeatHistory = {
  total: 60,
  limit: 60,
  offset: 0,
  history: [
    { cpu: 22.2, llm_latency_ms: 0, memory: 68, status: 'healthy', timestamp: '2026-10-15T17:52:04.159858' },
    { cpu: 24.3, llm_latency_ms: 0, memory: 86.6, status: 'healthy', timestamp: '2026-10-15T19:04:55.058920' },
  ],
}
const PROBE: ProbeTrend = {
  hours: 1,
  total_records: 2,
  points: [
    {
      timestamp: '2026-10-15T18:05:22.271062', overall: 0.8181818181818181,
      dimensions: { l1_process: 0.8, l2_dependency: 1, l3_llm_tool: null, l4_business: null, l5_semantic: 0.5 },
      probe_details: {
        l1_process: { available: true, detail: 'mem=72.2% cpu=21.0% threads=56 disk_free=19.1%', score: 0.8 },
        l2_dependency: { available: true, detail: 'configs 核心文件可读 2/2', score: 1 },
        l3_llm_tool: { available: false, detail: '无 LLM/工具链路数据（未注册熔断器且无调用计数）', score: null },
        l4_business: { available: false, detail: '最近窗口无业务请求', score: null },
        l5_semantic: { available: true, detail: 'feedback=2 satisfaction=50.0% dislike=1', score: 0.5 },
      },
    },
    {
      timestamp: '2026-10-15T19:05:22.271062', overall: 0.4,
      dimensions: { l1_process: 0.7, l2_dependency: 1, l3_llm_tool: null, l4_business: null, l5_semantic: 0.5 },
      probe_details: {
        l1_process: { available: true, detail: 'mem=70.0% cpu=20.0% threads=56 disk_free=19.1%', score: 0.7 },
        l2_dependency: { available: true, detail: 'configs 核心文件可读 2/2', score: 1 },
        l3_llm_tool: { available: false, detail: '无 LLM/工具链路数据', score: null },
        l4_business: { available: false, detail: '最近窗口无业务请求', score: null },
        l5_semantic: { available: true, detail: 'feedback=2', score: 0.5 },
      },
    },
  ],
}
/** 实测：counters 只有一个键、histograms 为空、没有 gauges 键 */
const METRICS: MetricsData = {
  counters: { 'llm.chat.failure': 2 },
  histograms: {},
  generated_at: 1792062295.828103,
  timestamp: 1792062295.828103,
}
const ALERTS: AlertsData = {
  groups: [{
    name: 'yunshu_service_health', interval: '30s',
    rules: [{
      alert: 'YunshuServiceDown',
      expr: 'up{job="yunshu"} == 0',
      for: '1m',
      labels: { category: 'availability', service: 'yunshu', severity: 'critical' },
      annotations: { summary: '云枢服务不可用', description: '服务实例已停止运行超过 1 分钟', impact: '用户无法使用云枢服务' },
    }, {
      alert: 'YunshuServiceRestart',
      expr: 'resets(up{job="yunshu"}[1h]) > 3',
      labels: { severity: 'warning' },
      annotations: { summary: '服务频繁重启' },
    }],
  }],
  timestamp: 1792062295.8,
}

// ── 报警器覆盖读数（2026-11-11 实测形状：/api/alarms/coverage · /api/alarms/rules）──
const COVERAGE = {
  sources: [
    { id: 'health', name: '健康五层探针', detail: 'L1/L2/L3/L4 低于阈值报警；L5 无数据不报警' },
    { id: 'units', name: '功能单位看板', detail: '12 个单位，只报 warn/error（never/off 不报）' },
    { id: 'rules', name: '告警规则（alerts.yml）', detail: '16 条 PromQL 子集在本进程求值' },
    { id: 'audit', name: '审计链', detail: '尾部 500 条重算不一致 = critical（每小时最多一次）' },
    { id: 'scheduler', name: '计划任务', detail: '近 6 小时执行历史里 status=failed 的任务' },
    { id: 'ci', name: 'CI 结果', detail: 'data/ci/status.json；超过 2 小时未同步按不可用' },
  ],
  rules: {
    rules_total: 16, rules_evaluable: 11, rules_blocked: 5,
    by_state: { ok: 10, firing: 1, no_data: 5 },
    blocked: [{ alert: 'YunshuSkillEvalScoreLow', state: 'no_data', state_label: '无数据（指标不存在或未自增）', reason: '无序列: yunshu_skill_eval_score' }],
  },
  last_collect: {
    checked: { health: true, units: false, rules: true, audit: true, scheduler: true, ci: false },
    raised: 1, resolved: 0, kept: [], warmup: false,
  },
  prometheus: { configured: false, last_ok: null, detail: '未配置（CP_PROMETHEUS_URL 为空）：up{} 类规则只能报 no_data' },
}

const RULES = {
  rules: [{ alert: 'YunshuHighErrorRate', state: 'ok', state_label: '正常' }],
  coverage: {
    rules_total: 16, rules_evaluable: 11, rules_blocked: 5,
    by_state: { ok: 10, firing: 1, no_data: 5 },
    blocked: [{ alert: 'YunshuServiceDown', state: 'no_data', state_label: '无数据（指标不存在或未自增）', reason: '无序列: up' }],
  },
  samples: 42,
}
// 【为什么 blocked 里不用 YunshuServiceDown】告警规则表里已经有同名规则；同名两处会让
// 既有用例的 getByText 命中多个节点（这次就撞了）。用一个规则表里没有、只在覆盖读数里出现的名字。

// ── fetch 打桩 ────────────────────────────────────────────────
const fetchMock = vi.fn()

/** enveloped=true 的端点带 X-Envelope: v2 + {code,data,message}；其余返回原始 JSON */
function fakeResponse(body: unknown, enveloped: boolean) {
  const payload = enveloped ? { code: 200, data: body, message: '' } : body
  return {
    ok: true,
    status: 200,
    headers: { get: (h: string) => (enveloped && h === 'X-Envelope' ? 'v2' : null) },
    json: async () => payload,
  }
}

/** 与 installRoutes 同路由，供逐用例覆盖单端点时复用 */
function installRoutesPassThrough(url: string) {
  if (url.startsWith('/api/heartbeat/status')) return fakeResponse(HB_STATUS, true)
  if (url.startsWith('/api/heartbeat/history')) return fakeResponse(HB_HISTORY, true)
  if (url.startsWith('/api/heartbeat')) return fakeResponse(HB, true)
  if (url.startsWith('/api/health/probe-trend')) return fakeResponse(PROBE, false)
  if (url === '/api/diagnostics/metrics') return fakeResponse(METRICS, false)
  if (url === '/api/observability/alerts') return fakeResponse(ALERTS, false)
  if (url === '/api/scheduler/overview') return fakeResponse({ code: 200, data: { tasks: [], count: 0 }, message: '' }, false)
  if (url.startsWith('/api/scheduler/history')) return fakeResponse({ history: [{ name: '系统心跳', status: 'success', duration_ms: 0, start_time: '2026-10-15T19:04:51.832102' }] }, false)
  // 2026-11-11：告警 Tab 顶部的「监控覆盖」两条只读端点（/api/alarms/* 走裸 fetch + 信封）
  if (url === '/api/alarms/coverage') return fakeResponse(COVERAGE, true)
  if (url === '/api/alarms/rules') return fakeResponse(RULES, true)
  throw new Error('unexpected url: ' + url)
}

function installRoutes() {
  fetchMock.mockImplementation(async (input: unknown) => installRoutesPassThrough(String(input)))
}

beforeEach(() => {
  fetchMock.mockReset()
  installRoutes()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// ═══════════════════════════════════════════════════════════════
//  纯函数
// ═══════════════════════════════════════════════════════════════
describe('health 纯函数', () => {
  it('levelOf 阈值与老页 getDimLevel 一致', () => {
    expect(levelOf(95)).toBe('excellent')
    expect(levelOf(70)).toBe('good')
    expect(levelOf(50)).toBe('fair')
    expect(levelOf(30)).toBe('warning')
    expect(levelOf(29.9)).toBe('critical')
  })

  it('probeScoreText / secondsToMs / heartbeatStatusText 对缺失值给出明确占位', () => {
    expect(probeScoreText(0.8)).toBe('80')
    expect(probeScoreText(null)).toBe(NO_PROBE_DATA)
    expect(probeScoreText(undefined)).toBe(NO_PROBE_DATA)
    expect(secondsToMs(0.1234)).toBe('123ms')
    expect(secondsToMs(undefined)).toBe(MISSING)
    expect(heartbeatStatusText('healthy')).toBe('健康')
    expect(heartbeatStatusText('degraded')).toBe('亚健康')
    expect(heartbeatStatusText('unhealthy')).toBe('异常')
  })

  it('metricRows 展开 counters/gauges/histograms，缺 gauges 不报错', () => {
    expect(metricRows(METRICS)).toEqual([
      { name: 'llm.chat.failure', type: 'counter', value: '2', desc: '累计计数' },
    ])
    expect(metricRows(undefined)).toEqual([])
    const withAll = metricRows({
      counters: { c: 1 },
      gauges: { g: 1.239 },
      histograms: { h: { avg: 0.5, p50: 0.25, p95: 0.9, p99: 1 } },
    })
    expect(withAll.map((r) => r.type)).toEqual(['counter', 'gauge', 'histogram'])
    expect(withAll.find((r) => r.type === 'gauge')?.value).toBe('1.24')
    expect(withAll.find((r) => r.type === 'histogram')?.desc).toContain('p99 1000ms')
  })

  it('overviewCards 对实测缺失字段给出 MISSING 而非编造值', () => {
    const cards = overviewCards(HB, HB_STATUS, METRICS, PROBE.points![1])
    const by = (k: string) => cards.find((c) => c.key === k)!
    expect(by('sys-status').value).toBe('健康')
    expect(by('cpu').value).toBe('24.3%')
    expect(by('memory').value).toBe('86.6%')
    expect(by('check-count').value).toBe('1440 次')
    // 【2026-11-11】两张与维度卡重复的指标卡已合并进维度卡 ⇒ 概览卡里不得再出现同名卡
    expect(cards.find((c) => c.key === 'task-success')).toBeUndefined()
    expect(cards.find((c) => c.key === 'error-rate')).toBeUndefined()
    for (const k of ['cpu-cores', 'mem-available', 'disk-available', 'requests', 'latency', 'p99']) {
      expect(by(k).value, k).toBe(MISSING)
      expect(by(k).missing, k).toBe(true)
    }
  })

  it('historyRows / buildHistoryCsv 输出老页同列结构', () => {
    const rows = historyRows(PROBE.points!)
    expect(rows).toHaveLength(2)
    expect(rows[0].overallText).toBe('81.8')
    expect(rows[0].level).toBe('good')
    expect(rows[0].dims.l1_process).toBe(80)
    expect(rows[1].dims.l3_llm_tool).toBeNull()
    const csv = buildHistoryCsv(PROBE.points!)
    const lines = csv.split('\n')
    expect(lines[0]).toBe('时间,综合得分,等级,L1 进程,L2 依赖,L3 LLM/工具,L4 业务,L5 语义')
    expect(lines[1].startsWith('2026-10-15T18:05:22.271062,81.8,良好,80,100,')).toBe(true)
    expect(csv).toContain(NO_PROBE_DATA)
  })

  it('downsample 点数不超上限且保留首尾', () => {
    const arr = Array.from({ length: 1000 }, (_, i) => i)
    const out = downsample(arr, 240)
    expect(out.length).toBeLessThanOrEqual(241)
    expect(out[0]).toBe(0)
    expect(out[out.length - 1]).toBe(999)
    expect(downsample([1, 2, 3], 240)).toEqual([1, 2, 3])
  })
})

// ═══════════════════════════════════════════════════════════════
//  2026-11-11 Operator 问：为什么有两个「L3 LLM/工具」？（合并进维度卡）
// ═══════════════════════════════════════════════════════════════
describe('L3/L4 同名指标卡合并进维度卡', () => {
  it('layerMetricLine 只认后端结构化数值，取不到就空串（不编数字）', () => {
    expect(layerMetricLine('l3_llm_tool', { error_rate: 0.024 })).toBe('错误率 2.4%')
    expect(layerMetricLine('l4_business', { success_rate: 0.99 })).toBe('成功率 99.0%')
    expect(layerMetricLine('l3_llm_tool', {})).toBe('')
    expect(layerMetricLine('l4_business', undefined)).toBe('')
    // 其它层没有这一行（指标只属于 L3/L4）
    expect(layerMetricLine('l1_process', { error_rate: 0.5 })).toBe('')
  })

  it('概览里「L3 LLM/工具」只在维度卡出现一次，比率行来自 probe_details.metrics', async () => {
    const point = {
      ...PROBE.points![1],
      probe_details: {
        ...PROBE.points![1].probe_details,
        l3_llm_tool: {
          available: true, score: 0.88, detail: 'llm_calls=100 errors=2 error_rate=2.0%',
          metrics: { llm_calls: 100, llm_errors: 2, error_rate: 0.02 },
        },
        l4_business: {
          available: true, score: 0.99, detail: 'http_requests=1000 errors=10 success_rate=99.0%',
          metrics: { http_requests: 1000, http_errors: 10, success_rate: 0.99 },
        },
      },
    }
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/health/probe-trend')) {
        return fakeResponse({ hours: 1, points: [point], total_records: 1 }, false)
      }
      return installRoutesPassThrough(url)
    })
    render(<PanoramaHealth />)
    await screen.findByTestId('dim-l3_llm_tool')
    // 合并后的比率行：只由后端 metrics 算，前端不拆 detail 文本
    expect(screen.getByTestId('metric-l3_llm_tool').textContent).toBe('错误率 2.0%')
    expect(screen.getByTestId('metric-l4_business').textContent).toBe('成功率 99.0%')
    // 两张旧指标卡（key=error-rate / task-success）彻底消失
    expect(screen.queryByTestId('ov-error-rate')).toBeNull()
    expect(screen.queryByTestId('ov-task-success')).toBeNull()
    // 维度卡网格里该标签只出现一次（旧版这里是两次：维度卡 + 同名指标卡）
    const grid = screen.getByTestId('health-dimensions')
    expect(within(grid).getAllByText('L3 LLM/工具').length).toBe(1)
  })
})

// ═══════════════════════════════════════════════════════════════
//  页面渲染
// ═══════════════════════════════════════════════════════════════
describe('PanoramaHealth 渲染', () => {
  it('状态条：心跳状态 / 上次更新时间 / 历史条数 / API 响应', async () => {
    render(<PanoramaHealth />)
    const bar = await screen.findByTestId('health-realtime-bar')
    expect(within(bar).getByText(/健康/)).toBeTruthy()
    expect(screen.getByTestId('health-last-update').textContent).toContain('2026')
    expect(screen.getByTestId('health-history-count').textContent).toBe('1440')
    await waitFor(() => expect(screen.getByTestId('health-api-latency').textContent).toMatch(/ms$/))
  })

  it('概览：指标卡 + 五层维度卡 + 近期摘要 + 心跳资源', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    expect(within(screen.getByTestId('ov-sys-status')).getByText('健康')).toBeTruthy()
    expect(within(screen.getByTestId('ov-cpu')).getByText('24.3%')).toBeTruthy()
    expect(within(screen.getByTestId('ov-memory')).getByText('86.6%')).toBeTruthy()
    expect(within(screen.getByTestId('ov-check-count')).getByText('1440 次')).toBeTruthy()
    expect(within(screen.getByTestId('ov-cpu-cores')).getByText(MISSING)).toBeTruthy()
    expect(within(screen.getByTestId('ov-disk-available')).getByText(MISSING)).toBeTruthy()
    expect(within(screen.getByTestId('ov-requests')).getByText(MISSING)).toBeTruthy()
    expect(within(screen.getByTestId('ov-latency')).getByText(MISSING)).toBeTruthy()
    for (const k of ['l1_process', 'l2_dependency', 'l3_llm_tool', 'l4_business', 'l5_semantic']) {
      expect(screen.getByTestId('dim-' + k)).toBeTruthy()
    }
    expect(within(screen.getByTestId('dim-l1_process')).getByText('70')).toBeTruthy()
    expect(within(screen.getByTestId('dim-l3_llm_tool')).getByText(NO_PROBE_DATA)).toBeTruthy()
    expect(screen.getByTestId('health-assess-time').textContent).toContain('2026')
    expect(screen.getByTestId('health-summary-table')).toBeTruthy()
    expect(screen.getByText(/资源趋势（最近 60 次心跳/)).toBeTruthy()
    expect(screen.getByText(/计划任务（0）/)).toBeTruthy()
    expect(screen.getByText('系统心跳')).toBeTruthy()
  })

  it('概览不再重复：五层探针明细 / 运行告警只在对应 Tab 出现（§11.21-7）', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    const ov = screen.getByTestId('panel-overview')
    // 概览只回答「现在好不好」：明细卡已移出，避免与稳定性/质量/告警 Tab 三处重复
    expect(within(ov).queryByText('五层探针明细')).toBeNull()
    expect(within(ov).queryByText(/运行告警/)).toBeNull()
    expect(within(ov).queryByText('YunshuServiceDown')).toBeNull()

    // 告警规则（真实形状 groups[].rules[]）仍可在告警 Tab 读到
    fireEvent.click(screen.getByTestId('tab-alerts'))
    const alerts = await screen.findByTestId('panel-alerts')
    expect(within(alerts).getByText('YunshuServiceDown')).toBeTruthy()
    expect(within(alerts).getAllByText('critical').length).toBeGreaterThan(0)

    // 五层探针明细同理：稳定性 Tab 有 L1-L3 表
    fireEvent.click(screen.getByTestId('tab-stability'))
    const stab = await screen.findByTestId('stab-probe-table')
    expect(within(stab).getByText(/L1 进程/)).toBeTruthy()
  })

  it('稳定性 / 质量 Tab 分别给出 L1-L3 与 L4-L5 探针明细表', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-stability'))
    const stab = await screen.findByTestId('stab-probe-table')
    expect(within(stab).getByText(/L1 进程/)).toBeTruthy()
    expect(within(stab).getByText(/L2 依赖/)).toBeTruthy()
    expect(within(stab).getByText(/L3 LLM\/工具/)).toBeTruthy()
    expect(within(stab).queryByText(/L4 业务/)).toBeNull()
    expect(within(stab).getByText(/mem=70.0%/)).toBeTruthy()

    fireEvent.click(screen.getByTestId('tab-quality'))
    const qual = await screen.findByTestId('qual-probe-table')
    expect(within(qual).getByText(/L4 业务/)).toBeTruthy()
    expect(within(qual).getByText(/L5 语义/)).toBeTruthy()
    expect(within(qual).queryByText(/L1 进程/)).toBeNull()
    expect(within(qual).getAllByText('无数据').length).toBeGreaterThan(0)
  })

  it('指标 Tab：全量展开 + 类型/搜索筛选', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-metrics'))
    expect(await screen.findByTestId('metrics-table')).toBeTruthy()
    expect(screen.getByText('llm.chat.failure')).toBeTruthy()
    expect(screen.getByText(/共 1 条，筛选后 1 条/)).toBeTruthy()
    fireEvent.change(screen.getByTestId('metric-search'), { target: { value: '不存在' } })
    expect(screen.getByText('没有匹配的指标')).toBeTruthy()
    fireEvent.change(screen.getByTestId('metric-search'), { target: { value: '' } })
    fireEvent.change(screen.getByTestId('metric-type-filter'), { target: { value: 'histogram' } })
    expect(screen.getByText('没有匹配的指标')).toBeTruthy()
  })

  it('性能 Tab：P50/P95/P99 与吞吐在端点未返回时显示占位', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-performance'))
    const panel = await screen.findByTestId('panel-performance')
    expect(within(panel).getAllByText(MISSING).length).toBeGreaterThanOrEqual(4)
    expect(within(panel).getByText(/延迟分布不可用/)).toBeTruthy()
    expect(within(panel).getByText(/LLM 延迟趋势/)).toBeTruthy()
  })

  it('告警 Tab：规则表 + 状态/触发时间如实标注端点未返回', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-alerts'))
    const table = await screen.findByTestId('alerts-table')
    expect(within(table).getByText('YunshuServiceDown')).toBeTruthy()
    expect(within(table).getByText('云枢服务不可用')).toBeTruthy()
    expect(within(table).getByText('up{job="yunshu"} == 0')).toBeTruthy()
    expect(within(table).getAllByText(MISSING).length).toBeGreaterThanOrEqual(2)
  })

  it('历史 Tab：历史表 + CSV 导出（含等级筛选）', async () => {
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock')
    const revokeObjectURL = vi.fn()
    ;(URL as unknown as Record<string, unknown>).createObjectURL = createObjectURL
    ;(URL as unknown as Record<string, unknown>).revokeObjectURL = revokeObjectURL
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-history'))
    const table = await screen.findByTestId('history-table')
    expect(within(table).getByText('81.8')).toBeTruthy()
    expect(within(table).getByText('40.0')).toBeTruthy()

    fireEvent.change(screen.getByTestId('history-level-filter'), { target: { value: 'good' } })
    expect(within(screen.getByTestId('history-table')).queryByText('40.0')).toBeNull()

    fireEvent.click(screen.getByTestId('history-export'))
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))
    const blob = createObjectURL.mock.calls[0][0] as Blob
    expect(blob.type).toBe('text/csv;charset=utf-8')
    const text = await new Promise<string>((resolve, reject) => {
      const fr = new FileReader()
      fr.onload = () => resolve(String(fr.result))
      fr.onerror = () => reject(fr.error)
      fr.readAsText(blob)
    })
    expect(text).toContain('时间,综合得分,等级')
    expect(text).toContain('81.8')
    expect(text).not.toContain('40.0')
    clickSpy.mockRestore()
  })
})

// ═══════════════════════════════════════════════════════════════
//  失败必须上屏
// ═══════════════════════════════════════════════════════════════
describe('PanoramaHealth 失败上屏', () => {
  it('probe-trend 500 → 顶栏数据源故障 + 稳定性/历史内联错误，不静默当空数组', async () => {
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/health/probe-trend')) {
        return { ok: false, status: 500, headers: { get: () => null }, json: async () => ({}) }
      }
      return installRoutesPassThrough(url)
    })
    render(<PanoramaHealth />)
    const sourceErrors = await screen.findByTestId('health-source-errors')
    expect(within(sourceErrors).getByText(new RegExp(SOURCE_LABELS.probeTrend))).toBeTruthy()
    fireEvent.click(screen.getByTestId('tab-stability'))
    expect(await screen.findByText(/加载失败：HTTP 500/)).toBeTruthy()
  })

  it('心跳响应缺少 X-Envelope 头 → 明确报「不是统一信封」，而不是显示空状态', async () => {
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input)
      if (url.startsWith('/api/heartbeat')) {
        return { ok: true, status: 200, headers: { get: () => null }, json: async () => HB }
      }
      return installRoutesPassThrough(url)
    })
    render(<PanoramaHealth />)
    const sourceErrors = await screen.findByTestId('health-source-errors')
    expect(within(sourceErrors).getAllByText(/X-Envelope/).length).toBeGreaterThan(0)
    expect(screen.getByTestId('health-history-count').textContent).toBe(MISSING)
  })

  it('metrics 500 → 指标 Tab 内联错误', async () => {
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input)
      if (url === '/api/diagnostics/metrics') {
        return { ok: false, status: 500, headers: { get: () => null }, json: async () => ({}) }
      }
      return installRoutesPassThrough(url)
    })
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-metrics'))
    expect(await screen.findByText(/加载失败：HTTP 500/)).toBeTruthy()
  })
})

// ═══════════════════════════════════════════════════════════════
//  功能单位独立成 Tab（2026-10-29）
// ═══════════════════════════════════════════════════════════════
describe('PanoramaHealth 管理概览 Tab（方案 A）', () => {
  it('切到「管理概览」会拉 /api/dashboard/summary；取不到时给出明确降级提示而不是空白', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-admin'))
    const panel = await screen.findByTestId('panel-admin')
    // 测试环境的 fetch stub 不会返回该端点的合法载荷 ⇒ 必须落到「明确错误」而不是白屏
    await waitFor(() => {
      const text = panel.textContent || ''
      expect(text).toMatch(/管理概览|取不到管理概览/)
    })
  })
})

describe('PanoramaHealth 功能单位 Tab', () => {
  it('概览不再常驻功能单位面板，切到「功能单位」Tab 才挂载', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    // 面板原先挂在所有 Tab 上方（把每页都压得很长）⇒ 概览里不应再出现
    expect(screen.queryByTestId('unit-status')).toBeNull()
    fireEvent.click(screen.getByTestId('tab-units'))
    const panel = await screen.findByTestId('panel-units')
    expect(within(panel).getByTestId('unit-status')).toBeTruthy()
    // 切回概览后卸载，不会继续轮询/占位
    fireEvent.click(screen.getByTestId('tab-overview'))
    expect(screen.queryByTestId('panel-units')).toBeNull()
  })
})

// ═══════════════════════════════════════════════════════════════
//  2026-11-08 Operator 三问：L5 是什么 / 数据从哪来 / 端点未返回怎么回事
// ═══════════════════════════════════════════════════════════════
describe('L1~L5 数据来源标注与「端点未返回」说明', () => {
  it('overviewCards 用 L1 探针 metrics 填核心数/可用内存/磁盘（不再恒为 MISSING）', () => {
    const point = {
      ...PROBE.points![1],
      probe_details: {
        ...PROBE.points![1].probe_details,
        l1_process: {
          available: true, score: 0.8,
          detail: 'mem=83.8% avail=2467MB cpu=25.0% threads=4 disk_free=22.8%',
          metrics: { cpu_cores: 12, mem_available_mb: 2467, disk_free_gb: 212.1 },
        },
      },
    }
    const cards = overviewCards(HB, HB_STATUS, METRICS, point)
    const by = (k: string) => cards.find((c) => c.key === k)!
    expect(by('cpu-cores').value).toBe('12 核')
    expect(by('cpu-cores').missing).toBe(false)
    expect(by('mem-available').value).toBe('2.4 GB')
    expect(by('disk-available').value).toBe('212.1 GB')
    expect(by('cpu-cores').sub).toContain('probe_details.l1_process.metrics')
  })

  it('五层维度卡都标出「来源：…」，并解释「—（端点未返回）」是什么', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('health-dimensions')
    for (const k of ['l1_process', 'l2_dependency', 'l3_llm_tool', 'l4_business', 'l5_semantic']) {
      const origin = screen.getByTestId('origin-' + k)
      expect(origin.textContent).toContain('来源：')
      // 悬浮给完整口径（否则"来源"两个字等于没说）
      expect((origin.getAttribute('title') || '').length).toBeGreaterThan(8)
    }
    const note = screen.getByTestId('missing-note')
    expect(note.textContent).toContain('端点未返回')
    expect(note.textContent).toContain('histograms')
    expect(note.textContent).toContain('state/activeAt')
  })

  it('探针明细表多出「数据来源」列（L1-L3 表里能看到 psutil）', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-stability'))
    const stab = await screen.findByTestId('stab-probe-table')
    expect(within(stab).getByText('数据来源')).toBeTruthy()
    expect(within(stab).getByText(/psutil 本机采样/)).toBeTruthy()
    expect(within(stab).getByText(/configs 核心文件可读性/)).toBeTruthy()
  })

  it('L5 口径写在来源说明里：只数 like/dislike、样本不足不评分（0 分是假的）', () => {
    // 渲染路径与其它四层共用 renderProbeTable（已由上面「稳定性/质量 Tab」用例覆盖）；
    // 这里锁"口径文案"，避免以后又把 suggestion/开发随记算进分母而没人发现。
    const l5 = PROBE_LAYERS.find((l) => l.key === 'l5_semantic')!
    expect(l5.origin).toContain('like/dislike')
    expect(l5.originDetail).toContain('开发随记')
    expect(l5.originDetail).toContain('样本')
    expect(probeScoreText(null)).toBe(NO_PROBE_DATA)
  })
})

// ═══════════════════════════════════════════════════════════════
//  报警器「监控覆盖」接进告警 Tab（2026-11-11 alarms-coverage-in-ui）
// ═══════════════════════════════════════════════════════════════
describe('告警 Tab 的监控覆盖块', () => {
  it('告警 Tab 顶部显示事件源 / 规则可求值性 / 采样环 / Prometheus 状态', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    fireEvent.click(screen.getByTestId('tab-alerts'))
    const block = await screen.findByTestId('alarm-coverage')

    // 六个事件源都列出来（含 2026-11-11 新增的计划任务与 CI）
    for (const name of ['健康五层探针', '功能单位看板', '告警规则（alerts.yml）', '审计链', '计划任务', 'CI 结果']) {
      expect(within(block).getByText(name)).toBeTruthy()
    }
    // 本轮读到的源与读不到的源必须**分别**显示（不可用 ≠ 正常）
    expect(within(block).getAllByText('本轮已读').length).toBe(4)
    expect(within(block).getAllByText('本轮不可用').length).toBe(2)

    // 规则可求值数 / 算不了数（不是「16 条都在监控」）
    expect(within(block).getByText(/可求值 11\/16/)).toBeTruthy()
    expect(within(block).getByText(/算不了 5/)).toBeTruthy()

    // 算不了的规则逐条给原因（缺哪个指标），不写成「未触发」
    const blocked = await screen.findByTestId('alarm-coverage-blocked')
    expect(within(blocked).getByText('YunshuSkillEvalScoreLow')).toBeTruthy()
    expect(within(blocked).getByText(/无序列: yunshu_skill_eval_score/)).toBeTruthy()

    // 采样环点数（窗口类规则需要 ≥2 点）
    expect(within(block).getByText(/42 个点/)).toBeTruthy()

    // 外部 Prometheus 未配置时如实说明 up{} 类规则只能报无数据
    const prom = screen.getByTestId('alarm-coverage-prometheus')
    expect(prom.textContent).toContain('未配置')
    expect(prom.textContent).toContain('无数据')
  })

  it('组件级：Prometheus 已配置可用 + 无 blocked 时不渲染告警块', () => {
    render(
      <AlarmCoverageBlock
        coverage={{
          sources: [{ id: 'rules', name: '告警规则（alerts.yml）', detail: '16 条' }],
          rules: { rules_total: 16, rules_evaluable: 16, rules_blocked: 0, by_state: { ok: 16 }, blocked: [] },
          last_collect: { checked: { rules: true }, raised: 0, resolved: 0, kept: [], warmup: false },
          prometheus: { configured: true, last_ok: true, detail: '已配置：每轮拉一次 instant up' },
        }}
        rules={{ rules: [], coverage: { rules_total: 16, rules_evaluable: 16, rules_blocked: 0, by_state: { ok: 16 }, blocked: [] }, samples: 3 }}
      />,
    )
    expect(screen.getByTestId('alarm-coverage')).toBeTruthy()
    expect(screen.queryByTestId('alarm-coverage-blocked')).toBeNull()
    const prom = screen.getByTestId('alarm-coverage-prometheus')
    expect(prom.textContent).toContain('已配置')
    expect(prom.textContent).toContain('可用')
    expect(screen.getByText(/3 个点/)).toBeTruthy()
  })

  it('组件级：两条端点都失败时上屏错误而不是显示成「一切正常」', () => {
    render(<AlarmCoverageBlock error="HTTP 500" />)
    expect(screen.getByText(/HTTP 500/)).toBeTruthy()
    expect(screen.queryByTestId('alarm-coverage')).toBeNull()
  })
})
