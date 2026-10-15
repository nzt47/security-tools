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
  MISSING, NO_PROBE_DATA, SOURCE_LABELS,
  buildHistoryCsv, downsample, heartbeatStatusText, historyRows, levelOf,
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
  if (url === '/api/scheduler/tasks') return fakeResponse({ tasks: [], scheduler_tasks: [], scheduler_updated_at: '' }, false)
  if (url.startsWith('/api/scheduler/history')) return fakeResponse({ history: [{ name: '系统心跳', status: 'success', duration_ms: 0, start_time: '2026-10-15T19:04:51.832102' }] }, false)
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
    expect(by('task-success').value).toBe(NO_PROBE_DATA)
    expect(by('error-rate').value).toBe(NO_PROBE_DATA)
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

  it('保留原有 5 层探针明细卡与运行告警卡（按真实 groups[].rules[] 读取）', async () => {
    render(<PanoramaHealth />)
    await screen.findByTestId('panel-overview')
    expect(screen.getByText('五层探针明细')).toBeTruthy()
    expect(screen.getByText('运行告警（2）')).toBeTruthy()
    expect(screen.getAllByText('YunshuServiceDown').length).toBeGreaterThan(0)
    expect(screen.getAllByText('critical').length).toBeGreaterThan(0)
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
