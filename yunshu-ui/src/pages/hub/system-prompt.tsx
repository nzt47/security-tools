/**
 * 身份提示词 —— 从 legacy index.html 移植（系统提示词配置）
 * 数据源：/api/system-prompt/config（GET/POST）、/api/system-prompt/reset
 * 功能：各提示词节启停（toggle）/ 可编辑内容 / token 统计
 */
import { useEffect, useState } from 'react'
import { Save, RotateCcw, Power } from 'lucide-react'
import { Card, PageHeader, hubGet, hubPost, Loading, ErrorBox } from './components/ui'

interface PromptSection {
  key: string
  label: string
  description: string
  enabled: boolean
  custom_content?: string
  token_limit?: number
  editable?: boolean
  ui_type?: string
}

interface PromptConfig {
  sections: Record<string, PromptSection>
  summary?: {
    grand_total: number
    total_enabled_tokens: number
    total_disabled_count: number
  }
  version?: number
}

export default function SystemPromptPage() {
  const [config, setConfig] = useState<PromptConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () => {
    setLoading(true)
    hubGet('/api/system-prompt/config').then((r) => {
      const d = (r as { data?: PromptConfig }).data ?? (r as unknown as PromptConfig)
      setConfig(d)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const toggle = (key: string) => {
    if (!config?.sections[key]) return
    const next = { ...config, sections: { ...config.sections, [key]: { ...config.sections[key], enabled: !config.sections[key].enabled } } }
    setConfig(next)
  }

  const edit = (key: string, content: string) => {
    if (!config?.sections[key]) return
    const next = { ...config, sections: { ...config.sections, [key]: { ...config.sections[key], custom_content: content } } }
    setConfig(next)
  }

  const save = async () => {
    if (saving || !config) return
    setSaving(true)
    setMsg('')
    try {
      await hubPost('/api/system-prompt/config', { sections: config.sections })
      setMsg('系统提示词配置已保存')
      load()
    } catch (e) { setMsg(`保存失败：${e instanceof Error ? e.message : e}`) } finally { setSaving(false) }
  }

  const reset = async () => {
    try {
      await hubPost('/api/system-prompt/reset')
      load()
      setMsg('提示词已重置为默认')
    } catch (e) { setError(String(e)) }
  }

  const sections = config?.sections ? Object.entries(config.sections) : []

  return (
    <div className="p-6">
      <PageHeader
        title="身份提示词"
        description="系统提示词各节配置（身份/原则/技能/工具/记忆等）"
        actions={
          <div className="flex items-center gap-2">
            <button onClick={reset} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RotateCcw size={12} /> 重置
            </button>
            <button onClick={save} disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-40">
              <Save size={12} /> {saving ? '保存中…' : '保存配置'}
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}
      {loading ? <Loading /> : (
        <>
          {config?.summary && (
            <div className="mb-4 grid grid-cols-3 gap-4">
              <Card title="总计 Token"><div className="text-2xl font-semibold text-white">{config.summary.grand_total ?? 0}</div></Card>
              <Card title="启用 Token"><div className="text-2xl font-semibold text-cyan-400">{config.summary.total_enabled_tokens ?? 0}</div></Card>
              <Card title="停用节"><div className="text-2xl font-semibold text-slate-300">{config.summary.total_disabled_count ?? 0}</div></Card>
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            {sections.map(([key, s]) => (
              <Card key={key} title={`${s.label}${s.editable ? '（可编辑）' : ''}`}>
                <p className="mb-3 text-xs text-slate-500">{s.description}</p>
                <div className="mb-3 flex items-center gap-3">
                  <button
                    onClick={() => toggle(key)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs ${s.enabled ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400'}`}
                  >
                    <Power size={11} /> {s.enabled ? '启用' : '停用'}
                  </button>
                  {s.token_limit != null && s.token_limit > 0 && (
                    <span className="font-mono text-[11px] text-slate-500">上限 {s.token_limit} tok</span>
                  )}
                </div>
                {s.editable ? (
                  <textarea
                    value={s.custom_content ?? ''}
                    onChange={(e) => edit(key, e.target.value)}
                    rows={4}
                    placeholder="编辑该节提示词内容…"
                    className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-600"
                  />
                ) : (
                  <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-xs text-slate-500">
                    {s.enabled ? '已启用（自动注入）' : '已停用'}
                  </div>
                )}
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
