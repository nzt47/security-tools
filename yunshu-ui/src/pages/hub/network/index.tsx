/**
 * 网络配置 —— 网络参数配置界面
 * 数据源：/api/network-config、/api/apply-network-config、/api/llm/instances
 */
import { useEffect, useState } from 'react'
import { Globe, Save, RotateCcw } from 'lucide-react'
import { Card, Loading, ErrorBox, PageHeader, hubGet, hubPost, unwrap } from '../components/ui'

export default function NetworkPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    hubGet('/api/network-config').then((r) => {
      setConfig(unwrap<Record<string, unknown>>(r as Record<string, unknown>) ?? {})
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const save = async () => {
    if (!config || saving) return
    setSaving(true)
    setMsg('')
    try {
      await hubPost('/api/network-config', config)
      await hubPost('/api/apply-network-config', {})
      setMsg('网络配置已保存并应用')
    } catch (e) {
      setMsg(`保存失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    try {
      await hubPost('/api/network-config/reset')
      load()
      setMsg('配置已重置')
    } catch (e) { setError(String(e)) }
  }

  const setVal = (key: string, v: unknown) => setConfig((c) => ({ ...(c ?? {}), [key]: v }))

  return (
    <div className="p-6">
      <PageHeader
        title="网络配置"
        description="LLM 与网络参数配置"
        actions={
          <div className="flex items-center gap-2">
            <button onClick={reset} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RotateCcw size={12} /> 重置
            </button>
            <button onClick={save} disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-40">
              <Save size={12} /> {saving ? '保存中…' : '保存并应用'}
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}
      {loading ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="LLM 配置">
            <div className="space-y-3">
              {(['provider', 'model', 'api_endpoint', 'timeout'] as const).map((key) => (
                <div key={key}>
                  <label className="mb-1 block text-xs text-slate-500">{key}</label>
                  <input
                    value={String(config?.[key] ?? '')}
                    onChange={(e) => setVal(key, e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-600"
                  />
                </div>
              ))}
              <div>
                <label className="mb-1 block text-xs text-slate-500">api_key（已配置则显示脱敏）</label>
                <input
                  value={String(config?.api_key ?? '')}
                  onChange={(e) => setVal('api_key', e.target.value)}
                  type="password"
                  placeholder="sk-…"
                  className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-600"
                />
              </div>
            </div>
          </Card>

          <Card title="网络与搜索引擎">
            <div className="space-y-3">
              {(['default_engine', 'user_agent', 'timeout', 'max_retries'] as const).map((key) => (
                <div key={key}>
                  <label className="mb-1 block text-xs text-slate-500">{key}</label>
                  <input
                    value={String(config?.[key] ?? '')}
                    onChange={(e) => setVal(key, e.target.value)}
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-600"
                  />
                </div>
              ))}
              <div className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs text-slate-500">
                <Globe size={14} className="text-cyan-500" />
                完整配置项见 /api/network-config 原始返回
              </div>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
