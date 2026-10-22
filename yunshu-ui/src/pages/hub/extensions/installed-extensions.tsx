/**
 * 已安装扩展（扩展系统）—— 各归属页共用的「启停 + 配置 + 卸载」列表
 * ------------------------------------------------------------------
 * 【解决什么】扩展市场页（extensions/market.tsx）被整页删除后，`/api/extensions`
 * 的已安装管理能力不能跟着消失。本组件把它收敛成一个可复用小节，按 `types` 过滤
 * 后嵌进各类型的**归属页**（MCP → 工具调用 · MCP 系统；插件包 → 插件清单；
 * 扩展技能 → 技能中心 · LLM 技能；通道已在 #1226 迁到消息中心）。
 *
 * 数据源：
 *   · 列表   GET  /api/extensions/list（一次拉全量，前端按 ext_type 过滤）；
 *   · 启停   POST /api/extensions/toggle   {type, id, enabled}
 *   · 配置   POST /api/extensions/configure {type, id, config}
 *   · 卸载   POST /api/extensions/uninstall {type, id, force}
 *
 * 【为什么卸载是两步】卸载对「文件轨独占」的扩展是**不可逆删除**（后端 force 只认
 * JSON 布尔字面量 true）⇒ 先点「卸载」展开确认条，再点「确认卸载」才发请求。
 * 这是从市场页原样搬过来的纪律，不能因为换了宿主页面就放宽。
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCw, Settings2, Trash2, TriangleAlert } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  EXTENSIONS_CONFIGURE,
  EXTENSIONS_LIST,
  EXTENSIONS_TOGGLE,
  EXTENSIONS_UNINSTALL,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-[11px] text-slate-200 outline-hidden focus:border-cyan-600'

export interface ExtRow {
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

export interface InstalledExtensionsProps {
  /** 只列这些 ext_type（'skill' | 'mcp' | 'plugin' | 'channel'） */
  types: string[]
  title: string
  /** 一句话说明（这组条目从哪来、启停影响什么） */
  hint?: string
  /** 空列表文案 */
  emptyText?: string
  /**
   * 已在上层页面展示过的条目 id（如技能中心统一表里的同一技能）。
   * 传了就**不再重复列出**——同一条目在一页里出现两次，用户无法判断该点哪个。
   */
  excludeIds?: string[]
  /** 去重/过滤后一条不剩时整块隐藏（默认仍显示空态文案，供「唯一入口」类页面提示） */
  hideWhenEmpty?: boolean
}

export default function InstalledExtensions({ types, title, hint, emptyText, excludeIds, hideWhenEmpty }: InstalledExtensionsProps) {
  const [rows, setRows] = useState<ExtRow[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [confirm, setConfirm] = useState('')
  const [configKey, setConfigKey] = useState('')
  const [configText, setConfigText] = useState('')

  const typeSet = types.join(',')
  // 依赖用 join 后的稳定字符串：父级每次渲染都会新建数组，直接进 deps 会无限重载
  const excludedKey = (excludeIds ?? []).join(',')

  const fail = (label: string, e: unknown) =>
    setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string; message?: string }) =>
    setErr(label + '：' + (r?.error || r?.message || '后端返回 ok=false'))

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; error?: string; extensions?: ExtRow[] }>(EXTENSIONS_LIST)
      if (r?.ok === false) { bad('读取已安装扩展失败', r); return }
      const allowed = new Set(typeSet.split(','))
      const excluded = new Set(excludedKey ? excludedKey.split(',') : [])
      setRows((r?.extensions ?? []).filter(
        (x) => allowed.has(String(x.ext_type ?? '')) && !excluded.has(String(x.ext_id ?? '')),
      ))
    } catch (e) { fail('读取已安装扩展失败', e) } finally { setLoading(false) }
  }, [typeSet, excludedKey])

  useEffect(() => { void load() }, [load])

  const toggle = async (row: ExtRow) => {
    const id = String(row.ext_id ?? '')
    setBusy('toggle:' + id); setErr(''); setMsg('')
    try {
      const next = !row.enabled
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(
        EXTENSIONS_TOGGLE, { type: row.ext_type, id, enabled: next })
      if (r?.ok === false) { bad('切换失败', r); return }
      setMsg((next ? '已启用 ' : '已禁用 ') + id)
      await load()
    } catch (e) { fail('切换失败', e) } finally { setBusy('') }
  }

  const uninstall = async (row: ExtRow) => {
    const id = String(row.ext_id ?? '')
    setBusy('uninstall:' + id); setErr(''); setMsg('')
    try {
      // force 只在用户**在这个确认条上第二次点击**时传布尔 true
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(
        EXTENSIONS_UNINSTALL, { type: row.ext_type, id, force: true })
      if (r?.ok === false) { bad('卸载失败', r); return }
      setMsg('已卸载 ' + id)
      setConfirm('')
      await load()
    } catch (e) { fail('卸载失败', e) } finally { setBusy('') }
  }

  const openConfig = (row: ExtRow) => {
    const key = String(row.ext_id ?? '')
    if (configKey === key) { setConfigKey(''); return }
    setConfigKey(key)
    setConfigText(JSON.stringify(row.config ?? {}, null, 2))
    setErr(''); setMsg('')
  }

  const saveConfig = async (row: ExtRow) => {
    const id = String(row.ext_id ?? '')
    let parsed: unknown
    try {
      parsed = JSON.parse(configText || '{}')
    } catch {
      setErr('配置不是合法 JSON：' + id)
      return
    }
    setBusy('config:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(
        EXTENSIONS_CONFIGURE, { type: row.ext_type, id, config: parsed })
      if (r?.ok === false) { bad('保存配置失败', r); return }
      setMsg('配置已保存 ' + id)
      setConfigKey('')
      await load()
    } catch (e) { fail('保存配置失败', e) } finally { setBusy('') }
  }

  // 去重后一条不剩：整块不渲染（调用方已要求隐藏时）
  if (hideWhenEmpty && !loading && !err && rows.length === 0) return null

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3" data-testid={'installed-ext-' + typeSet}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold text-slate-200">{title}</h3>
        <span className="text-[10px] text-slate-500">{rows.length} 项</span>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className={BTN + ' ml-auto'}
          aria-label="刷新已安装扩展"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>
      {hint && <p className="mb-2 text-[10px] leading-relaxed text-slate-500">{hint}</p>}
      {err && <div className="mb-2"><ErrorBox message={err} /></div>}
      {msg && <div className="mb-2 rounded-sm border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-cyan-300">{msg}</div>}

      {loading && rows.length === 0 && (
        <div className="py-3 text-center text-[11px] text-slate-500"><Loader2 size={12} className="mr-1 inline animate-spin" />读取中…</div>
      )}
      {!loading && rows.length === 0 && (
        <div className="py-3 text-center text-[11px] text-slate-500">{emptyText ?? '没有通过扩展系统安装的条目。'}</div>
      )}

      <div className="flex flex-col gap-1">
        {rows.map((row) => {
          const id = String(row.ext_id ?? '')
          const key = String(row.ext_type ?? '') + ':' + id
          const confirming = confirm === key
          const editingConfig = configKey === id
          return (
            <div key={key} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="font-mono text-cyan-300">{id}</span>
                <span className="text-slate-300">{row.name ?? ''}</span>
                {row.version && <span className="font-mono text-[10px] text-slate-600">v{row.version}</span>}
                <span className={row.enabled ? 'text-emerald-400' : 'text-slate-500'}>
                  {row.enabled ? '启用' : '停用'}
                </span>
                <span className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => void toggle(row)}
                    disabled={loading}
                    aria-label={'切换 ' + id}
                  >
                    {busy === 'toggle:' + id ? <Loader2 size={11} className="animate-spin" /> : null}
                    {row.enabled ? '停用' : '启用'}
                  </button>
                  <button
                    type="button"
                    className={BTN}
                    onClick={() => openConfig(row)}
                    disabled={loading}
                    aria-label={'配置 ' + id}
                  >
                    <Settings2 size={11} /> 配置
                  </button>
                  <button
                    type="button"
                    className={BTN_RED}
                    onClick={() => setConfirm(confirming ? '' : key)}
                    disabled={loading}
                    aria-label={'卸载 ' + id}
                  >
                    <Trash2 size={11} /> 卸载
                  </button>
                </span>
              </div>
              {row.description && <div className="mt-0.5 text-[10px] text-slate-500">{row.description}</div>}

              {editingConfig && (
                <div className="mt-1.5 rounded-sm border border-slate-700 bg-slate-950 px-2 py-1.5">
                  <div className="mb-1 text-[10px] text-slate-500">配置（JSON）</div>
                  <textarea
                    className={INPUT + ' h-24 resize-y'}
                    value={configText}
                    onChange={(e) => setConfigText(e.target.value)}
                    aria-label={'配置文本 ' + id}
                  />
                  <div className="mt-1 flex justify-end gap-1">
                    <button type="button" className={BTN} onClick={() => setConfigKey('')}>取消</button>
                    <button
                      type="button"
                      className={BTN}
                      onClick={() => void saveConfig(row)}
                      disabled={busy === 'config:' + id}
                      aria-label={'保存配置 ' + id}
                    >
                      {busy === 'config:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 保存
                    </button>
                  </div>
                </div>
              )}

              {confirming && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded-sm border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                  <TriangleAlert size={12} className="text-red-400" />
                  <span className="text-[10px] text-red-300">
                    卸载对「文件轨独占」的扩展是不可逆删除（force=true 才允许）。确定？
                  </span>
                  <button
                    type="button"
                    className={BTN_RED}
                    onClick={() => void uninstall(row)}
                    disabled={loading}
                    aria-label={'确认卸载 ' + id}
                  >
                    {busy === 'uninstall:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认卸载
                  </button>
                  <button type="button" className={BTN} onClick={() => setConfirm('')}>取消</button>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
