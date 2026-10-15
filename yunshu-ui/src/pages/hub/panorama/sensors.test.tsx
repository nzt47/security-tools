/**
 * 全景感知页守卫（sensors.tsx）
 * ------------------------------------------------------------------
 * 锁死 2026-10-15 修复的口径：
 *   1. **读数与描述符分别取**：行来自 /api/sensors 的描述符，值/severity 来自
 *      /api/panorama 的 data.health（缺读数如实显示"—（无读数）"，不假装 0）；
 *   2. 顶部卡片取 data.health 的 cpu_usage / memory_usage + sensor_on/sensor_total + 电池；
 *   3. 8 维标签直接消费后端 data.tag_dimensions（前端不重写维度表）；
 *   4. Promise.allSettled 的失败显式上屏（不静默只剩 "—"）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

function json(body: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body) } as unknown as Response
}
function envelope(data: unknown, status = 200) {
  return { ok: status < 300, status, headers: { get: (k: string) => (k === 'X-Envelope' ? 'v2' : null) }, json: async () => ({ code: 200, data }), text: async () => JSON.stringify({ code: 200, data }) } as unknown as Response
}

const TAG_DIMENSIONS = [
  { label: '目标域', values: ['硬件感知', '软件感知'] },
  { label: '内外方位', values: ['内部感知', '外部感知'] },
  { label: '动静属性', values: ['静态配置', '动态运行'] },
  { label: '采集方式', values: ['主动探测', '被动监听'] },
  { label: '感知层次', values: ['物理层', '系统层'] },
  { label: '功能角色', values: ['基础生存', '性能监控'] },
  { label: '数据特征', values: ['数值量', '状态量'] },
  { label: '可干预性', values: ['仅可观测', '可配置'] },
]

const PANORAMA = {
  sensor_on: 2,
  sensor_total: 3,
  health: [
    { sensor_name: 'cpu_usage', category: 'cpu', description: 'CPU 使用率', value: 24.7, unit: '%', severity: 'normal' },
    { sensor_name: 'memory_usage', category: 'memory', description: '内存占用率', value: 86.6, unit: '%', severity: 'warning' },
  ],
  tag_dimensions: TAG_DIMENSIONS,
}

const SENSORS = [
  { name: 'cpu', label: 'CPU（大脑）', category: 'cpu', enabled: true },
  { name: 'memory', label: '内存（记忆）', category: 'memory', enabled: true },
  { name: 'battery', label: '电池（饥饿感）', category: 'battery', enabled: false },
]

const HEALTH = [
  { sensor_name: 'cpu_usage', category: 'cpu', value: 11.1, unit: '%', severity: 'normal' },
]

function makeFetch(overrides: { panorama?: Response; sensors?: Response; health?: Response } = {}) {
  return vi.fn(async (url: string) => {
    const u = String(url)
    if (u.includes('/api/panorama')) return overrides.panorama ?? envelope(PANORAMA)
    if (u.includes('/api/sensors')) return overrides.sensors ?? envelope(SENSORS)
    if (u.includes('/api/health')) return overrides.health ?? json(HEALTH)
    if (u.includes('/api/cognitive/status')) return json({ summary: '一切正常' })
    return json({ rejected: false, reason: 'ok' })
  })
}

let fetchMock = makeFetch()
vi.stubGlobal('fetch', (...args: unknown[]) => (fetchMock as unknown as (...a: unknown[]) => unknown)(...args))

afterEach(() => {
  cleanup()
  fetchMock = makeFetch()
})

const SensorsPage = (await import('./sensors')).default

describe('全景感知 · 读数与描述符分别取', () => {
  it('描述符成行、读数来自 data.health；缺读数如实显示', async () => {
    render(<SensorsPage />)

    await waitFor(() => expect(screen.getByTestId('sensor-table').textContent).toContain('CPU（大脑）'))

    // 表格：值来自 data.health，不是描述符（描述符没有 value）
    expect(screen.getByTestId('sensor-table').textContent).toContain('24.7%')
    expect(screen.getByTestId('sensor-table').textContent).toContain('86.6%')
    // 电池无读数 ⇒ 如实显示，且不是 "0"
    expect(screen.getByTestId('sensor-table').textContent).toContain('—（无读数）')
    expect(screen.getByTestId('sensor-table').textContent).not.toContain('0.00')
  })

  it('顶部卡片：CPU/内存取读数，sensor_on/total 取顶层，电池无读数', async () => {
    render(<SensorsPage />)

    await waitFor(() => expect(screen.getByTestId('stat-cpu').textContent).toContain('24.7%'))
    expect(screen.getByTestId('stat-memory').textContent).toContain('86.6%')
    expect(screen.getByTestId('stat-sensor-on').textContent).toContain('2')
    expect(screen.getByTestId('stat-sensor-total').textContent).toContain('3')
    expect(screen.getByTestId('stat-battery').textContent).toContain('无读数')
  })

  it('8 维标签直接来自后端 tag_dimensions；分类 chip 带 severity 配色与启用态', async () => {
    render(<SensorsPage />)

    await waitFor(() => expect(screen.getByTestId('tag-dimensions')).toBeTruthy())
    expect(screen.getByTestId('tag-dim-目标域').textContent).toContain('硬件感知')
    // 8 个维度全部渲染
    expect(screen.getByTestId('tag-dimensions').children.length).toBe(8)

    const cpuChip = await screen.findByTestId('sensor-chip-cpu')
    expect(cpuChip.textContent).toContain('normal')
    expect(screen.getByTestId('sensor-chip-memory').textContent).toContain('warning')
    // battery 描述符 enabled=false ⇒ 显示已禁用且无读数
    const batteryChip = screen.getByTestId('sensor-chip-battery')
    expect(batteryChip.textContent).toContain('已禁用')
    expect(batteryChip.textContent).toContain('无读数')
    // 按 category 分组
    expect(screen.getByTestId('sensor-cat-cpu')).toBeTruthy()
    expect(screen.getByTestId('sensor-cat-battery')).toBeTruthy()
  })
})

describe('全景感知 · 失败上屏', () => {
  it('/api/sensors 失败：错误显式上屏，读数仍显示', async () => {
    fetchMock = makeFetch({ sensors: json({ error: 'boom' }, 500) })
    render(<SensorsPage />)

    await waitFor(() => expect(screen.getByTestId('panorama-failures')).toBeTruthy())
    expect(screen.getByTestId('panorama-failures').textContent).toContain('/api/sensors')
    // 表格空态（不再假装有条目）
    expect(await screen.findByText('无传感器描述符（/api/sensors 未返回数据）')).toBeTruthy()
    // 读数仍在
    expect(screen.getByTestId('stat-cpu').textContent).toContain('24.7%')
  })

  it('/api/panorama 失败：错误上屏且读数回退 /api/health', async () => {
    fetchMock = makeFetch({ panorama: json({ error: 'boom' }, 500) })
    render(<SensorsPage />)

    await waitFor(() => expect(screen.getByTestId('panorama-failures')).toBeTruthy())
    expect(screen.getByTestId('panorama-failures').textContent).toContain('/api/panorama')
    await waitFor(() => expect(screen.getByTestId('stat-cpu').textContent).toContain('11.1%'))
    // 维度表不可得时如实说明，不静默
    expect(await screen.findByText(/维度表不可得/)).toBeTruthy()
  })
})
