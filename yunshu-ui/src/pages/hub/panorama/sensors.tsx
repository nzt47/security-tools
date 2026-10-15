/**
 * 全景感知 —— 传感器实时状态 + 健康指标
 * 数据源：/api/panorama、/api/sensors、/api/health
 *
 * 【2026-10-15 修复 · 口径为什么要拆开】
 * 实测两处"读错源"的静默缺陷：
 *   ① /api/sensors 返回的是**描述符** {name,label,category,enabled}，**没有 value/severity**；
 *      本页原先把描述符直接当读数渲染 ⇒ "值"列恒为 "-"、"状态"列恒为 unknown。
 *      真实读数在 GET /api/panorama 的 data.health（含 cpu_usage / memory_usage）。
 *   ② 原实现读 /api/panorama **顶层** 的 cpu/memory。迁移到统一信封后那些键在 data 里，
 *      且真实键名是 sensor_name=cpu_usage / memory_usage，故两张指标卡也恒为 "-"。
 *   ③ /api/panorama 的 data.tag_dimensions 就是后端 _get_tag_dimensions()（单一口径，8 维），
 *      直接消费它，**不在前端重写维度表**。
 * 现在：读数只取 data.health（失败才退回 /api/health），描述符只取 /api/sensors，
 * 两者按 sensor_name / category 对齐；缺读数如实显示"—（无读数）"，不拿 0 冒充。
 * 失败一律上屏（Promise.allSettled 的 rejected 显式列出），不留静默空表。
 */
import { useEffect, useState } from 'react'
import { Activity, Cpu, MemoryStick, Battery, RefreshCw } from 'lucide-react'
import { Card, StatCard, DataTable, Loading, ErrorBox, Badge, PageHeader, hubGet } from '../components/ui'
// 【P1-front 第五批】后端 GET /api/panorama 与 GET /api/sensors 已带 X-Envelope: v2，
// 改用显式信封解析。同页的 /api/health **不在本系列**（它有 40+ 消费方，含看门狗与就绪门），
// 故仍走 hubGet 的旧解析。
import { getEnvelope } from '@/api/envelope'
// 认知闭环（元认知）状态 / 裁决 —— 方案 §7 第三批接入
import CognitiveLoopPanel from './CognitiveLoopPanel'

import {
  HEALTH,
  PANORAMA,
  SENSORS,
} from '@/api/endpoints'

/** /api/sensors 的每一项：**描述符**，不是读数 */
interface SensorDescriptor {
  name: string
  label: string
  category: string
  enabled: boolean
}

/** data.health / /api/health 的每一项：**读数** */
interface SensorReading {
  sensor_name?: string
  name?: string
  category?: string
  description?: string
  value?: number | string
  unit?: string
  severity?: string
  tags?: string[]
}

interface TagDimension {
  label: string
  values: string[]
}

interface Panorama {
  sensor_on?: number
  sensor_total?: number
  health?: SensorReading[]
  tag_dimensions?: TagDimension[]
  [k: string]: unknown
}

const SEV_CHIP: Record<string, string> = {
  green: 'border-emerald-800 bg-emerald-500/10 text-emerald-300',
  amber: 'border-amber-800 bg-amber-500/10 text-amber-300',
  red: 'border-red-800 bg-red-500/10 text-red-300',
  slate: 'border-slate-700 bg-slate-800/40 text-slate-400',
}

/** severity 配色：normal=绿、warning=琥珀、其它=红（缺读数额外走 slate） */
const sevColor = (sev?: string): 'green' | 'amber' | 'red' => (
  sev === 'normal' ? 'green' : sev === 'warning' ? 'amber' : 'red'
)

/** 把一项读数格式化成 "值+单位"；不是数字就原样呈现 */
function fmtReadingValue(r?: SensorReading): string {
  if (!r || r.value === undefined || r.value === null || r.value === '') return ''
  const v = typeof r.value === 'number'
    ? (Number.isInteger(r.value) ? String(r.value) : r.value.toFixed(1))
    : String(r.value)
  return v + (r.unit ? String(r.unit) : '')
}

/**
 * 描述符 → 读数对齐：先按 sensor_name 精确匹配，再按 category 匹配
 * （cpu 描述符 ↔ cpu_usage 读数），最后按 "name_" 前缀兜底。
 */
function findReading(readings: SensorReading[], d: SensorDescriptor): SensorReading | undefined {
  const lower = String(d.name ?? '').toLowerCase()
  const byName = (r: SensorReading) => String(r.sensor_name ?? r.name ?? '').toLowerCase()
  return (
    readings.find((r) => byName(r) === lower)
    ?? readings.find((r) => d.category && r.category === d.category)
    ?? readings.find((r) => byName(r).startsWith(lower + '_'))
  )
}

/** /api/health 返回裸数组；宽容地兼容 {data:[...]} 的情况（不猜业务键） */
function normalizeReadings(raw: unknown): SensorReading[] {
  if (Array.isArray(raw)) return raw as SensorReading[]
  if (raw && typeof raw === 'object' && Array.isArray((raw as { data?: unknown }).data)) {
    return (raw as { data: SensorReading[] }).data
  }
  return []
}

export default function PanoramaSensors() {
  const [panorama, setPanorama] = useState<Panorama | null>(null)
  const [sensors, setSensors] = useState<SensorDescriptor[]>([])
  const [healthFallback, setHealthFallback] = useState<SensorReading[]>([])
  const [loading, setLoading] = useState(true)
  const [failures, setFailures] = useState<string[]>([])
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setFailures([])
    Promise.allSettled([
      // 显式信封解析：panorama 的 data 是对象本身、sensors 的 data 是数组本身。
      getEnvelope<Panorama | null>(PANORAMA).then((d) => d ?? ({} as Panorama)),
      getEnvelope<SensorDescriptor[]>(SENSORS).then((list) => (Array.isArray(list) ? list : [])),
      // /api/health 未迁信封，是读数回退源（panorama.health 不可用时才用它）
      hubGet(HEALTH).catch((e: unknown) => { throw e }),
    ]).then(([p, s, h]) => {
      if (!alive) return
      const fails: string[] = []
      if (p.status === 'fulfilled') setPanorama(p.value)
      else fails.push('全景数据（/api/panorama）：' + String(p.reason))
      if (s.status === 'fulfilled') setSensors(s.value ?? [])
      else fails.push('传感器清单（/api/sensors）：' + String(s.reason))
      if (h.status === 'fulfilled') setHealthFallback(normalizeReadings(h.value))
      else fails.push('健康读数回退（/api/health）：' + String(h.reason))
      setFailures(fails)
      setLoading(false)
    })
    return () => { alive = false }
  }, [reload])

  // 传感器监控自动刷新（每 15s）
  useEffect(() => {
    const timer = setInterval(() => setReload((r) => r + 1), 15000)
    return () => clearInterval(timer)
  }, [])

  const readings = (panorama?.health && panorama.health.length > 0 ? panorama.health : healthFallback) ?? []
  const tagDimensions = panorama?.tag_dimensions ?? []

  const cpu = findReading(readings, { name: 'cpu', label: '', category: 'cpu', enabled: true })
  const mem = findReading(readings, { name: 'memory', label: '', category: 'memory', enabled: true })
  const batteryReading = findReading(readings, { name: 'battery', label: '', category: 'battery', enabled: true })
  const hasBattery = sensors.some((s) => s.category === 'battery') || !!batteryReading

  // 描述符按 category 分组（保留首次出现顺序）
  const groups = new Map<string, SensorDescriptor[]>()
  for (const s of sensors) {
    const key = s.category || '未分类'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(s)
  }

  const withReading = sensors.filter((s) => !!findReading(readings, s)).length

  return (
    <div className="p-6">
      <PageHeader
        title="全景感知"
        description="实时传感器状态与健康指标"
        actions={
          <button onClick={() => setReload((r) => r + 1)} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
            <RefreshCw size={12} /> 刷新
          </button>
        }
      />

      {failures.length > 0 && (
        <div className="mb-4 space-y-2" data-testid="panorama-failures">
          {failures.map((f) => <ErrorBox key={f} message={f} />)}
        </div>
      )}

      {loading ? <Loading /> : (
        <>
          <div className={`mb-4 grid grid-cols-2 gap-4 ${hasBattery ? 'md:grid-cols-5' : 'md:grid-cols-4'}`}>
            <div data-testid="stat-cpu">
              <StatCard
                label="CPU 使用率"
                value={cpu ? fmtReadingValue(cpu) : '—'}
                icon={<Cpu size={16} />}
                color={typeof cpu?.value === 'number' && cpu.value > 80 ? 'text-red-400' : 'text-cyan-400'}
              />
            </div>
            <div data-testid="stat-memory">
              <StatCard
                label="内存使用率"
                value={mem ? fmtReadingValue(mem) : '—'}
                icon={<MemoryStick size={16} />}
                color={typeof mem?.value === 'number' && mem.value > 85 ? 'text-red-400' : 'text-cyan-400'}
              />
            </div>
            <div data-testid="stat-sensor-on">
              <StatCard label="传感器在线" value={panorama?.sensor_on ?? '—'} unit={`/ ${panorama?.sensor_total ?? '—'}`} icon={<Activity size={16} />} />
            </div>
            <div data-testid="stat-sensor-total">
              <StatCard label="传感器总数" value={panorama?.sensor_total ?? '—'} icon={<Activity size={16} />} />
            </div>
            {hasBattery && (
              <div data-testid="stat-battery">
                <StatCard
                  label="电池"
                  value={batteryReading ? fmtReadingValue(batteryReading) : '—'}
                  unit={batteryReading ? undefined : '无读数'}
                  icon={<Battery size={16} />}
                  color={typeof batteryReading?.value === 'number' && batteryReading.value < 20 ? 'text-red-400' : 'text-cyan-400'}
                />
              </div>
            )}
          </div>

          <Card title={`传感器明细（${sensors.length} 项，其中 ${withReading} 项有读数）`}>
            <div data-testid="sensor-table">
              <DataTable
                data={sensors}
                keyField="name"
                empty="无传感器描述符（/api/sensors 未返回数据）"
                columns={[
                  { key: 'name', title: '传感器' },
                  { key: 'label', title: '描述', render: (r) => <span className="text-slate-400">{String(r.label ?? '')}</span> },
                  { key: 'category', title: '分类', render: (r) => <span className="text-slate-500">{String(r.category ?? '')}</span> },
                  {
                    key: 'value', title: '值',
                    render: (r) => {
                      const rd = findReading(readings, r)
                      if (!rd) return <span className="text-slate-600">—（无读数）</span>
                      return <span className="font-mono text-cyan-400">{fmtReadingValue(rd)}</span>
                    },
                  },
                  {
                    key: 'severity', title: '状态',
                    render: (r) => {
                      const rd = findReading(readings, r)
                      if (!rd) return <Badge color="slate">无读数</Badge>
                      return <Badge color={sevColor(rd.severity)}>{rd.severity ?? 'unknown'}</Badge>
                    },
                  },
                  {
                    key: 'enabled', title: '启用',
                    render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '已启用' : '已禁用'}</Badge>,
                  },
                ]}
              />
            </div>
          </Card>

          <div className="mt-4">
            <Card title="传感器分类（按 category 分组）">
              {groups.size === 0 ? (
                <div className="py-6 text-center text-sm text-slate-500">无传感器描述符</div>
              ) : (
                <div className="grid gap-3 md:grid-cols-2" data-testid="sensor-categories">
                  {Array.from(groups.entries()).map(([cat, items]) => (
                    <div key={cat} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3" data-testid={`sensor-cat-${cat}`}>
                      <div className="mb-2 flex items-center justify-between">
                        <span className="text-xs font-medium text-slate-300">{cat}</span>
                        <span className="text-[11px] text-slate-500">
                          {items.length} 个 · 在线 {items.filter((i) => i.enabled).length}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {items.map((s) => {
                          const rd = findReading(readings, s)
                          const color = rd ? sevColor(rd.severity) : 'slate'
                          return (
                            <span
                              key={s.name}
                              data-testid={`sensor-chip-${s.name}`}
                              title={`${s.label || s.name}（${s.category}）` + (rd ? ` 读数：${fmtReadingValue(rd)} [${rd.severity ?? 'unknown'}]` : ' 无读数')}
                              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] ${SEV_CHIP[color]} ${s.enabled ? '' : 'opacity-50'}`}
                            >
                              <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.enabled ? '#3fb950' : '#30363d' }} />
                              {s.label || s.name}
                              {!s.enabled && <span className="text-slate-500">（已禁用）</span>}
                              {rd ? (
                                <span className="text-slate-400">{rd.severity ?? 'unknown'}</span>
                              ) : (
                                <span className="text-slate-500">无读数</span>
                              )}
                            </span>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <div className="mt-4">
            <Card title={`感知维度（8 维标签体系，来自后端 _get_tag_dimensions）`}>
              {tagDimensions.length === 0 ? (
                <div className="py-6 text-center text-sm text-slate-500">
                  维度表不可得：/api/panorama 的 data.tag_dimensions 为空
                </div>
              ) : (
                <div className="grid gap-3 md:grid-cols-2" data-testid="tag-dimensions">
                  {tagDimensions.map((dim) => (
                    <div key={dim.label} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3" data-testid={`tag-dim-${dim.label}`}>
                      <div className="mb-2 text-xs font-medium text-slate-300">{dim.label}</div>
                      <div className="flex flex-wrap gap-1.5">
                        {dim.values.map((v) => (
                          <span key={v} className="rounded border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-400">{v}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <div className="mt-4">
            <CognitiveLoopPanel />
          </div>
        </>
      )}
    </div>
  )
}
