/**
 * 插件与扩展 —— 插件清单 + schema 配置表单 + 插件自带 UI（工作台「会话与配置 / 插件与扩展」）
 * -------------------------------------------------
 * 【2026-10-15 合并】原「清单与配置 / 扩展呈现」两 Tab 合为一页：
 *   · 两者同读 /api/plugins，「扩展呈现」的 schema 配置部分与本页完全重复；
 *   · 「扩展呈现」唯一独有能力是自动挂载插件自带 UI（client_slot → registerPluginModule），
 *     现已并入本页每个插件卡片（声明了 client_slot 的插件才会出现「插件界面」区块）。
 * 仍是插件的唯一导航入口（禁止双入口）。
 * 数据源：/api/plugins（GET manifest）、/api/plugins/reload（POST 刷新，受令牌保护）
 *
 * 能力（任务 5 / P1-4：并入 legacy src/plugins/ 的 PluginPanel + SchemaRenderer +
 * ApiTokenField，不迁移 SlotHost/profile.json 插槽宿主体系）：
 * 1. 插件卡片列表：名称/版本/描述、路由数、「可配置」/「前端插槽」徽标；
 * 2. 每个插件「配置」入口：点击展开 manifest schema 驱动的表单（复用
 *    @/plugins/schema 的 SchemaRenderer；wire 字段兼容 schema 与 config_schema）；
 *    - manifest 无 schema → 「该插件暂无可配置界面（未声明 schema）」；
 *    - 有 schema 但无 submit_url → 只读预览 + 「暂不支持在线修改」提示；
 *    - 有 schema + submit_url → 展开时 GET submit_url 预填当前生效值（失败静默
 *      回退 schema default），提交 POST submit_url，成功后重新读取当前值；
 * 3. 令牌：FLASK_API_TOKEN 启用时 reload / 部分提交端点返回 401 → 明确提示
 *    「请在『API 令牌』中填入 FLASK_API_TOKEN 后重试」（文案与 legacy PluginPanel
 *    一致）；ApiTokenField 输入后经 lib/apiToken 持久化，apiClient.request 自动注入
 *    Authorization 头，无需重复输入。
 * 4. 【2026-10-18】「从市场安装插件包」收敛到本页：插件包的安装入口只在这里
 *    （抽屉内搜索 /api/extensions/market/search?type=plugin → POST
 *    /api/extensions/install {type:'plugin', source:id}）。扩展市场只保留发现/浏览 ——
 *    同一件事两个入口，迟早会出现"在 A 装完去 B 看不见"的分裂。
 */
import { createElement, useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown, Loader2, PackagePlus, RefreshCw, Search, Server, Settings2, X } from 'lucide-react'
import { Card, Badge, PageHeader, Loading, ErrorBox } from './components/ui'
import InstalledExtensions from './extensions/installed-extensions'
import { SchemaRenderer } from '@/plugins/schema'
import { ApiTokenField } from '@/plugins/ApiTokenField'
import { ApiError, request } from '@/lib/apiClient'
import { registerPluginModule, type SlotEntry } from '@/plugins/extensionHost'

import {
  EXTENSIONS_INSTALL,
  EXTENSIONS_MARKET_SEARCH,
  PLUGINS,
  PLUGINS_RELOAD,
} from '@/api/endpoints';

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- JSON Schema 动态值无静态类型
type Json = Record<string, any>

interface PluginInfo {
  name: string
  version: string
  description: string
  schema: Json | null
  routes: string[]
  submitUrl?: string
  clientSlot?: { slotId?: string; module?: string } | null
}

interface Notice {
  kind: 'ok' | 'err'
  text: string
}

/** 市场搜索命中的插件包（/api/extensions/market/search?type=plugin） */
interface MarketPluginRow {
  id?: string
  name?: string
  description?: string
  ext_type?: string
  source?: string
  market_type?: string
}

/** 把单个 manifest 条目归一化为 PluginInfo（wire snake_case → 前端 camelCase） */
function normalizePlugin(raw: unknown): PluginInfo | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  if (typeof r.name !== 'string' || r.name.length === 0) return null

  // manifest 输出契约字段为 schema（空 dict = 未声明）；兼容 config_schema 拼写
  const schemaRaw = r.schema ?? r.config_schema ?? null
  const schema =
    schemaRaw && typeof schemaRaw === 'object' && Object.keys(schemaRaw as Record<string, unknown>).length > 0
      ? (schemaRaw as Json)
      : null

  const submitRaw = r.submit_url ?? r.submitUrl ?? ''
  const csRaw = r.client_slot ?? r.clientSlot ?? null
  const cs =
    csRaw && typeof csRaw === 'object'
      ? (csRaw as { slotId?: unknown; module?: unknown })
      : null

  return {
    name: r.name,
    version: typeof r.version === 'string' ? r.version : '0.0.0',
    description: typeof r.description === 'string' ? r.description : '',
    schema,
    routes: Array.isArray(r.routes) ? r.routes.filter((x): x is string => typeof x === 'string') : [],
    submitUrl: typeof submitRaw === 'string' ? submitRaw : '',
    clientSlot:
      cs && typeof cs.slotId === 'string' && typeof cs.module === 'string'
        ? { slotId: cs.slotId, module: cs.module }
        : null,
  }
}

/** 插件是否具备「schema 表单 + 提交端点」的完整可配置闭环 */
function isConfigurable(plugin: PluginInfo): boolean {
  return !!plugin.schema && !!plugin.submitUrl
}

/** 从后端当前值里挑选 schema 已声明字段（防止多余字段污染表单值） */
function pickSchemaFields(schema: Json, current: Record<string, unknown>): Json {
  const picked: Json = {}
  const props =
    schema && typeof schema === 'object' && schema.properties && typeof schema.properties === 'object'
      ? (schema.properties as Record<string, unknown>)
      : {}
  for (const key of Object.keys(props)) {
    if (Object.prototype.hasOwnProperty.call(current, key)) picked[key] = current[key]
  }
  return picked
}

export default function PluginManagePage() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [host, setHost] = useState<{ python?: string; flask?: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // 卡片展开态：配置面板 / 路由明细（同一时间各最多展开一个）
  const [config, setConfig] = useState<PluginInfo | null>(null)
  const [routeName, setRouteName] = useState<string | null>(null)

  /** 插件自带 UI 的挂载结果（client_slot → registerPluginModule；原「扩展呈现」能力） */
  const [mounted, setMounted] = useState<Record<string, { state: string; error?: string; entries: SlotEntry[] }>>({})

  // 「从市场安装插件包」抽屉（2026-10-18：插件包安装入口收敛到本页）
  const [marketOpen, setMarketOpen] = useState(false)
  const [marketQ, setMarketQ] = useState('')
  const [marketRows, setMarketRows] = useState<MarketPluginRow[]>([])
  const [marketBusy, setMarketBusy] = useState('')
  const [marketNotice, setMarketNotice] = useState<Notice | null>(null)

  // 当前配置面板的表单态
  const [values, setValues] = useState<Json>({})
  const [valuesLoading, setValuesLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [formMsg, setFormMsg] = useState('')
  /** 当前配置面板对应的插件名（异步竞态守卫） */
  const configNameRef = useRef<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    request<{ plugins?: unknown[]; host?: { python?: string; flask?: string } }>(PLUGINS)
      .then((d) => {
        setPlugins(
          Array.isArray(d?.plugins)
            ? d.plugins.map(normalizePlugin).filter((p): p is PluginInfo => p !== null)
            : [],
        )
        setHost(d?.host ?? null)
      })
      .catch((e) => { setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  // 【2026-10-15 合并「扩展呈现」】声明 client_slot 的插件自动挂载自带 UI：
  // 插件作者只写 public/plugins/<name>.js + 后端声明，新增即被发现，无需改前端。
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

  /** 拉取当前配置面板插件的生效值（仅 schema 声明字段；失败静默回退 default） */
  const loadCurrentValues = useCallback(async (plugin: PluginInfo) => {
    if (!plugin.schema || !plugin.submitUrl) return
    configNameRef.current = plugin.name
    setValuesLoading(true)
    setValues({})
    try {
      const current = await request<Record<string, unknown>>(plugin.submitUrl)
      if (configNameRef.current !== plugin.name) return // 已切换插件，丢弃过期响应
      if (current && typeof current === 'object' && !Array.isArray(current)) {
        setValues(pickSchemaFields(plugin.schema, current))
      }
    } catch {
      /* 读值失败 → 保留 schema default 填充，不打扰用户（与 PluginPanel 行为一致） */
    } finally {
      if (configNameRef.current === plugin.name) setValuesLoading(false)
    }
  }, [])

  const openConfig = useCallback(
    (plugin: PluginInfo) => {
      if (config?.name === plugin.name) {
        // 再次点击同一插件 → 收起
        configNameRef.current = null
        setConfig(null)
        return
      }
      configNameRef.current = plugin.name
      setConfig(plugin)
      setFormError('')
      setFormMsg('')
      setSubmitting(false)
      if (plugin.schema && plugin.submitUrl) {
        void loadCurrentValues(plugin)
      } else {
        setValues({})
        setValuesLoading(false)
      }
    },
    [config, loadCurrentValues],
  )

  const reload = async () => {
    if (refreshing) return
    setRefreshing(true)
    setNotice(null)
    try {
      const r = await request<{ ok?: boolean; error?: string }>(PLUGINS_RELOAD, { method: 'POST' })
      if (r && r.ok === false) {
        setNotice({ kind: 'err', text: `刷新失败：${r.error ?? '未知错误'}` })
      } else {
        setNotice({ kind: 'ok', text: '插件清单已刷新' })
        // reload 返回后 manifest 已重建，延迟重载列表
        window.setTimeout(load, 300)
      }
    } catch (e) {
      const is401 = e instanceof ApiError && e.status === 401
      setNotice({
        kind: 'err',
        text: is401
          ? '刷新插件清单失败（401 未授权）：请在「API 令牌」中填入 FLASK_API_TOKEN 后重试'
          : `刷新请求失败：${e instanceof Error ? e.message : String(e)}`,
      })
    } finally {
      setRefreshing(false)
    }
  }

  /** SchemaRenderer 提交：POST submit_url；401 → FLASK_API_TOKEN 指引（与仓库既有文案一致） */
  const handleSubmit = useCallback(
    async (formValues: Json) => {
      if (!config?.submitUrl) return
      const name = config.name
      setSubmitting(true)
      setFormError('')
      setFormMsg('')
      try {
        const res = await request<{ ok?: boolean; error?: string }>(config.submitUrl, {
          method: 'POST',
          body: formValues,
        })
        if (res && res.ok === false) {
          setFormError(res.error || `「${name}」配置提交失败`)
        } else {
          setFormMsg(`「${name}」配置已生效`)
          // 提交成功后重新读取当前值（后端确认的生效状态可见）
          await loadCurrentValues(config)
        }
      } catch (e) {
        const is401 = e instanceof ApiError && e.status === 401
        setFormError(
          is401
            ? `「${name}」配置提交失败（401 未授权）：请在「API 令牌」中填入 FLASK_API_TOKEN 后重试`
            : e instanceof Error
              ? e.message
              : String(e),
        )
      } finally {
        setSubmitting(false)
      }
    },
    [config, loadCurrentValues],
  )

  /** 市场搜索插件包（type=plugin）——只搜不装，安装是下一步的显式动作 */
  const searchMarketPlugins = useCallback(async (q: string) => {
    setMarketBusy('search')
    setMarketNotice(null)
    try {
      const params = '?type=plugin' + (q.trim() ? '&q=' + encodeURIComponent(q.trim()) : '')
      const res = await request<{
        ok?: boolean
        error?: string
        builtin?: MarketPluginRow[]
        community?: MarketPluginRow[]
        github?: MarketPluginRow[]
      }>(EXTENSIONS_MARKET_SEARCH + params)
      if (res && res.ok === false) {
        setMarketRows([])
        setMarketNotice({ kind: 'err', text: `搜索失败：${res.error ?? '后端返回 ok=false'}` })
        return
      }
      const rows = [...(res?.builtin ?? []), ...(res?.community ?? []), ...(res?.github ?? [])]
      setMarketRows(rows)
      if (rows.length === 0) setMarketNotice({ kind: 'ok', text: '市场里没有匹配的插件包' })
    } catch (e) {
      setMarketRows([])
      setMarketNotice({ kind: 'err', text: `搜索失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setMarketBusy('')
    }
  }, [])

  const openMarket = () => {
    setMarketOpen(true)
    setMarketNotice(null)
    void searchMarketPlugins('')
  }

  /** 装插件包：{type:'plugin', source:id}；成功后重载清单（新插件要等 manifest 重建） */
  const installMarketPlugin = async (id: string) => {
    setMarketBusy('install:' + id)
    setMarketNotice(null)
    try {
      const r = await request<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_INSTALL, {
        method: 'POST',
        body: { type: 'plugin', source: id },
      })
      if (r && r.ok === false) {
        setMarketNotice({ kind: 'err', text: `安装失败：${r.error ?? r.message ?? '后端返回 ok=false'}` })
        return
      }
      setMarketNotice({ kind: 'ok', text: `已安装插件包 ${id}；清单稍后自动刷新` })
      window.setTimeout(load, 300)
    } catch (e) {
      setMarketNotice({ kind: 'err', text: `安装失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setMarketBusy('')
    }
  }

  const renderConfigBody = (plugin: PluginInfo) => {
    if (!plugin.schema) {
      return (
        <p className="py-1 text-center text-xs text-slate-500" data-testid={`plugin-no-schema-${plugin.name}`}>
          该插件暂无可配置界面（未声明 schema）。
        </p>
      )
    }
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-medium text-slate-300">配置项（schema 驱动）</span>
          {plugin.submitUrl && (
            <code className="break-all font-mono text-[10px] text-slate-600">提交端点：{plugin.submitUrl}</code>
          )}
        </div>
        {valuesLoading && (
          <p className="text-xs text-slate-500" data-testid={`plugin-values-loading-${plugin.name}`}>
            正在读取当前配置…
          </p>
        )}
        <SchemaRenderer
          schema={plugin.schema}
          value={values}
          onChange={setValues}
          onSubmit={plugin.submitUrl ? handleSubmit : undefined}
        />
        {!plugin.submitUrl && (
          <p
            className="border-t border-slate-800/70 pt-2 text-xs text-amber-400/80"
            data-testid={`plugin-no-submit-${plugin.name}`}
          >
            该插件暂不支持在线修改（未声明提交端点）。
          </p>
        )}
        {submitting && (
          <p className="text-xs text-slate-500" data-testid={`plugin-submitting-${plugin.name}`}>
            提交中…
          </p>
        )}
        {formError && (
          <p role="alert" className="text-xs text-red-400" data-testid={`plugin-config-error-${plugin.name}`}>
            {formError}
          </p>
        )}
        {formMsg && (
          <p className="text-xs text-emerald-400" data-testid={`plugin-config-msg-${plugin.name}`}>
            {formMsg}
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="p-6">
      <PageHeader
        title="插件与扩展"
        description={`已加载 ${plugins.length} 个插件${host?.flask ? `（Flask ${host.flask} / Python ${host.python ?? ''}）` : ''}；逐插件可展开配置、路由明细与插件自带界面`}
        actions={
          <div className="flex items-center gap-2">
            <div className="w-60" data-testid="plugin-api-token-wrap">
              <ApiTokenField />
            </div>
            <button
              onClick={load}
              className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
            >
              <RefreshCw size={12} /> 刷新列表
            </button>
            <button
              onClick={openMarket}
              data-testid="plugin-market-open"
              className="flex items-center gap-1.5 rounded-lg border border-cyan-800 px-3 py-1.5 text-xs text-cyan-300 hover:bg-cyan-950/40"
            >
              <PackagePlus size={12} /> 从市场安装插件包
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
      {notice && (
        <div
          className={`mb-4 rounded-lg border px-4 py-2 text-sm ${
            notice.kind === 'ok'
              ? 'border-slate-800 bg-slate-900 text-cyan-400'
              : 'border-red-900/60 bg-red-950/40 text-red-400'
          }`}
          role={notice.kind === 'err' ? 'alert' : undefined}
        >
          {notice.text}
        </div>
      )}

      {loading ? <Loading /> : (
        <div className="grid gap-3 md:grid-cols-2">
          {plugins.map((p) => (
            <Card key={p.name} title={p.name}>
              <div className="flex flex-col gap-3" data-testid={`plugin-card-${p.name}`}>
                <p className="flex items-center gap-2 text-xs text-slate-400">
                  <Badge color="cyan">v{p.version}</Badge>
                  {p.description}
                </p>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <Badge color="slate">{p.routes?.length ?? 0} 条路由</Badge>
                  {isConfigurable(p) && <Badge color="green">可配置</Badge>}
                  {p.clientSlot && <Badge color="amber">前端插槽</Badge>}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    data-testid={`plugin-config-toggle-${p.name}`}
                    onClick={() => openConfig(p)}
                    className={`flex flex-1 items-center justify-center gap-1 rounded-md border px-3 py-1.5 text-[11px] transition-colors ${
                      config?.name === p.name
                        ? 'border-cyan-800 bg-cyan-950/40 text-cyan-300'
                        : 'border-slate-800 text-slate-400 hover:bg-slate-800/60'
                    }`}
                  >
                    <Settings2 size={11} className={config?.name === p.name ? 'text-cyan-400' : ''} />
                    {config?.name === p.name ? '收起配置' : '配置'}
                  </button>
                  <button
                    data-testid={`plugin-routes-toggle-${p.name}`}
                    onClick={() => setRouteName(routeName === p.name ? null : p.name)}
                    className="flex flex-1 items-center justify-center gap-1 rounded-md border border-slate-800 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800/60"
                  >
                    <ChevronDown size={11} className={`transition-transform ${routeName === p.name ? 'rotate-180' : ''}`} />
                    {routeName === p.name ? '收起明细' : '路由明细'}
                  </button>
                </div>

                {config?.name === p.name && config && (
                  <div
                    className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"
                    data-testid={`plugin-config-panel-${p.name}`}
                  >
                    {renderConfigBody(config)}
                  </div>
                )}

                {routeName === p.name && (
                  <div
                    className="max-h-44 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950/60 p-2"
                    data-testid={`plugin-routes-${p.name}`}
                  >
                    {(p.routes ?? []).map((rt) => (
                      <div
                        key={rt}
                        className="flex items-center gap-2 rounded px-2 py-0.5 font-mono text-[10.5px] text-slate-500 hover:bg-slate-800/50"
                      >
                        <Server size={9} className="shrink-0 text-slate-700" />
                        {rt}
                      </div>
                    ))}
                    {(p.routes ?? []).length === 0 && (
                      <div className="py-2 text-center text-[11px] text-slate-600">无路由</div>
                    )}
                  </div>
                )}

                {/* 插件自带 UI（原「扩展呈现」）：声明 client_slot 才渲染；失败必须上屏 */}
                {p.clientSlot?.module && (
                  <div
                    className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"
                    data-testid={`plugin-client-slot-${p.name}`}
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                      <span className="font-medium text-slate-300">插件界面</span>
                      <span className="font-mono text-slate-600">{p.clientSlot.module}</span>
                      {!mounted[p.name] && <span className="text-slate-600">加载中…</span>}
                      {mounted[p.name]?.state === 'error' && (
                        <span className="text-red-400">加载失败：{mounted[p.name]?.error}</span>
                      )}
                      {mounted[p.name]?.state === 'ok' && mounted[p.name]?.entries.length === 0 && (
                        <span className="text-amber-400">模块已加载但没有 mountToSlot 任何界面</span>
                      )}
                    </div>
                    {(mounted[p.name]?.entries ?? []).map((e, i) => (
                      <div key={(e.id || 'entry') + i} className="border-t border-slate-800 pt-2">
                        {e.title && <div className="mb-1 text-[11px] text-slate-400">{e.icon} {e.title}</div>}
                        {e.component ? createElement(e.component as React.ComponentType) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* ── 已安装插件包（扩展系统）：启停 / 配置 / 卸载 ──
          原在「扩展市场」页的已安装管理；市场整页删除（2026-10-20）后回到插件包归属页。 */}
      <div className="mt-4">
        <InstalledExtensions
          types={['plugin']}
          title="已安装插件包（扩展系统）"
          hint="从市场/外部安装的插件包。启停只影响该包；卸载会删除扩展登记，不可逆（二次确认）。"
          emptyText="还没有通过扩展系统安装的插件包；上方「从市场安装插件包」是唯一安装入口。"
        />
      </div>

      {/* ── 从市场安装插件包（插件包安装的唯一入口；市场只发现/浏览） ── */}
      {marketOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-6" onClick={() => setMarketOpen(false)}>
          <div
            className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 p-4"
            onClick={(e) => e.stopPropagation()}
            data-testid="plugin-market-drawer"
          >
            <div className="mb-3 flex items-center gap-2">
              <PackagePlus size={14} className="text-cyan-400" />
              <h3 className="text-sm font-semibold text-slate-100">从市场安装插件包</h3>
              <span className="text-[10px] text-slate-500">只列 `type=plugin`；装完自动刷新清单</span>
              <button
                type="button"
                onClick={() => setMarketOpen(false)}
                className="ml-auto rounded-md border border-slate-700 p-1 text-slate-400 hover:bg-slate-800"
                aria-label="关闭插件市场抽屉"
              >
                <X size={12} />
              </button>
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <input
                className="w-64 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600"
                placeholder="关键词（留空 = 全部插件包）"
                value={marketQ}
                onChange={(e) => setMarketQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void searchMarketPlugins(marketQ) }}
                aria-label="插件市场搜索关键词"
              />
              <button
                type="button"
                onClick={() => void searchMarketPlugins(marketQ)}
                disabled={marketBusy !== ''}
                className="flex items-center gap-1 rounded-md border border-cyan-800 px-2 py-1.5 text-xs text-cyan-300 hover:bg-cyan-950/40 disabled:opacity-40"
                aria-label="搜索插件包"
              >
                {marketBusy === 'search' ? <Loader2 size={11} className="animate-spin" /> : <Search size={11} />} 搜索
              </button>
            </div>
            {marketNotice && (
              <div
                className={`mb-2 rounded-md border px-2 py-1.5 text-[11px] ${marketNotice.kind === 'ok' ? 'border-slate-700 bg-slate-950 text-cyan-300' : 'border-red-900/60 bg-red-950/40 text-red-300'}`}
                role={marketNotice.kind === 'err' ? 'alert' : undefined}
              >
                {marketNotice.text}
              </div>
            )}
            {marketRows.length === 0 && marketBusy !== 'search' && (
              <div className="py-6 text-center text-[11px] text-slate-500">没有可安装的插件包。</div>
            )}
            <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
              {marketRows.map((m) => {
                const id = m.id ?? ''
                return (
                  <div key={'market-plugin-' + id} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                    <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                    <span className="text-slate-300">{m.name ?? ''}</span>
                    <span className="truncate text-[10px] text-slate-500">{m.description ?? ''}</span>
                    <span className="ml-auto flex items-center gap-1.5">
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">
                        {m.market_type ?? m.source ?? 'plugin'}
                      </span>
                      <button
                        type="button"
                        onClick={() => void installMarketPlugin(id)}
                        disabled={marketBusy !== ''}
                        aria-label={'安装插件包 ' + id}
                        className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                      >
                        {marketBusy === 'install:' + id ? <Loader2 size={11} className="animate-spin" /> : <PackagePlus size={11} />} 安装
                      </button>
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
