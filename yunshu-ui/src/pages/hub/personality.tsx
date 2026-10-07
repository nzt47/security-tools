/**
 * 人格配置 —— 从 legacy index.html 移植
 * 数据源：/api/personality（GET）、/api/personality/params|profile|reset（POST）
 * 功能：6 维度调节（语气/情感/简练/主动/幽默/同理心）+ 预设 profile + 保存
 */
import { useEffect, useState } from 'react'
import { Save, RotateCcw, User } from 'lucide-react'
import { Card, Badge, PageHeader, Loading, ErrorBox } from './components/ui'
// 【P1-front 第四批】后端 GET /api/personality 已带 X-Envelope: v2，改用显式信封解析。
// 【P1-front 第八批】同页三个 POST（params/profile/reset）也已迁移，一并改用 postEnvelope。
//   【迁移顺手修掉的一处静默缺陷】后端一向返回 `params` / `profile`，而本页原先读的是
//   `custom_params` / `current_profile`（与同文件 GET 的键名混淆）⇒ 点预设人格后本地参数
//   一直不刷新（拿到 undefined，不报错）。现按后端既有契约读 `params` / `profile`。
import { getEnvelope, postEnvelope } from '@/api/envelope';

import {
  PERSONALITY,
  PERSONALITY_PARAMS,
  PERSONALITY_PROFILE,
  PERSONALITY_RESET,
} from '@/api/endpoints';

interface Dimension { key: string; label: string; left: string; right: string }
interface Personality {
  current_profile: string
  custom_params: Record<string, number>
  dimensions: Dimension[]
  profiles: Record<string, { name: string; description: string; params: Record<string, number> }>
}

export default function PersonalityPage() {
  const [data, setData] = useState<Personality | null>(null)
  const [params, setParams] = useState<Record<string, number>>({})
  const [activeProfile, setActiveProfile] = useState('custom')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    // 【P1-front 第四批】显式信封解析：data 就是人格配置对象本身（原来靠 pickObj 猜）。
    // 显式声明可空：信封里 data 理论上可以是 null（后端契约不保证非空），
    // 下面的 ?? 就是为这一态准备的，类型也应如实反映，而不是靠断言盖掉。
    getEnvelope<Personality | null>(PERSONALITY).then((d) => {
      setData(d)
      setParams(d?.custom_params ?? {})
      setActiveProfile(d?.current_profile ?? 'custom')
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const applyProfile = async (key: string) => {
    try {
      const d = await postEnvelope<{ profile?: string; params?: Record<string, number> }>(PERSONALITY_PROFILE, { profile: key })
      if (d.params) setParams(d.params)
      if (d.profile) setActiveProfile(d.profile)
      setMsg(`已应用人格：${data?.profiles[key]?.name ?? key}`)
    } catch (e) { setError(String(e)) }
  }

  const save = async () => {
    if (saving) return
    setSaving(true)
    setMsg('')
    try {
      await postEnvelope(PERSONALITY_PARAMS, { params })
      setActiveProfile('custom')
      setMsg('人格参数已保存（自定义）')
    } catch (e) { setMsg(`保存失败：${e instanceof Error ? e.message : e}`) } finally { setSaving(false) }
  }

  const reset = async () => {
    try {
      await postEnvelope(PERSONALITY_RESET)
      load()
      setMsg('人格参数已重置')
    } catch (e) { setError(String(e)) }
  }

  if (loading) return <div className="p-6"><Loading /></div>

  return (
    <div className="p-6">
      <PageHeader
        title="人格配置"
        description="调节云枢的语气体征（语气/情感/简练/主动/幽默/同理心）"
        actions={
          <div className="flex items-center gap-2">
            <button onClick={reset} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RotateCcw size={12} /> 重置
            </button>
            <button onClick={save} disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-40">
              <Save size={12} /> {saving ? '保存中…' : '保存参数'}
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}

      {/* 预设人格 */}
      {data?.profiles && (
        <Card title="预设人格" className="mb-4">
          <div className="flex flex-wrap gap-3">
            <button
              onClick={() => applyProfile('custom')}
              className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-left transition-colors ${
                activeProfile === 'custom' ? 'border-cyan-600 bg-cyan-950/40' : 'border-slate-800 bg-slate-900/60 hover:bg-slate-800/50'
              }`}
            >
              <User size={16} className="text-cyan-400" />
              <div>
                <div className="text-sm font-medium text-slate-200">自定义</div>
                <div className="text-xs text-slate-500">手动调节各维度</div>
              </div>
            </button>
            {Object.entries(data.profiles).map(([key, p]) => (
              <button
                key={key}
                onClick={() => applyProfile(key)}
                className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-left transition-colors ${
                  activeProfile === key ? 'border-cyan-600 bg-cyan-950/40' : 'border-slate-800 bg-slate-900/60 hover:bg-slate-800/50'
                }`}
              >
                <User size={16} className={activeProfile === key ? 'text-cyan-400' : 'text-slate-500'} />
                <div>
                  <div className="text-sm font-medium text-slate-200">{p.name}</div>
                  <div className="text-xs text-slate-500">{p.description}</div>
                </div>
                {activeProfile === key && <Badge color="cyan">当前</Badge>}
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* 维度调节 */}
      <Card title="维度参数">
        <div className="space-y-4">
          {data?.dimensions.map((dim) => (
            <div key={dim.key}>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-sm text-slate-300">{dim.label}</span>
                <span className="font-mono text-xs text-cyan-400">{((params[dim.key] ?? 0.5) * 100).toFixed(0)}%</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="w-12 shrink-0 text-xs text-slate-500">{dim.left}</span>
                <input
                  type="range"
                  min={0} max={1} step={0.01}
                  value={params[dim.key] ?? 0.5}
                  onChange={(e) => {
                    setParams({ ...params, [dim.key]: Number(e.target.value) })
                    setActiveProfile('custom')
                  }}
                  className="flex-1 accent-cyan-500"
                />
                <span className="w-12 shrink-0 text-right text-xs text-slate-500">{dim.right}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
