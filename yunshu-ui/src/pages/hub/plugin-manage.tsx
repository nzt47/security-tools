/**
 * 插件管理 —— 列出全部插件 + 刷新清单
 * ------------------------------------------------
 * 数据源：/api/plugins（GET manifest）、/api/plugins/reload（POST 刷新）
 * 功能：插件列表（名称/版本/描述/路由数）、单个插件路由明细、刷新清单
 */
import { useEffect, useState } from 'react'
import { Puzzle, RefreshCw, ChevronDown, Server } from 'lucide-react'
import { Card, Badge, PageHeader, hubGet, hubPost, Loading, ErrorBox, pickObj } from './components/ui'

interface PluginInfo {
  name: string
  version: string
  description: string
  routes?: string[]
  submit_url?: string
  client_slot?: { slotId?: string; module?: string } | null
}

export default function PluginManagePage() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [host, setHost] = useState<{ python?: string; flask?: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = () => {
    setLoading(true)
    hubGet('/api/plugins').then((r) => {
      const d = pickObj<{ plugins?: PluginInfo[]; host?: { python?: string; flask?: string } }>(r) ?? {}
      setPlugins(Array.isArray(d.plugins) ? d.plugins : [])
      setHost(d.host ?? null)
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const reload = async () => {
    if (refreshing) return
    setRefreshing(true)
    setMsg('')
    try {
      const r = await hubPost('/api/plugins/reload')
      const rr = r as { ok?: boolean; error?: string }
      if (rr.ok) {
        setMsg('插件清单已刷新')
        // 延迟重载（reload 返回后 manifest 更新）
        setTimeout(load, 300)
      } else {
        setMsg(`刷新失败：${rr.error ?? '未知错误'}`)
      }
    } catch (e) { setMsg(`刷新请求失败：${e instanceof Error ? e.message : e}`) } finally { setRefreshing(false) }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="插件管理"
        description={`已加载 ${plugins.length} 个插件${host?.flask ? `（Flask ${host.flask} / Python ${host.python ?? ''}）` : ''}`}
        actions={
          <div className="flex items-center gap-2">
            <button onClick={load} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <RefreshCw size={12} /> 刷新列表
            </button>
            <button
              onClick={reload}
              disabled={refreshing}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-40"
            >
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              {refreshing ? '刷新中…' : '刷新清单'}
            </button>
          </div>
        }
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{msg}</div>}

      {loading ? <Loading /> : (
        <div className="grid gap-3 md:grid-cols-2">
          {plugins.map((p) => (
            <Card key={p.name} title={p.name}>
              <p className="mb-3 flex items-center gap-2 text-xs text-slate-400">
                <Badge color="cyan">v{p.version}</Badge>
                {p.description}
              </p>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
                <Badge color="slate">{p.routes?.length ?? 0} 条路由</Badge>
                {p.submit_url && <Badge color="green">可配置</Badge>}
                {p.client_slot && <Badge color="amber">前端插槽</Badge>}
              </div>

              <button
                onClick={() => setExpanded(expanded === p.name ? null : p.name)}
                className="flex w-full items-center justify-center gap-1 rounded-md border border-slate-800 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800/60"
              >
                <ChevronDown size={11} className={`transition-transform ${expanded === p.name ? 'rotate-180' : ''}`} />
                路由明细
              </button>

              {expanded === p.name && (
                <div className="mt-2 max-h-44 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                  {(p.routes ?? []).map((rt) => (
                    <div key={rt} className="flex items-center gap-2 rounded px-2 py-0.5 font-mono text-[10.5px] text-slate-500 hover:bg-slate-800/50">
                      <Server size={9} className="shrink-0 text-slate-700" />
                      {rt}
                    </div>
                  ))}
                  {(p.routes ?? []).length === 0 && <div className="py-2 text-center text-[11px] text-slate-600">无路由</div>}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
