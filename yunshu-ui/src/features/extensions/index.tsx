/**
 * 扩展中心 —— 「插件自带 UI」与「Schema 驱动配置」的统一呈现（T3.3 + T4.2 落地）。
 *
 * 【它是 SchemaRenderer 的第二个真实消费方】第一个是插件管理页（逐个展开、按需配置）；
 * 本页把「所有声明了 schema 的插件」自动铺开成一屏，是新插件「新增即被发现」的默认落点。
 *
 * 【插件自带 UI 自动挂上】对每个 client_slot.module 调 registerPluginModule，
 * 把模块声明的界面直接渲染出来 —— 插件作者只写 public/plugins/<name>.js + 后端声明。
 *
 * 【失败必须可见】模块加载失败 / 模块没导出 register / schema 提交失败，全部上屏。
 *
 * 【本页自身也是「宿主功能自动发现」的第一个实例】它就是一个 features/<key>/index.tsx，
 * 由 featureRegistry 的 import.meta.glob 自动挂进导航，没有改 hubNav 的任何一行清单。
 */
import { useCallback, useEffect, useState } from 'react'
import * as React from 'react'
import { Puzzle, RefreshCw, Boxes, Settings2 } from 'lucide-react'
import { request } from '@/lib/apiClient'
import { PLUGINS, PLUGINS_RELOAD } from '@/api/endpoints'
import { SchemaRenderer } from '@/plugins/schema'
import { registerPluginModule, type SlotEntry } from '@/plugins/extensionHost'

export const manifest = { key: 'extensions', label: '扩展中心', icon: Puzzle, order: 900 }

interface PluginInfo {
  name: string
  version: string
  description: string
  schema: Record<string, unknown> | null
  submitUrl: string
  clientSlot: { slotId?: string; module?: string } | null
}

interface MountState { state: string; error?: string; entries: SlotEntry[] }

function normalize(raw: unknown): PluginInfo | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.name !== 'string') return null
  const cs = (r.client_slot ?? r.clientSlot) as { slotId?: string; module?: string } | null
  const submit = r.submit_url ?? r.submitUrl
  const schema = r.schema && typeof r.schema === 'object' && !Array.isArray(r.schema)
    ? (r.schema as Record<string, unknown>) : null
  return {
    name: r.name,
    version: typeof r.version === 'string' ? r.version : '',
    description: typeof r.description === 'string' ? r.description : '',
    schema,
    submitUrl: typeof submit === 'string' ? submit : '',
    clientSlot: cs && typeof cs === 'object' ? cs : null,
  }
}

export default function ExtensionsPage() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [mounted, setMounted] = useState<Record<string, MountState>>({})
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>({})

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    request<{ plugins?: unknown[] }>(PLUGINS)
      .then((d) => setPlugins(Array.isArray(d?.plugins)
        ? d.plugins.map(normalize).filter((p): p is PluginInfo => p !== null) : []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    let cancelled = false
    const withSlot = plugins.filter((p) => p.clientSlot?.module)
    void (async () => {
      for (const p of withSlot) {
        const r = await registerPluginModule(p.name, String(p.clientSlot?.module), {})
        if (!cancelled) {
          setMounted((m) => ({ ...m, [p.name]: { state: r.state, error: r.error, entries: r.entries } }))
        }
      }
    })()
    return () => { cancelled = true }
  }, [plugins])

  useEffect(() => {
    for (const p of plugins) {
      if (!p.schema || !p.submitUrl) continue
      request<Record<string, unknown>>(p.submitUrl)
        .then((cur) => {
          if (cur && typeof cur === 'object' && !Array.isArray(cur)) {
            setValues((v) => ({ ...v, [p.name]: cur }))
          }
        })
        .catch(() => { /* 读值失败 → 保留 schema default，不打扰用户（与插件管理页一致） */ })
    }
  }, [plugins])

  const submit = async (p: PluginInfo, data: Record<string, unknown>) => {
    try {
      await request(p.submitUrl, { method: 'POST', body: data })
      setNotice('已保存「' + p.name + '」的配置')
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      setNotice('保存失败：' + (m.includes('401') ? '需配置 FLASK_API_TOKEN' : m))
    }
  }

  const reload = async () => {
    try {
      await request(PLUGINS_RELOAD, { method: 'POST' })
      setNotice('插件清单已刷新')
      load()
    } catch (e) {
      setNotice('刷新失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }

  const schemaPlugins = plugins.filter((p) => p.schema && Object.keys(p.schema).length > 0)
  const clientPlugins = plugins.filter((p) => p.clientSlot?.module)

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <Puzzle size={18} className="text-cyan-400" /> 扩展中心
          </h1>
          <p className="mt-1 text-xs text-slate-500">
            插件自带界面（client_slot）与 Schema 驱动配置的自动呈现；新增插件声明后点「刷新清单」即可出现，无需改前端。
          </p>
        </div>
        <button onClick={reload}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
          <RefreshCw size={12} /> 刷新清单
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-2 text-sm text-red-300">加载插件清单失败：{error}</div>}
      {notice && <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-2 text-sm text-cyan-400">{notice}</div>}
      {loading && <div className="text-sm text-slate-500">加载中…</div>}

      <section>
        <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
          <Boxes size={14} /> 插件自带界面（{clientPlugins.length}）
        </h2>
        {clientPlugins.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 text-xs text-slate-500">
            暂无插件声明 client_slot。声明格式见后端 Plugin(client_slot=...)。
          </div>
        ) : (
          <div className="space-y-3">
            {clientPlugins.map((p) => {
              const ms = mounted[p.name]
              return (
                <div key={p.name} className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                    <span className="font-mono text-slate-200">{p.name}</span>
                    <span className="text-slate-600">{p.clientSlot?.module}</span>
                    {ms?.state === 'error' && <span className="text-red-400">加载失败：{ms.error}</span>}
                    {!ms && <span className="text-slate-600">加载中…</span>}
                    {ms?.state === 'ok' && ms.entries.length === 0 && (
                      <span className="text-amber-400">模块已加载但没有 mountToSlot 任何界面</span>
                    )}
                  </div>
                  {p.description && <p className="mb-2 text-xs text-slate-500">{p.description}</p>}
                  {(ms?.entries ?? []).map((e, i) => (
                    <div key={(e.id || 'entry') + i} className="border-t border-slate-800 pt-2">
                      {e.title && <div className="mb-1 text-xs text-slate-400">{e.icon} {e.title}</div>}
                      {e.component ? React.createElement(e.component as React.ComponentType) : null}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
          <Settings2 size={14} /> 插件配置（Schema 驱动，{schemaPlugins.length}）
        </h2>
        {schemaPlugins.length === 0 ? (
          <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 text-xs text-slate-500">暂无插件声明 schema。</div>
        ) : (
          <div className="space-y-3">
            {schemaPlugins.map((p) => (
              <details key={p.name} className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                <summary className="cursor-pointer text-sm text-slate-200">
                  {p.name}<span className="ml-2 text-xs text-slate-500">{p.description}</span>
                </summary>
                <div className="mt-3">
                  <SchemaRenderer
                    schema={p.schema as Record<string, unknown>}
                    value={values[p.name] ?? {}}
                    onChange={(v) => setValues((s) => ({ ...s, [p.name]: v }))}
                    onSubmit={p.submitUrl ? (d) => { void submit(p, d) } : undefined}
                  />
                  {!p.submitUrl && <p className="mt-2 text-xs text-slate-500">该插件未声明 submit_url，仅只读预览。</p>}
                </div>
              </details>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
