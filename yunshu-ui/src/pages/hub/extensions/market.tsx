/**
 * 扩展市场 —— 已安装扩展的管理 + 内置/社区扩展的发现与安装（/api/extensions/*）
 * ------------------------------------------------------------------
 * 与「插件清单」的区别（同一导航项下的两个 Tab，不建第二个入口）：
 *   · 插件清单 = /api/plugins 的 manifest 与 schema 配置（我们自己的插件体系）；
 *   · 扩展市场 = /api/extensions 的**可安装扩展**（技能 / MCP / 通道 / 插件包）：
 *     **发现**（内置/社区/GitHub 索引）+ **已安装管理（含卸载）**；安装入口按类型收敛：
 *     技能 → 技能中心「按来源安装」，插件 → 插件清单，MCP/通道 → 各自页面。
 *     内置项本身不在这里装/启停 —— 它们各自的归属页见页面顶部的指路文案。
 *
 * 这 11 条端点此前在前端一条都没接线（2026-10-18 实测），故整页新建。
 *
 * 【为什么把「安装」和「卸载」放在同一行、且卸载要二次确认】
 * 安装可逆（卸载即可），卸载对文件轨独占的扩展是**不可逆删除**（后端 force 只认
 * 布尔字面量 true）。所以卸载走两步：先点「卸载」，展开确认条，再点一次。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, Download, Loader2, Plug, RefreshCw, Search,
  Trash2, TriangleAlert,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  EXTENSIONS_CONFIGURE,
  EXTENSIONS_INSTALL,
  EXTENSIONS_INSTALLED,
  EXTENSIONS_LIST,
  EXTENSIONS_MARKET_RECOMMEND,
  EXTENSIONS_MARKET_REFRESH,
  EXTENSIONS_MARKET_SEARCH,
  EXTENSIONS_TOGGLE,
  EXTENSIONS_UNINSTALL,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

interface ExtRow {
  ext_id?: string
  ext_type?: string
  name?: string
  description?: string
  version?: string
  enabled?: boolean
  status?: string
  source?: string
  config?: Record<string, unknown>
}

interface MarketRow {
  id?: string
  name?: string
  description?: string
  ext_type?: string
  market_type?: string
  source?: string
  builtin?: boolean
  recommended?: boolean
}

const TYPES = ['skill', 'mcp', 'channel', 'plugin'] as const
const TYPE_LABEL: Record<string, string> = {
  skill: '技能',
  mcp: 'MCP 服务',
  channel: '通道',
  plugin: '插件包',
}

export default function ExtensionsMarket() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [list, setList] = useState<ExtRow[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [recs, setRecs] = useState<MarketRow[]>([])
  const [results, setResults] = useState<Record<string, MarketRow[]>>({})

  const [q, setQ] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [confirmUninstall, setConfirmUninstall] = useState('')
  const [configTarget, setConfigTarget] = useState<ExtRow | null>(null)
  const [configText, setConfigText] = useState('')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string; message?: string }) =>
    setErr(label + '：' + (r?.error || r?.message || '后端返回 ok=false'))

  const loadList = useCallback(async () => {
    setBusy('list'); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; error?: string; extensions?: ExtRow[] }>(EXTENSIONS_LIST)
      if (r?.ok === false) { bad('读取已安装扩展失败', r); return }
      setList(r?.extensions ?? [])
    } catch (e) { fail('读取已安装扩展失败', e) } finally { setBusy('') }
  }, [])

  const loadInstalled = useCallback(async () => {
    setBusy('installed'); setErr('')
    try {
      const r = await hubGet<Record<string, unknown>>(EXTENSIONS_INSTALLED)
      if ((r as { ok?: boolean })?.ok === false) { bad('读取扩展分类统计失败', r as { error?: string }); return }
      const next: Record<string, number> = {}
      for (const key of ['skills', 'plugins', 'channels', 'mcp_services',
        'store_skills', 'store_plugins', 'store_channels', 'store_mcp']) {
        const v = r?.[key]
        next[key] = Array.isArray(v) ? v.length : 0
      }
      setCounts(next)
    } catch (e) { fail('读取扩展分类统计失败', e) } finally { setBusy('') }
  }, [])

  const loadRecommend = useCallback(async () => {
    setBusy('recommend'); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; error?: string; recommendations?: MarketRow[] }>(EXTENSIONS_MARKET_RECOMMEND + '?limit=6')
      if (r?.ok === false) { bad('读取推荐失败', r); return }
      setRecs(r?.recommendations ?? [])
    } catch (e) { fail('读取推荐失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => {
    void loadList(); void loadInstalled(); void loadRecommend()
  }, [loadList, loadInstalled, loadRecommend])

  const search = async () => {
    setBusy('search'); setErr(''); setMsg('')
    try {
      const params = '?q=' + encodeURIComponent(q) + (typeFilter ? '&type=' + encodeURIComponent(typeFilter) : '')
      const r = await hubGet<Record<string, unknown>>(EXTENSIONS_MARKET_SEARCH + params)
      if ((r as { ok?: boolean })?.ok === false) { bad('搜索失败', r as { error?: string }); return }
      const out: Record<string, MarketRow[]> = {}
      for (const key of ['builtin', 'community', 'github']) {
        out[key] = Array.isArray(r?.[key]) ? (r[key] as MarketRow[]) : []
      }
      setResults(out)
      setMsg('搜索完成：' + Object.values(out).reduce((n, v) => n + v.length, 0) + ' 条')
    } catch (e) { fail('搜索失败', e) } finally { setBusy('') }
  }

  const refreshIndex = async () => {
    setBusy('refresh'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; count?: number }>(EXTENSIONS_MARKET_REFRESH, {})
      if (r?.ok === false) { bad('刷新社区索引失败', r); return }
      setMsg('社区索引已刷新：' + (r?.count ?? 0) + ' 条')
    } catch (e) { fail('刷新社区索引失败', e) } finally { setBusy('') }
  }

  const install = async (type: string, id: string) => {
    setBusy('install:' + type + ':' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_INSTALL, { type, source: id })
      if (r?.ok === false) { bad('安装失败', r); return }
      setMsg('已安装 ' + id + '（' + (TYPE_LABEL[type] ?? type) + '）')
      await Promise.all([loadList(), loadInstalled()])
    } catch (e) { fail('安装失败', e) } finally { setBusy('') }
  }

  const toggle = async (row: ExtRow) => {
    const id = row.ext_id ?? ''
    const type = row.ext_type ?? ''
    setBusy('toggle:' + id); setErr(''); setMsg('')
    try {
      const next = !row.enabled
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_TOGGLE, { type, id, enabled: next })
      if (r?.ok === false) { bad('切换失败', r); return }
      setMsg((next ? '已启用 ' : '已禁用 ') + id)
      await loadList()
    } catch (e) { fail('切换失败', e) } finally { setBusy('') }
  }

  const uninstall = async (row: ExtRow) => {
    const id = row.ext_id ?? ''
    const type = row.ext_type ?? ''
    setBusy('uninstall:' + id); setErr(''); setMsg('')
    try {
      // force 只在用户**在这个确认条上第二次点击**时传布尔 true
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_UNINSTALL, { type, id, force: true })
      if (r?.ok === false) { bad('卸载失败', r); return }
      setMsg('已卸载 ' + id)
      setConfirmUninstall('')
      await Promise.all([loadList(), loadInstalled()])
    } catch (e) { fail('卸载失败', e) } finally { setBusy('') }
  }

  const openConfig = (row: ExtRow) => {
    setConfigTarget(row)
    setConfigText(JSON.stringify(row.config ?? {}, null, 2))
  }

  const saveConfig = async () => {
    if (!configTarget) return
    let parsed: unknown
    try {
      parsed = JSON.parse(configText || '{}')
    } catch {
      setErr('配置不是合法 JSON —— 没有提交')
      return
    }
    setBusy('configure'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_CONFIGURE, {
        type: configTarget.ext_type,
        id: configTarget.ext_id,
        config: parsed,
      })
      if (r?.ok === false) { bad('保存配置失败', r); return }
      setMsg('配置已保存：' + (configTarget.ext_id ?? ''))
      setConfigTarget(null)
      await loadList()
    } catch (e) { fail('保存配置失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">扩展市场</h2>
        <span className="text-[11px] text-slate-500">
          技能 / MCP 服务 / 通道 / 插件包 —— 发现与浏览（安装入口按类型在各自页面，插件包在「插件清单」）
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} onClick={() => { void loadList(); void loadInstalled(); void loadRecommend() }} disabled={loading} aria-label="刷新扩展概览">
            {loading && busy !== 'search' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
          <button type="button" className={BTN_EM} onClick={() => void refreshIndex()} disabled={loading} aria-label="刷新社区索引">
            {busy === 'refresh' ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 刷新社区索引
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && (
        <div className="mb-2 flex items-center gap-1.5 rounded-md border border-emerald-800/60 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300">
          <CheckCircle2 size={12} /> {msg}
        </div>
      )}

      {/* 【2026-10-18】Claude 技能包不再单独占一个扩展类型：它只有 SKILL.md、没有
          manifest.json，装进 ~/.claude/skills 后云枢自身从不读 ⇒ 装了等于没装。
          替代入口在技能中心（维护 Tab），这里只留一句指路，避免用户以为"没地方装"。 */}
      <div className="mb-3 rounded-md border border-slate-800 bg-slate-900/40 px-2.5 py-1.5 text-[10px] text-slate-500">
        Claude 技能包（只有 SKILL.md、没有 manifest.json）不在这里装：请到 <span className="text-cyan-400">技能中心 → 维护 →「从 .claude/skills 导入」</span>，
        由服务端生成 manifest.json 后落进技能仓库。
      </div>

      {/* ── 分类统计 ── */}
      <div className="mb-3 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['技能', counts.skills],
          ['MCP 服务', counts.mcp_services],
          ['通道', counts.channels],
          ['插件包', counts.plugins],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(label)}</div>
            <div className="font-mono text-sm text-cyan-300">{value ?? 0}</div>
          </div>
        ))}
      </div>

      {/* ── 已安装 ── */}
      <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <Plug size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">已安装扩展（{list.length}）</h3>
        </div>
        {list.length === 0 && busy !== 'list' && (
          <div className="text-[11px] text-slate-500">还没有通过扩展系统安装过东西（内置技能不在此表）。</div>
        )}
        <div className="flex flex-col gap-1">
          {list.map((row) => {
            const id = row.ext_id ?? ''
            const key = (row.ext_type ?? '') + ':' + id
            const isOpen = confirmUninstall === key
            return (
              <div key={key} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                    {TYPE_LABEL[row.ext_type ?? ''] ?? row.ext_type ?? '?'}
                  </span>
                  <span className="font-mono text-cyan-300">{id}</span>
                  <span className="text-slate-300">{row.name ?? ''}</span>
                  {row.version && <span className="font-mono text-[10px] text-slate-600">v{row.version}</span>}
                  <span className={row.enabled ? 'text-emerald-400' : 'text-slate-500'}>
                    {row.enabled ? '启用' : '停用'}
                  </span>
                  <span className="ml-auto flex items-center gap-1">
                    <button type="button" className={BTN} onClick={() => void toggle(row)} disabled={loading} aria-label={'切换 ' + id}>
                      {busy === 'toggle:' + id ? <Loader2 size={11} className="animate-spin" /> : null}
                      {row.enabled ? '停用' : '启用'}
                    </button>
                    <button type="button" className={BTN} onClick={() => openConfig(row)} disabled={loading} aria-label={'配置 ' + id}>
                      配置
                    </button>
                    <button
                      type="button"
                      className={BTN_RED}
                      onClick={() => setConfirmUninstall(isOpen ? '' : key)}
                      disabled={loading}
                      aria-label={'卸载 ' + id}
                    >
                      <Trash2 size={11} /> 卸载
                    </button>
                  </span>
                </div>
                {row.description && <div className="mt-0.5 text-[10px] text-slate-500">{row.description}</div>}
                {isOpen && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                    <TriangleAlert size={12} className="text-red-400" />
                    <span className="text-[10px] text-red-300">
                      卸载对「文件轨独占」的扩展是不可逆删除（force=true 才允许）。确定？
                    </span>
                    <button type="button" className={BTN_RED} onClick={() => void uninstall(row)} disabled={loading} aria-label={'确认卸载 ' + id}>
                      {busy === 'uninstall:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认卸载
                    </button>
                    <button type="button" className={BTN} onClick={() => setConfirmUninstall('')}>取消</button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* ── 内置项归属（三条指路；市场里不再有任何"安装内置项"的动作） ── */}
      {/* 【2026-10-19】最后一个内置分组（内置行为 = 随仓库分发的 persona 提示片段）也收走了：
          它的状态事实源是 SkillRegistry，启停入口在「技能中心 · LLM 技能」Tab（同一份数据、
          同一个事实源）。至此内置项全部回到各自的功能页；市场只剩两件事：
          **发现**（内置/社区/GitHub 索引）与**从外部安装**（搜索结果/推荐里的社区条目）。 */}
      <div className="mb-3 rounded-md border border-slate-800 bg-slate-900/40 px-2.5 py-1.5 text-[10px] leading-relaxed text-slate-500">
        内置项的安装/启停都在各自功能页：
        <span className="text-cyan-400"> 内置行为（persona 技能）→ 技能中心 · LLM 技能</span>、
        <span className="text-cyan-400"> 内置 MCP → 工具调用 · MCP 系统</span>、
        <span className="text-cyan-400"> 内置通道 → 系统管理 · 消息中心</span>。
        市场只负责发现与已安装管理；技能从外部安装 → <span className="text-cyan-400">技能中心 · 维护 · 按来源安装</span>。
      </div>

      {/* ── 市场搜索 ── */}
      <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Search size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">市场搜索</h3>
          <input
            className={INPUT + ' w-56'}
            placeholder="关键词（留空 = 全部）"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void search() }}
            aria-label="市场搜索关键词"
          />
          <select className={INPUT} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="市场搜索类型">
            <option value="">全部类型</option>
            {TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </select>
          <button type="button" className={BTN_EM} onClick={() => void search()} disabled={loading} aria-label="搜索扩展市场">
            {busy === 'search' ? <Loader2 size={11} className="animate-spin" /> : <Search size={11} />} 搜索
          </button>
        </div>

        {(['builtin', 'community', 'github'] as const).map((group) => (
          <div key={group} className="mb-2">
            <div className="mb-1 text-[10px] text-slate-500">
              {group === 'builtin' ? '内置' : group === 'community' ? '社区索引' : 'GitHub'}（{results[group]?.length ?? 0}）
            </div>
            {(results[group]?.length ?? 0) === 0 && <div className="text-[10px] text-slate-600">—</div>}
            <div className="flex flex-col gap-1">
              {(results[group] ?? []).map((m) => {
                const type = m.ext_type || m.market_type || 'skill'
                const id = m.id ?? ''
                const busyKey = 'install:' + type + ':' + id
                return (
                  <div key={group + id} className="flex flex-wrap items-center gap-1.5 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                    <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                    <span className="text-slate-300">{m.name ?? ''}</span>
                    <span className="truncate text-[10px] text-slate-500">{m.description ?? ''}</span>
                    <span className="ml-auto flex items-center gap-1">
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[9px] text-slate-400">{TYPE_LABEL[type] ?? type}</span>
                      {/* 安装入口按类型收敛：插件→插件清单、技能→技能中心；市场只发现/浏览 */}
                      {type === 'plugin'
                        ? <span className="text-[10px] text-slate-500">到插件清单安装</span>
                        : type === 'skill'
                          ? <span className="text-[10px] text-slate-500">到技能中心安装</span>
                          : (
                            <button type="button" className={BTN} onClick={() => void install(type, id)} disabled={loading} aria-label={'安装 ' + id}>
                              {busy === busyKey ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 安装
                            </button>
                          )}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}

        <div className="mt-2 border-t border-slate-800 pt-2">
          <div className="mb-1 text-[10px] text-slate-500">推荐（{recs.length}）</div>
          <div className="flex flex-wrap gap-1.5">
            {recs.map((r) => {
              const type = r.ext_type ?? 'skill'
              const id = r.id ?? ''
              // 安装入口按类型收敛：插件→插件清单、技能→技能中心
              if (type === 'plugin' || type === 'skill') {
                return (
                  <span
                    key={'rec-' + type + id}
                    className="rounded-md border border-slate-800 px-2 py-1 text-[11px] text-slate-500"
                    title={type === 'plugin'
                      ? '插件包安装入口在「插件与扩展 · 插件清单」'
                      : '技能安装入口在「技能中心 · 维护 · 按来源安装」'}
                  >
                    {r.name ?? id}
                    <span className="ml-1 text-[9px]">
                      {type === 'plugin' ? '（到插件清单安装）' : '（到技能中心安装）'}
                    </span>
                  </span>
                )
              }
              return (
                <button
                  key={'rec-' + type + id}
                  type="button"
                  className={BTN}
                  title={r.description}
                  onClick={() => void install(type, id)}
                  disabled={loading}
                  aria-label={'安装推荐 ' + id}
                >
                  <Download size={10} /> {r.name ?? id}
                  <span className="text-[9px] text-slate-500">{TYPE_LABEL[type] ?? type}</span>
                </button>
              )
            })}
            {recs.length === 0 && <span className="text-[10px] text-slate-600">—</span>}
          </div>
        </div>
      </div>

      {/* ── 配置弹层 ── */}
      {configTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setConfigTarget(null)}>
          <div className="w-full max-w-lg rounded-xl border border-slate-700 bg-slate-900 p-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-2 text-sm font-semibold text-slate-100">
              配置 · {configTarget.name ?? configTarget.ext_id}（{TYPE_LABEL[configTarget.ext_type ?? ''] ?? configTarget.ext_type}）
            </h3>
            <textarea
              className={INPUT + ' h-40 w-full font-mono'}
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              aria-label="扩展配置 JSON"
            />
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className={BTN} onClick={() => setConfigTarget(null)}>取消</button>
              <button type="button" className={BTN_EM} onClick={() => void saveConfig()} disabled={loading} aria-label="保存扩展配置">
                {busy === 'configure' ? <Loader2 size={11} className="animate-spin" /> : null} 保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
