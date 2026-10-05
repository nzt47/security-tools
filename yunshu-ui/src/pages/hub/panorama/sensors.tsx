/**
 * 全景感知 —— 传感器实时状态 + 健康指标
 * 数据源：/api/panorama、/api/sensors、/api/health
 */
import { useEffect, useState } from 'react'
import { Activity, Cpu, MemoryStick, Battery, RefreshCw } from 'lucide-react'
import {  Card, StatCard, DataTable, Loading, ErrorBox, Badge, PageHeader, hubGet } from '../components/ui'
// 【P1-front 第五批】后端 GET /api/panorama 与 GET /api/sensors 已带 X-Envelope: v2，
// 改用显式信封解析。同页的 /api/health **不在本系列**（它有 40+ 消费方，含看门狗与就绪门），
// 故仍走 hubGet 的旧解析。
import { getEnvelope } from '@/api/envelope'

import {
  HEALTH,
  PANORAMA,
  SENSORS,
} from '@/api/endpoints';

interface SensorReading {
  sensor_name: string
  description: string
  value?: number | string
  severity?: string
  tags?: string[]
}

interface Panorama {
  sensor_on: number
  sensor_total: number
  cpu?: number
  memory?: number
  [k: string]: unknown
}

export default function PanoramaSensors() {
  const [panorama, setPanorama] = useState<Panorama | null>(null)
  const [sensors, setSensors] = useState<SensorReading[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      // 【P1-front 第五批】显式信封解析：panorama 的 data 是对象本身、sensors 的 data 是数组本身。
      getEnvelope<Panorama | null>(PANORAMA).then((d) => d ?? ({} as Panorama)),
      getEnvelope<SensorReading[]>(SENSORS).then((list) => (Array.isArray(list) ? list : [])),
      hubGet(HEALTH).catch(() => null),
    ]).then(([p, s]) => {
      if (p.status === 'fulfilled') setPanorama(p.value)
      if (s.status === 'fulfilled') setSensors(s.value ?? [])
      setLoading(false)
    }).catch((e) => {
      setError(String(e))
      setLoading(false)
    })
  }, [reload])

  // 传感器监控自动刷新（每 15s）
  useEffect(() => {
    const timer = setInterval(() => setReload((r) => r + 1), 15000)
    return () => clearInterval(timer)
  }, [])

  const sevColor = (sev?: string) => (sev === 'normal' ? 'green' : sev === 'warning' ? 'amber' : 'red')

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

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="传感器在线" value={panorama?.sensor_on ?? '-'} unit={`/ ${panorama?.sensor_total ?? '-'}`} icon={<Activity size={16} />} />
            <StatCard label="CPU 使用率" value={panorama?.cpu != null ? `${Number(panorama.cpu).toFixed(1)}%` : '-'} icon={<Cpu size={16} />} color={Number(panorama?.cpu ?? 0) > 80 ? 'text-red-400' : 'text-cyan-400'} />
            <StatCard label="内存使用率" value={panorama?.memory != null ? `${Number(panorama.memory).toFixed(1)}%` : '-'} icon={<MemoryStick size={16} />} color={Number(panorama?.memory ?? 0) > 85 ? 'text-red-400' : 'text-cyan-400'} />
            <StatCard label="传感器总数" value={panorama?.sensor_total ?? '-'} icon={<Battery size={16} />} />
          </div>

          <Card title={`传感器明细（${sensors.length}）`}>
            <DataTable
              data={sensors}
              keyField="sensor_name"
              columns={[
                { key: 'sensor_name', title: '传感器' },
                { key: 'description', title: '描述', render: (r) => <span className="text-slate-400">{String(r.description ?? '')}</span> },
                {
                  key: 'value', title: '值',
                  render: (r) => (
                    <span className="font-mono text-cyan-400">
                      {typeof r.value === 'number' ? Number(r.value).toFixed(2) : String(r.value ?? '-')}
                    </span>
                  ),
                },
                {
                  key: 'severity', title: '状态',
                  render: (r) => <Badge color={sevColor(r.severity as string)}>{r.severity ?? 'unknown'}</Badge>,
                },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  )
}
