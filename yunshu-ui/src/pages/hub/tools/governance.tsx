/**
 * 工具治理 —— 分类 / 健康 / 关键词 / 启用状态总览（6 条）
 * ------------------------------------------------------------------
 *   GET    /api/tools/categories      四能力平面分类 + 关键词表
 *   GET    /api/tools/health          工具健康（追踪/成功率/评分）
 *   GET    /api/tools/status-batch    所有工具与技能的启用摘要
 *   POST   /api/tools/keywords        加关键词
 *   DELETE /api/tools/keywords        删关键词
 *   POST   /api/tools/keywords/update 改关键词
 *   POST   /api/tools/keywords/reset  重置为默认
 *
 * 【关键词决定"哪类工具会被路由选中"】它不是装饰：加错一个词，会让某类工具在
 * 不相干的对话里被反复注入。所以改/删/重置都做二次确认，且每次改完回读分类表。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Activity, Loader2, RefreshCw, RotateCcw, Tags, Wrench,
} from 'lucide-react'
import { ErrorBox, hubDeleteJson, hubGet, hubPost } from '../components/ui'
import {
  TOOLS_CATEGORIES,
  TOOLS_HEALTH,
  TOOLS_KEYWORDS,
  TOOLS_KEYWORDS_RESET,
  TOOLS_KEYWORDS_UPDATE,
  TOOLS_STATUS_BATCH,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const PRE = 'max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

interface CatRow { key?: string; label?: string; icon?: string; description?: string; always?: boolean; tools?: string[] }
interface StatusRow { name?: string; enabled?: boolean; description?: string }

export default function ToolsGovernance() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [cats, setCats] = useState<CatRow[]>([])
  const [keywords, setKeywords] = useState<Record<string, string[]>>({})
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [statusRows, setStatusRows] = useState<StatusRow[]>([])

  const [cat, setCat] = useState('')
  const [kw, setKw] = useState('')
  const [oldKw, setOldKw] = useState('')
  const [newKw, setNewKw] = useState('')
  const [confirmReset, setConfirmReset] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [c, h, s] = await Promise.allSettled([
        hubGet<{ categories?: CatRow[]; keywords?: Record<string, string[]> }>(TOOLS_CATEGORIES),
        hubGet<Record<string, unknown>>(TOOLS_HEALTH),
        hubGet<StatusRow[] | { items?: StatusRow[] }>(TOOLS_STATUS_BATCH),
      ])
      if (c.status === 'fulfilled') {
        setCats(c.value?.categories ?? [])
        setKeywords(c.value?.keywords ?? {})
        const firstCat = (c.value?.categories ?? [])[0]?.key ?? ''
        setCat((cur) => cur || firstCat)
      }
      if (h.status === 'fulfilled') setHealth(h.value)
      if (s.status === 'fulfilled') {
        setStatusRows(Array.isArray(s.value) ? s.value : (s.value?.items ?? []))
      }
    } catch (e) { fail('读取工具治理失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void load() }, [load])

  const addKeyword = async () => {
    setBusy('kw-add'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(TOOLS_KEYWORDS, { category: cat, keyword: kw })
      if (r?.ok === false) { setErr('加关键词失败：' + (r.error || 'ok=false')); return }
      setMsg('已加入 ' + cat + '：' + kw)
      setKw('')
      await load()
    } catch (e) { fail('加关键词失败', e) } finally { setBusy('') }
  }

  const removeKeyword = async (category: string, keyword: string) => {
    setBusy('kw-del'); setErr(''); setMsg('')
    try {
      // 删除靠 body 定位（{category, keyword}），故必须用带 body 的 DELETE
      const r = await hubDeleteJson<{ ok?: boolean; error?: string }>(TOOLS_KEYWORDS, { category, keyword })
      if (r?.ok === false) { setErr('删关键词失败：' + (r.error || 'ok=false')); return }
      setMsg('已删除 ' + category + '：' + keyword)
      await load()
    } catch (e) { fail('删关键词失败', e) } finally { setBusy('') }
  }

  const updateKeyword = async () => {
    setBusy('kw-upd'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(TOOLS_KEYWORDS_UPDATE, {
        category: cat, old_keyword: oldKw, new_keyword: newKw,
      })
      if (r?.ok === false) { setErr('改关键词失败：' + (r.error || 'ok=false')); return }
      setMsg('已把 ' + oldKw + ' 改为 ' + newKw)
      setOldKw(''); setNewKw('')
      await load()
    } catch (e) { fail('改关键词失败', e) } finally { setBusy('') }
  }

  const resetKeywords = async () => {
    setBusy('kw-reset'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(TOOLS_KEYWORDS_RESET, {})
      if (r?.ok === false) { setErr('重置失败：' + (r.error || 'ok=false')); return }
      setMsg('关键词已重置为默认')
      setConfirmReset(false)
      await load()
    } catch (e) { fail('重置失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const catKeys = Object.keys(keywords)

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Wrench size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">工具治理</h2>
        <span className="text-[11px] text-slate-500">分类 / 健康 / 关键词（决定路由选中哪类工具）/ 启用总览</span>
        <div className="ml-auto">
          <button type="button" className={BTN} onClick={() => void load()} disabled={loading} aria-label="刷新工具治理">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ['工具总数', ((health?.total_tools as number) ?? statusRows.length)],
          ['健康工具', ((health?.healthy_tools as number) ?? 0)],
          ['整体评分', ((health?.overall_score as number) ?? '—')],
          ['分类数', cats.length],
        ].map(([k, v]) => (
          <div key={String(k)} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
            <div className="text-[10px] text-slate-500">{String(k)}</div>
            <div className="font-mono text-[13px] text-cyan-300">{String(v)}</div>
          </div>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          <Activity size={11} className="text-cyan-400" /> 能力分类（{cats.length}）
        </div>
        <div className="flex flex-col gap-1">
          {cats.map((c) => (
            <div key={c.key ?? ''} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-slate-300">{c.icon ?? ''} {c.label ?? c.key}</span>
                <span className="font-mono text-[10px] text-slate-600">{c.key}</span>
                {c.always && <span className="rounded bg-cyan-500/10 px-1 text-[9px] text-cyan-300">常驻</span>}
                <span className="ml-auto text-[10px] text-slate-600">{(c.tools ?? []).length} 个工具</span>
              </div>
              <div className="text-[10px] text-slate-500">{c.description ?? ''}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          <Tags size={11} className="text-cyan-400" /> 路由关键词
          <select className={INPUT} value={cat} onChange={(e) => setCat(e.target.value)} aria-label="关键词分类">
            {cats.map((c) => <option key={c.key} value={c.key}>{c.label ?? c.key}</option>)}
            {cats.length === 0 && catKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>

        <div className="mb-2 flex flex-wrap items-center gap-2">
          <input className={INPUT + ' w-40'} value={kw} onChange={(e) => setKw(e.target.value)} placeholder="新关键词" aria-label="新关键词" />
          <button type="button" className={BTN} onClick={() => void addKeyword()} disabled={loading || kw.trim() === '' || cat === ''} aria-label="添加关键词">
            {busy === 'kw-add' ? <Loader2 size={11} className="animate-spin" /> : null} 添加
          </button>
        </div>

        <div className="mb-2 flex flex-wrap items-center gap-2">
          <input className={INPUT + ' w-40'} value={oldKw} onChange={(e) => setOldKw(e.target.value)} placeholder="原关键词" aria-label="原关键词" />
          <input className={INPUT + ' w-40'} value={newKw} onChange={(e) => setNewKw(e.target.value)} placeholder="新关键词" aria-label="改名后的关键词" />
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void updateKeyword()}
            disabled={loading || oldKw.trim() === '' || newKw.trim() === '' || cat === ''}
            aria-label="修改关键词"
          >
            {busy === 'kw-upd' ? <Loader2 size={11} className="animate-spin" /> : null} 改名
          </button>
          <label className="ml-2 flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={confirmReset} onChange={(e) => setConfirmReset(e.target.checked)} aria-label="确认重置关键词" />
            我确认重置为默认
          </label>
          <button type="button" className={BTN_RED} onClick={() => void resetKeywords()} disabled={loading || !confirmReset} aria-label="重置关键词">
            <RotateCcw size={11} /> 重置
          </button>
        </div>

        <div className="flex flex-col gap-1">
          {Object.entries(keywords).map(([k, list]) => (
            <div key={k} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1">
              <div className="text-[11px] text-slate-300">{k}（{list.length}）</div>
              <div className="mt-0.5 flex flex-wrap gap-1">
                {list.map((w) => (
                  <span key={w} className="flex items-center gap-1 rounded bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                    {w}
                    <button
                      type="button"
                      className="text-red-400 hover:text-red-300"
                      title="删除该关键词"
                      onClick={() => void removeKeyword(k, w)}
                      aria-label={'删除关键词 ' + w}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
          {Object.keys(keywords).length === 0 && <div className="text-[11px] text-slate-500">没有取到关键词表。</div>}
        </div>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-1 text-[11px] text-slate-400">工具与技能启用总览（{statusRows.length}）</div>
        <pre className={PRE}>{JSON.stringify(statusRows.slice(0, 20), null, 2)}</pre>
      </div>
    </div>
  )
}
