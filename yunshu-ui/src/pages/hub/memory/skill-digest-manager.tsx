/**
 * SkillDigestManager —— 技能资产「评审-消化」全生命周期管理（skills-mgmt）
 * ------------------------------------------------------------------
 * 满足：安装 / 创建 / 修改 / 删除 全生命周期 + 自动验证评估流程：
 *   - 新建 / 外来安装 / 修改后，后端自动执行扩展评估（权限/攻击面/数据合规 +
 *     兼容性：原生冲突/操作重叠/资源竞争/交互冲突/重复建议）；本组件直接呈现
 *     评估结论（digest verdict + 安全/质量/兼容分数 + findings）。
 *   - 动作：新建(手写)、外来安装(source)、评审-消化(digest)、批量审核、
 *     全量自动评审-消化(run-all)、启停、发布（发布门禁=PASSED）、删除。
 * 说明：与上方「运行时注入启停」表互补——这里是资产库（skills-mgmt 数据），
 * 通过（发布+启用）的资产会进入运行时能力集（上下文注入层消费）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ListTree, ChevronDown, ChevronRight, Download, FileInput, Filter, Folder, Lightbulb, Loader2, Layers, PackagePlus, Plus,
  RefreshCw, Rocket, ScrollText, ShieldCheck, Trash2, X, Zap,
} from 'lucide-react'
import { hubGet, hubPost } from '../../hub/components/ui'
import GenerateRequirementModal from './generate-requirement-modal'
import ClassIcon from './class-icon'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = `${BTN} border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10`
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const BTN_RED = `${BTN} hover:bg-red-500/10 hover:text-red-400`

interface Finding { severity: string; category: string; code: string; message: string; location?: string }
interface ReviewLike {
  status?: string; score?: number; security_score?: number; quality_score?: number
  compatibility_score?: number; duplicate_score?: number; digest_verdict?: string
  auto_assessed?: boolean; summary?: string; findings?: Finding[]
}
interface SkillItem {
  id: string; name: string; description?: string; status?: string; enabled?: boolean
  content_type?: string; category?: string; version?: string; review?: ReviewLike
  tags?: string[]; is_sensitive?: boolean; isolation_strategy?: string; source?: string
}

const SEV: Record<string, string> = {
  critical: 'bg-red-500/15 text-red-400 border-red-800',
  error: 'bg-orange-500/15 text-orange-400 border-orange-800',
  warn: 'bg-amber-500/15 text-amber-400 border-amber-800',
  info: 'bg-slate-500/15 text-slate-400 border-slate-700',
}
const chip = (txt: string, cls: string) => (
  <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] ${cls}`}>{txt}</span>
)
const statusChip = (s?: string) => chip(
  s ?? '-',
  s === 'published' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-800'
    : s === 'approved' ? 'bg-cyan-500/15 text-cyan-400 border-cyan-800'
      : s === 'pending_review' ? 'bg-amber-500/15 text-amber-400 border-amber-800'
        : s === 'rejected' ? 'bg-red-500/15 text-red-400 border-red-800'
          : 'bg-slate-500/15 text-slate-400 border-slate-700',
)

export default function SkillDigestManager() {
  const [items, setItems] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [createOpen, setCreateOpen] = useState(false)
  const [installOpen, setInstallOpen] = useState(false)
  const [genOpen, setGenOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [publishTarget, setPublishTarget] = useState<SkillItem | null>(null)
  const [adviceTarget, setAdviceTarget] = useState<SkillItem | null>(null)
  const [versionsTarget, setVersionsTarget] = useState<SkillItem | null>(null)
  const [redraftTarget, setRedraftTarget] = useState<SkillItem | null>(null)
  const [curateOpen, setCurateOpen] = useState(false)
  const [feedOpen, setFeedOpen] = useState(false)
  const [cmdTarget, setCmdTarget] = useState<SkillItem | null>(null)
  interface DigestEv { ts?: string; kind?: string; skill_id?: string; verdict?: string; summary?: string }
  const [events, setEvents] = useState<DigestEv[]>([])
  const loadEvents = useCallback(async () => {
    try {
      const r = await hubGet<{ records?: DigestEv[] }>('/api/skills-mgmt/digest/events?limit=10')
      const recs = Array.isArray(r.records) ? r.records : []
      setEvents(recs)
      const mx = recs.reduce<string>((a, e) => (e.ts && e.ts > a ? e.ts : a), '')
      if (mx) sinceRef.current = mx
    } catch { /* 事件源不可用时不打扰 */ }
  }, [])
  useEffect(() => { void loadEvents() }, [loadEvents])

  // ── 消化动态：未读角标 + 轮询 + 原生通知 ──
  const [lastSeen, setLastSeen] = useState<string>(() => {
    try { return localStorage.getItem('yunshu:digest:last-seen') ?? '' } catch { return '' }
  })
  const lastSeenRef = useRef(lastSeen)
  useEffect(() => { lastSeenRef.current = lastSeen }, [lastSeen])
  const sinceRef = useRef('')
  const unread = events.filter((e) => e.ts && (!lastSeen || e.ts > lastSeen)).length

  // 实时推送：digest/stream 长轮询（服务端 hold 至多 20s，有新事件立即返回）
  useEffect(() => {
    let alive = true
    const poll = async () => {
      let delay = 3000
      try {
        const q = sinceRef.current ? `?since=${encodeURIComponent(sinceRef.current)}&timeout_sec=20` : ''
        const ctrl = new AbortController()
        const tmr = setTimeout(() => ctrl.abort(), 30000)
        const res = await fetch(`/api/skills-mgmt/digest/stream${q}`, { signal: ctrl.signal })
        clearTimeout(tmr)
        if (res.ok) {
          const d = (await res.json()) as { records?: DigestEv[] }
          const recs = Array.isArray(d.records) ? d.records : []
          if (recs.length > 0) {
            setEvents((prev) => {
              const seen = new Set(prev.map((x) => `${x.ts ?? ''}|${x.skill_id ?? ''}`))
              const add = recs.filter((x) => !seen.has(`${x.ts ?? ''}|${x.skill_id ?? ''}`))
              return [...prev, ...add].slice(-40)
            })
            sinceRef.current = recs[recs.length - 1].ts ?? sinceRef.current
            delay = 400
          } else {
            delay = 300 // 超时空返回 → 立即续连
          }
        } else {
          throw new Error(`HTTP ${res.status}`)
        }
      } catch { delay = 3000 } finally { if (alive) setTimeout(() => void poll(), delay) }
    }
    void poll()
    return () => { alive = false }
  }, [])

  useEffect(() => {
    try {
      if (typeof Notification === 'undefined') return
      const unseen = events.filter((e) => e.ts && e.ts > lastSeenRef.current)
      if (unseen.length === 0) return
      const target = unseen[unseen.length - 1]
      if (Notification.permission === 'granted') {
        new Notification('云枢 · 技能评审-消化', {
          body: `${target.skill_id ?? ''}：${target.verdict === 'block' ? '存在阻断项，需人工复核' : target.verdict === 'ok' ? '评估通过' : '评估完成'}${target.summary ? ` · ${target.summary.slice(0, 60)}` : ''}`,
        })
      }
    } catch { /* 无通知能力/被拒时静默 */ }
  }, [events])

  const markEventsSeen = () => {
    const mx = events.reduce<string>((a, e) => (e.ts && e.ts > a ? e.ts : a), '')
    setLastSeen(mx)
    try { localStorage.setItem('yunshu:digest:last-seen', mx) } catch { /* ignore */ }
  }

  /** 导出总览 CSV：审计（人工复核）+ 消化动态（评估事件）合并 */
  const exportOverview = async () => {
    try {
      const [a, e] = await Promise.all([
        hubGet<{ records?: { ts?: string; event?: string; skill_id?: string; actor?: string; reason?: string }[] }>('/api/skills-mgmt/review/audit?limit=500'),
        hubGet<{ records?: DigestEv[] }>('/api/skills-mgmt/digest/events?limit=200'),
      ])
      const esc = (s?: string) => `"${String(s ?? '').replace(/"/g, '""')}"`
      const rows: string[] = []
      rows.push(['type', 'ts', 'skill_id', 'actor/verdict', 'detail'].join(','))
      for (const r of a.records ?? []) rows.push(['audit', r.ts, r.skill_id, r.actor, r.reason].map(esc).join(','))
      for (const r of e.records ?? []) rows.push(['digest', r.ts, r.skill_id, r.verdict, r.summary].map(esc).join(','))
      const blob = new Blob([`\uFEFF${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' })
      const el = document.createElement('a')
      el.href = URL.createObjectURL(blob)
      el.download = `skills-digest-overview-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`
      el.click()
      URL.revokeObjectURL(el.href)
    } catch (err) {
      setMsg(`导出总览失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const load = useCallback(async () => {
    setError('')
    try {
      const r = await hubGet<{ items?: SkillItem[] }>('/api/skills-mgmt')
      setItems(Array.isArray(r.items) ? r.items : [])
    } catch (e) {
      setError(`技能资产加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(''); setMsg('')
    try {
      const r = (await fn()) as { ok?: boolean; error?: string } | undefined
      if (r && r.ok === false && r.error) setMsg(`操作未完成：${r.error}`)
      else setMsg(`${label} 完成`)
      await load()
    } catch (e) {
      setMsg(`${label} 失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(''); void loadEvents() }
  }

  const digestOne = (id: string) => act('评审-消化', () => hubPost(`/api/skills-mgmt/digest/${id}`))
  const runAll = () => act('全量自动评审-消化', () => hubPost('/api/skills-mgmt/digest/run-all'))
  const batchReview = () => act('批量审核', () => hubPost('/api/skills-mgmt/review/batch'))
  /** 发布（先经「人工复核」弹窗确认；未通过评审时须填原因强制发布并写入审计） */
  const confirmPublish = (it: SkillItem, reason?: string) => {
    setPublishTarget(null)
    const needsReason = it.review?.status !== 'passed' || it.review?.digest_verdict === 'block'
    void act('发布', () =>
      needsReason
        ? hubPost(`/api/skills-mgmt/${it.id}/publish?force=1&reason=${encodeURIComponent(reason || 'manual_review_passed')}`)
        : hubPost(`/api/skills-mgmt/${it.id}/publish`))
  }
  const toggle = (it: SkillItem) => act('启停', () => hubPost(`/api/skills-mgmt/${it.id}/toggle`, { enabled: !it.enabled }))
  const remove = async (it: SkillItem) => {
    if (!window.confirm(`确定从资产库删除技能「${it.name || it.id}」？`)) return
    setBusy('删除'); setError(''); setMsg('')
    try {
      const r = await fetch(`/api/skills-mgmt/${it.id}`, { method: 'DELETE' })
      const body = await r.json().catch(() => null)
      if (!r.ok) setMsg(`删除失败：${body?.error ?? `HTTP ${r.status}`}`)
      else setMsg(`已删除「${it.name || it.id}」`)
      await load()
    } catch (e) {
      setMsg(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }

  // ── 技能自动分类（同类折叠浏览：新技能自动归类/自动新建类）──
  interface ClassGroup { name: string; count?: number; auto?: boolean; skills?: { id?: string }[] }
  const [grouped, setGrouped] = useState(false)
  const [collapsedCls, setCollapsedCls] = useState<Record<string, boolean>>({})
  const [classView, setClassView] = useState<ClassGroup[] | null>(null)
  const loadClasses = useCallback(async () => {
    try {
      const r = await hubGet<{ groups?: ClassGroup[] }>('/api/skills-mgmt/classes')
      setClassView(Array.isArray(r.groups) ? r.groups : [])
      setError('')
    } catch (e) {
      setMsg(`分类视图加载失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [])
  const toggleGrouped = () => {
    const next = !grouped
    setGrouped(next)
    if (next) void loadClasses()
  }
  const rerunAutoClassify = async () => {
    setBusy('自动分类'); setMsg(''); setError('')
    try {
      const r = await hubPost<{ classified?: number; created_classes?: number }>('/api/skills-mgmt/classes/run-auto')
      setMsg(`自动分类完成：新归类 ${r?.classified ?? 0} 项，自动新建类 ${r?.created_classes ?? 0} 个。`)
      await Promise.all([load(), loadClasses()])
    } catch (e) {
      setMsg(`自动分类失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }
  /** 行内移动技能到其它分类（人工选择；后续自动重判不再覆盖） */
  const moveClass = async (it: SkillItem, cls: string) => {
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>('/api/skills-mgmt/classes/move', { skill_id: it.id, class_name: cls })
      if (r && r.ok === false) setMsg(`移动失败：${r.error}`)
      else { setMsg(`已将「${it.name || it.id}」移至「${cls}」`); await Promise.all([load(), loadClasses()]) }
    } catch (e) { setMsg(`移动失败：${e instanceof Error ? e.message : String(e)}`) }
  }
  /** 可移动到的分类候选：分类视图 + 未分类 */
  const classOptions = useMemo(() => {
    const names = [...(classView ?? []).map((g) => g.name)]
    if (!names.includes('未分类')) names.push('未分类')
    return names
  }, [classView])
  const toggleCls = (name: string) => setCollapsedCls((p) => ({ ...p, [name]: !p[name] }))

  const toggleRow = (id: string) => setExpanded((p) => ({ ...p, [id]: !p[id] }))

  /** 资产行渲染（表格模式逐行；分类折叠模式下由分组头包裹） */
  const renderAssetRow = (it: SkillItem) => {
    const rv = it.review
    const open = !!expanded[it.id]
    let cur = ''
    if (grouped) {
      cur = '未分类'
      for (const g of classView ?? []) {
        if ((g.skills ?? []).some((s) => s?.id === it.id)) { cur = g.name; break }
      }
    }
    return (
      <tr key={it.id} id={`skill-row-${it.id}`} className={`border-b border-slate-800/60 transition-colors hover:bg-slate-900/40 ${open ? 'bg-slate-900/60' : ''}`}>
        <td className="px-2 py-2">
          <button type="button" onClick={() => toggleRow(it.id)} className="text-slate-500 hover:text-slate-300" title={open ? '收起报告' : '展开评审-消化报告'}>
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
        </td>
        <td className="max-w-[24rem] px-2 py-2">
          <div className="truncate font-medium text-slate-200">{it.name || it.id}</div>
          <div className="truncate text-[10px] text-slate-500">{it.description ?? ''}</div>
          {/* 触发条件：发布+启用才参与运行时注入；命中方式=语义匹配(描述/标签/内容) */}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {it.enabled && (it.status === 'published' || it.status === 'approved')
              ? chip('触发·注入生效', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60')
              : chip(it.enabled ? '已启用·待发布不触发' : '停用·不触发', 'bg-slate-500/10 text-slate-400 border-slate-700')}
            {chip(['python', 'javascript', 'shell', 'js'].includes(String(it.content_type ?? '').toLowerCase())
              ? '脚本型·命中后执行脚本'
              : `指令型·命中后注入提示`, 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60')}
            {it.is_sensitive && chip('敏感·隔离注入', 'bg-amber-500/10 text-amber-400 border-amber-800/60')}
            {(it.tags ?? []).slice(0, 3).map((t) => chip(`#${t}`, 'bg-indigo-500/10 text-indigo-300 border-indigo-800/50'))}
          </div>
        </td>
        <td className="px-2 py-2">
          <div className="flex flex-wrap gap-1">
            {statusChip(it.status)}
            {it.enabled ? chip('启用', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60') : chip('停用', 'bg-slate-500/10 text-slate-400 border-slate-700')}
          </div>
        </td>
        <td className="px-2 py-2">
          {rv?.auto_assessed
            ? (rv.digest_verdict === 'block'
              ? chip('阻断·待复核', 'bg-red-500/15 text-red-400 border-red-800')
              : chip('已自动评估', 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60'))
            : chip('未评估', 'bg-slate-500/10 text-slate-400 border-slate-700')}
        </td>
        <td className="px-2 py-2 font-mono text-[10px] text-slate-400">
          {Math.round(rv?.security_score ?? 0)}/{Math.round(rv?.quality_score ?? 0)}/{Math.round(rv?.compatibility_score ?? 0)}
        </td>
        <td className="px-2 py-2">
          <div className="flex flex-wrap items-center gap-1">
            {grouped && classOptions.length > 0 && (
              <select value={cur} onChange={(e) => void moveClass(it, e.target.value)}
                title="人工移动到其它分类（之后自动重判不再覆盖人工选择）"
                className="max-w-[7.5rem] rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px] text-slate-300 outline-none focus:border-cyan-600">
                {classOptions.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            )}
            <button type="button" className={BTN_EM} onClick={() => void digestOne(it.id)} disabled={busy !== ''} title="执行完整评审-消化（三审+扩展评估）">
              <Zap size={11} /> 评审-消化
            </button>
            <button type="button" className={BTN} onClick={() => setAdviceTarget(it)} disabled={busy !== ''} title="学习/迭代建议：参数优化 + 评审改进意见">
              <Lightbulb size={11} /> 建议
            </button>
            <button type="button" className={BTN} onClick={() => setVersionsTarget(it)} disabled={busy !== ''} title="版本历史：升级小/中版本、一键回滚到任意版本">
              版本
            </button>
            <button type="button" className={BTN} onClick={() => setRedraftTarget(it)} disabled={busy !== ''} title="再定义：LLM/规则起草中文说明与展示名，差异预览后应用并重新评审">
              再定义
            </button>
            <button type="button" className={BTN} onClick={() => setCmdTarget(it)} disabled={busy !== ''} title="斜杠命令：info/versions/execute/params 快捷操作">
              命令
            </button>
            <button type="button" className={BTN_EM} onClick={() => setPublishTarget(it)} disabled={busy !== ''} title="发布前人工复核（通过/强制并写审计）">
              <Rocket size={11} /> 发布
            </button>
            <button type="button" className={BTN} onClick={() => toggle(it)} disabled={busy !== ''}>
              {it.enabled ? '停用' : '启用'}
            </button>
            <button type="button" className={BTN_RED} onClick={() => void remove(it)} disabled={busy !== ''} title="从资产库删除">
              <Trash2 size={11} />
            </button>
          </div>
        </td>
      </tr>
    )
  }

  /** 表体：分类折叠模式（同类分组头 + 可折叠行）或平铺模式 */
  const renderBody = () => {
    if (!grouped) return items.map(renderAssetRow)
    if (!classView) {
      return (
        <tr><td colSpan={6} className="px-2 py-4 text-[11px] text-slate-500">
          <Loader2 size={12} className="mr-1 inline animate-spin" /> 分类视图加载中…
        </td></tr>
      )
    }
    const idCls = new Map<string, string>()
    for (const g of classView) for (const s of g.skills ?? []) if (s?.id) idCls.set(s.id, g.name)
    const out: React.ReactElement[] = []
    const pushGroup = (name: string, members: SkillItem[]) => {
      if (members.length === 0) return
      const isOpen = !collapsedCls[name]
      const g = classView.find((x) => x.name === name)
      out.push(
        <tr key={`g-${name}`} className="bg-slate-900/85">
          <td colSpan={6} className="px-1.5 py-1">
            <button type="button" onClick={() => toggleCls(name)} className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-slate-800/60" title={isOpen ? '折叠该类' : '展开该类'}>
              {isOpen ? <ChevronDown size={13} className="shrink-0 text-slate-400" /> : <ChevronRight size={13} className="shrink-0 text-slate-400" />}
              <ClassIcon name={name} size={13} className="shrink-0 text-cyan-400" />
              <span className="text-[11px] font-medium text-slate-100">{name}</span>
              <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">{members.length}</span>
              {g?.auto && chip('自动建类', 'bg-violet-500/10 text-violet-300 border-violet-800/60')}
            </button>
          </td>
        </tr>
      )
      if (isOpen) members.forEach((m) => out.push(renderAssetRow(m)))
    }
    for (const g of classView) pushGroup(g.name, items.filter((it) => idCls.get(it.id) === g.name))
    pushGroup('未分类', items.filter((it) => !idCls.has(it.id)))
    return out
  }

  /** 动态行 → 定位资产库技能：展开该行报告并滚动到可视区（折叠模式下先展开所属类） */
  const handlePickSkill = (skillId: string) => {
    const it = items.find((x) => x.id === skillId)
    if (!it) {
      try { void navigator.clipboard?.writeText(skillId) } catch { /* 忽略 */ }
      setMsg(`技能「${skillId}」不在资产库（可能仅为运行时技能或已删除）。id 已复制，可到上方「LLM 技能」分类里查看。`)
      return
    }
    setExpanded((p) => ({ ...p, [skillId]: true }))
    if (grouped) {
      for (const g of classView ?? []) {
        if ((g.skills ?? []).some((s) => s?.id === skillId)) {
          setCollapsedCls((p) => ({ ...p, [g.name]: false }))
          break
        }
      }
    }
    setMsg(`已定位技能「${it.name || skillId}」，下方报告已展开。`)
    window.setTimeout(() => {
      document.getElementById(`skill-row-${skillId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 80)
  }

  return (
    <div className="mt-5">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-slate-200">
          <ShieldCheck size={14} className="text-emerald-400" />
          技能资产库 · 评审-消化管线（skills-mgmt）
        </h3>
        <span className="text-[11px] text-slate-500">
          新建/外来技能自动评估（权限 · 攻击面 · 数据合规 + 兼容性/重叠/资源/交互），通过发布即成为运行时能力
        </span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <button type="button" className={BTN_EM} onClick={() => setCreateOpen(true)}>
            <Plus size={12} /> 新建技能
          </button>
          <button type="button" className={BTN_EM} onClick={() => setInstallOpen(true)}>
            <PackagePlus size={12} /> 外来安装
          </button>
          <button type="button" className={BTN_EM} onClick={() => setGenOpen(true)} title="把对话提示词中的能力要求自动写成技能草稿（生成后自动评审-消化，可再审核）">
            <Lightbulb size={12} /> 从对话要求生成
          </button>
          <button type="button" className={BTN_EM} onClick={() => setImportOpen(true)} title="把其他 Agent 的技能描述(JSON)自动改写为云枢格式并评审">
            <FileInput size={12} /> 改写导入
          </button>
          <button type="button" className={BTN_EM} onClick={() => void runAll()} disabled={busy !== ''}>
            {busy === '全量自动评审-消化' ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
            全量自动评审-消化
          </button>
          <button type="button" className={BTN} onClick={() => void batchReview()} disabled={busy !== ''}>
            批量审核
          </button>
          <button type="button" className={BTN} onClick={() => setCurateOpen(true)} disabled={busy !== ''} title="对存量老技能一键体检：补齐中文说明/归档停用零使用/给出合并与拆分建议（可自动整理）">
            <ListTree size={12} /> 整理老技能
          </button>
          <button type="button" className={grouped ? BTN_EM : BTN} onClick={toggleGrouped} disabled={busy !== ''}
            title="按自动分类分组，同类可折叠；新技能自动归类（不匹配时自动新建类）">
            <Layers size={12} /> {grouped ? '按分类折叠中' : '按分类折叠'}
          </button>
          {grouped && (
            <button type="button" className={BTN} onClick={() => void rerunAutoClassify()} disabled={busy !== ''}
              title="为尚未归类的技能自动判定（含自动新建类；人工移动过的保留）">
              {busy === '自动分类' ? <Loader2 size={12} className="animate-spin" /> : <Folder size={12} />}
              重新自动分类
            </button>
          )}
          <button type="button" className={BTN} onClick={() => setFeedOpen(true)} title="全部动态：digest 事件 + 人工复核审计 聚合时间线">
            全部动态
          </button>
          <button type="button" className={BTN} onClick={() => void load()} disabled={busy !== ''}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>
      </div>

      {error && <p className="mb-2 text-[11px] text-red-400">{error}</p>}
      {msg && <p className="mb-2 rounded-md border border-cyan-900/60 bg-cyan-950/30 px-2 py-1 text-[11px] text-cyan-300">{msg}</p>}

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
          <Loader2 size={14} className="animate-spin" /> 加载技能资产…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-800 px-4 py-8 text-center text-xs leading-relaxed text-slate-500">
          资产库暂无技能。用「新建技能」沉淀新生能力，或用「外来安装」（github:/url:/local:/registry: 源）把外部技能吃进来——
          二者都会自动进入评审-消化评估，通过后发布即为自身能力。
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900/70 text-[10px] uppercase tracking-wider text-slate-500">
                <th className="w-7 px-2 py-1.5" />
                <th className="px-2 py-1.5 font-medium">技能</th>
                <th className="px-2 py-1.5 font-medium">状态</th>
                <th className="px-2 py-1.5 font-medium">评审</th>
                <th className="px-2 py-1.5 font-medium">安全/质量/兼容</th>
                <th className="px-2 py-1.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {renderBody()}
            </tbody>
          </table>
        </div>
      )}

      {items.map((it) => expanded[it.id] && (
        <ReportPanel key={`report-${it.id}`} item={it} onClose={() => toggleRow(it.id)} />
      ))}

      {createOpen && (
        <CreateModal onClose={() => setCreateOpen(false)} onDone={() => { setCreateOpen(false); void load() }} />
      )}
      {installOpen && (
        <InstallModal onClose={() => setInstallOpen(false)} onDone={() => { setInstallOpen(false); void load() }} />
      )}
      {publishTarget && (
        <PublishModal
          item={publishTarget}
          onClose={() => setPublishTarget(null)}
          onConfirm={(reason) => confirmPublish(publishTarget, reason)}
        />
      )}
      {adviceTarget && (
        <AdviceModal item={adviceTarget} onClose={() => setAdviceTarget(null)} />
      )}
      {versionsTarget && (
        <VersionsModal item={versionsTarget} onClose={() => setVersionsTarget(null)}
          onDone={() => { setVersionsTarget(null); void load() }} />
      )}
      {redraftTarget && (
        <RedraftModal item={redraftTarget} onClose={() => setRedraftTarget(null)}
          onDone={() => { setRedraftTarget(null); void load(); void loadEvents() }} />
      )}
      {genOpen && (
        <GenerateRequirementModal onClose={() => setGenOpen(false)} onDone={() => { setGenOpen(false); void load(); void loadEvents() }} />
      )}
      {importOpen && (
        <ImportModal onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); void load(); void loadEvents() }} />
      )}
      {curateOpen && (
        <CurateModal onClose={() => setCurateOpen(false)} onDone={() => { setCurateOpen(false); void load() }} />
      )}
      {feedOpen && <FeedModal onClose={() => setFeedOpen(false)} onPick={handlePickSkill} />}
      {cmdTarget && (
        <CommandModal item={cmdTarget} onClose={() => setCmdTarget(null)} onDone={() => void load()} />
      )}

      {/* 发布审计（人工复核/强制发布记录）可视化 */}
      <AuditPanel />

      {/* digest 结果动态（轻量推送源：轮询 + 未读角标 + 原生通知） */}
      <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/30">
        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          <Zap size={12} className="text-cyan-400" />
          <span className="text-xs font-medium text-slate-200">消化动态</span>
          <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">{events.length}</span>
          {unread > 0 && (
            <span className="rounded-full bg-red-500/20 px-1.5 text-[10px] font-semibold text-red-300" title={`${unread} 条未读（在技能中心内每 15s 轮询；授权后新结果会发系统通知）`}>
              {unread} 新
            </span>
          )}
          <span className="text-[10px] text-slate-600">评估结果推送 · 未读角标与浏览器通知（需授权）</span>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            {unread > 0 && (
              <button type="button" className={BTN} onClick={markEventsSeen} title="把当前全部事件标记为已读">
                全部已读
              </button>
            )}
            <button type="button" className={BTN} onClick={() => void exportOverview()} title="导出审计(人工复核)+消化动态 合并 CSV">
              <Download size={11} /> 导出总览(审计+动态)
            </button>
            <button type="button" className={BTN} onClick={() => void loadEvents()} title="刷新 digest 事件">
              <RefreshCw size={11} /> 刷新
            </button>
          </div>
        </div>
        {events.length > 0 && (
          <div className="border-t border-slate-800 px-3 pb-2 pt-2">
            <div className="flex flex-col gap-1">
              {events.slice(0, 8).map((e, i) => (
                <div key={`${e.ts}-${i}`} className="flex flex-wrap items-center gap-1.5 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[10px] text-slate-400">
                  <span className="font-mono text-[9px] text-slate-600">{e.kind ?? ''}</span>
                  <span className="text-cyan-300">{e.skill_id ?? ''}</span>
                  <span className={e.verdict === 'block' ? 'text-red-400' : 'text-emerald-400'}>
                    {e.verdict === 'block' ? '阻断' : e.verdict === 'ok' ? '通过' : e.verdict ?? ''}
                  </span>
                  <span className="max-w-[28rem] truncate text-slate-500">{e.summary}</span>
                  <span className="ml-auto font-mono text-[9px] text-slate-600">{e.ts ?? ''}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── 发布审计记录面板 ──────────────────────────────────────────────
interface AuditRec { ts?: string; event?: string; skill_id?: string; actor?: string; reason?: string }
function AuditPanel() {
  const [records, setRecords] = useState<AuditRec[]>([])
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [filterText, setFilterText] = useState('')
  const [timeSel, setTimeSel] = useState('all')
  const baseRef = useRef<AuditRec[]>([])
  const PAGE = 100

  const merge = (a: AuditRec[], b: AuditRec[]) => {
    const seen = new Set(a.map((x) => `${x.ts ?? ''}|${x.skill_id ?? ''}`))
    return [...a, ...b.filter((x) => !seen.has(`${x.ts ?? ''}|${x.skill_id ?? ''}`))]
  }

  const load = useCallback(async (offset: number, append: boolean) => {
    setError('')
    if (!append) setLoading(true)
    try {
      const r = await hubGet<{ records?: AuditRec[] }>(`/api/skills-mgmt/review/audit?limit=${PAGE}&offset=${offset}`)
      const recs = Array.isArray(r.records) ? r.records : []
      const next = append ? merge(baseRef.current, recs) : recs
      baseRef.current = next
      setRecords(next)
      setHasMore(recs.length === PAGE)
    } catch (e) {
      setError(`发布审计读取失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(0, false) }, [load])

  const cutoff = timeSel === 'all' ? 0 : Date.now() - (timeSel === 'day' ? 864e5 : 7 * 864e5)
  const shown = records.filter((r) => {
    const t = new Date(r.ts ?? '').getTime()
    if (!Number.isFinite(t) || t < cutoff) return false
    if (!filterText.trim()) return true
    return [r.skill_id, r.actor, r.reason, r.ts].some((v) =>
      String(v ?? '').toLowerCase().includes(filterText.trim().toLowerCase()))
  })

  const exportCsv = () => {
    const esc = (s?: string) => `"${String(s ?? '').replace(/"/g, '""')}"`
    const head = ['ts', 'event', 'skill_id', 'actor', 'reason'].join(',')
    const rows = shown.map((r) => [r.ts, r.event, r.skill_id, r.actor, r.reason].map(esc).join(','))
    const blob = new Blob([`\uFEFF${head}\n${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `review-audit-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="mt-5 rounded-lg border border-slate-800 bg-slate-900/30">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ScrollText size={13} className="text-slate-400" />
        <span className="text-xs font-medium text-slate-200">发布审计（人工复核 / 强制发布记录）</span>
        <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">
          {filterText || timeSel !== 'all' ? `${shown.length}/${records.length}` : records.length}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} onClick={(e) => { e.stopPropagation(); void load(0, false) }} title="刷新审计记录">
            <RefreshCw size={11} /> 刷新
          </button>
          {open ? <ChevronDown size={13} className="text-slate-500" /> : <ChevronRight size={13} className="text-slate-500" />}
        </span>
      </button>
      {open && (
        <div className="border-t border-slate-800 px-3 pb-2 pt-2">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <input
              className="w-44 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
              placeholder="按 技能/复核人/说明 筛选…"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
            />
            <select
              className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none"
              value={timeSel}
              onChange={(e) => setTimeSel(e.target.value)}
              title="时间段筛选"
            >
              <option value="all">全部时间</option>
              <option value="day">近 24 小时</option>
              <option value="week">近 7 天</option>
            </select>
            <button type="button" className={BTN} onClick={exportCsv} disabled={shown.length === 0} title="导出当前筛选记录为 CSV">
              <Download size={11} /> 导出 CSV
            </button>
            {hasMore && (
              <button type="button" className={BTN} onClick={() => void load(baseRef.current.length, true)} disabled={loading} title="加载更早的审计记录">
                {loading ? <Loader2 size={11} className="animate-spin" /> : <ChevronRight size={11} />} 加载更多
              </button>
            )}
          </div>
          {error && <p className="mb-1 text-[11px] text-red-400">{error}</p>}
          {loading ? (
            <div className="flex items-center gap-2 py-2 text-[11px] text-slate-500">
              <Loader2 size={12} className="animate-spin" /> 加载中…
            </div>
          ) : shown.length === 0 ? (
            <p className="py-2 text-[11px] text-slate-500">
              {records.length === 0
                ? '暂无人工强制发布记录（未通过评审的发布会被记入 data/skills_mgmt_review_audit.jsonl）。'
                : '没有符合筛选条件的记录。'}
            </p>
          ) : (
            <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
              {shown.map((r, i) => (
                <li key={`${r.ts}-${i}`} className="rounded-md border border-slate-800/70 bg-slate-950/40 px-2.5 py-1.5 text-[11px] leading-relaxed">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] text-slate-500">{r.ts ?? ''}</span>
                    {chip('强制发布', 'bg-amber-500/10 text-amber-400 border-amber-800/60')}
                    <span className="font-medium text-slate-200">{r.skill_id ?? '-'}</span>
                    <span className="text-slate-500">复核人：{r.actor ?? '-'}</span>
                  </div>
                  <div className="mt-0.5 text-slate-400">说明：{r.reason ?? '-'}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

// ── 发布前人工复核弹窗 ────────────────────────────────────────────
function PublishModal({ item, onClose, onConfirm }: {
  item: SkillItem; onClose: () => void; onConfirm: (reason?: string) => void
}) {
  const rv = item.review
  const needsReason = rv?.status !== 'passed' || rv?.digest_verdict === 'block'
  const findings = rv?.findings ?? []
  const blockers = findings.filter((f) => f.severity === 'critical' || f.severity === 'error')
  const [reason, setReason] = useState('')

  return (
    <ModalShell title={`发布前人工复核 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-400">
        发布后技能将进入运行时能力集。请人工复核下方评审-消化结论：系统评估为启发式护栏，
        最终发布决定由复核人做出。
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        {statusChip(item.status ?? '-')}
        {rv?.digest_verdict === 'block'
          ? chip('存在阻断项', 'bg-red-500/15 text-red-400 border-red-800')
          : rv?.status === 'passed'
            ? chip('评审通过', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60')
            : chip('未通过正式评审', 'bg-amber-500/15 text-amber-400 border-amber-800')}
        <span className="font-mono text-slate-500">
          安全 {Math.round(rv?.security_score ?? 0)} · 质量 {Math.round(rv?.quality_score ?? 0)} ·
          兼容 {Math.round(rv?.compatibility_score ?? 0)} · 重复 {Math.round(rv?.duplicate_score ?? 0)}
        </span>
      </div>
      {needsReason && (
        <div className="mb-2 rounded-md border border-amber-800/70 bg-amber-950/30 px-2.5 py-2 text-[11px] text-amber-300">
          该技能尚未通过正式评审（或无 PASSED 审核记录）。如仍要发布，属于<strong>人工强制发布</strong>，
          必须填写复核说明（将写入审计 data/skills_mgmt_review_audit.jsonl）。
        </div>
      )}
      {blockers.length > 0 && (
        <div className="mb-2 rounded-md border border-red-900/70 bg-red-950/30 px-2.5 py-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-red-400">
            阻断项（{blockers.length}）——发布前必须人工裁定
          </div>
          <ul className="max-h-28 space-y-0.5 overflow-y-auto">
            {blockers.map((f, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[11px] text-red-300/90">
                <span className="shrink-0 font-mono text-[10px] text-red-500">{f.code}</span>
                <span>{f.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {needsReason && (
        <Field label="复核说明 *（人工强制发布时必填）">
          <textarea className={INPUT} rows={3} value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例如：已人工审查代码，阻断项为误报 / 使用场景受控……" />
        </Field>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>取消（不发布）</button>
        {!needsReason ? (
          <button type="button" className={BTN_EM} onClick={() => onConfirm()}>
            <Rocket size={12} /> 确认发布
          </button>
        ) : (
          <button type="button" className={BTN_EM} onClick={() => onConfirm(reason.trim())}
            disabled={reason.trim().length < 4}
            title="强制发布需 ≥4 字复核说明（写入审计）">
            <Rocket size={12} /> 强制发布（人工复核通过，写审计）
          </button>
        )}
      </div>
    </ModalShell>
  )
}

// ── 评审-消化报告面板 ──────────────────────────────────────────────
function ReportPanel({ item, onClose }: { item: SkillItem; onClose: () => void }) {
  const rv = item.review
  const findings = rv?.findings ?? []
  const stat = (label: string, v?: number) => (
    <div className="rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1 text-center">
      <div className="text-[9px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="font-mono text-[12px] text-cyan-300">{Math.round(v ?? 0)}</div>
    </div>
  )
  return (
    <div className="mt-1.5 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-200">
          <ShieldCheck size={13} className="text-emerald-400" />
          评审-消化报告 · {item.name || item.id}
          {rv?.digest_verdict === 'block'
            ? chip('存在阻断项 · 需人工复核', 'bg-red-500/15 text-red-400 border-red-800')
            : rv?.auto_assessed ? chip('无阻断项', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60') : null}
          {rv?.summary && <span className="text-[10px] font-normal text-slate-500">{rv.summary}</span>}
        </div>
        <button type="button" onClick={onClose} className="text-slate-500 hover:text-slate-300"><X size={13} /></button>
      </div>
      <div className="mb-2 grid max-w-md grid-cols-4 gap-1.5">
        {stat('综合', rv?.score)}{stat('安全', rv?.security_score)}
        {stat('质量', rv?.quality_score)}{stat('兼容', rv?.compatibility_score)}
      </div>
      {findings.length === 0 ? (
        <p className="text-[11px] text-slate-500">未发现评估问题（重复/安全/质量/兼容）。</p>
      ) : (
        <ul className="max-h-56 space-y-1 overflow-y-auto pr-1">
          {findings.map((f, i) => (
            <li key={`${f.code}-${i}`} className="flex items-start gap-2 text-[11px] leading-relaxed">
              {chip(f.severity ?? 'info', SEV[f.severity ?? 'info'] ?? SEV.info)}
              <span className="shrink-0 font-mono text-[10px] text-slate-500">{f.code}</span>
              <span className="min-w-0 flex-1 text-slate-300">{f.message}</span>
              {f.location && <span className="shrink-0 text-[10px] text-slate-600">{f.location}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── 新建（手写）模态 ─────────────────────────────────────────────
function CreateModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState({ name: '', description: '', content_type: 'markdown', content: '', tags: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const slug = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'skill'
  const submit = async () => {
    if (!form.name.trim() || !form.content.trim()) return
    setBusy(true); setErr('')
    try {
      await hubPost('/api/skills-mgmt/create/manual', {
        id: slug(form.name), name: form.name.trim(), description: form.description.trim(),
        content: form.content, content_type: form.content_type,
        tags: form.tags.split(/[,，\s]+/).filter(Boolean), author: 'workbench', category: 'custom',
      })
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return (
    <ModalShell title="新建技能（手写，创建即自动评审-消化）" onClose={onClose}>
      <Field label="技能名称 *">
        <input className={INPUT} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如 PDF 解析器" />
      </Field>
      <Field label="说明">
        <input className={INPUT} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="用途与场景（影响兼容性/重叠评估）" />
      </Field>
      <Field label="类型">
        <select className={INPUT} value={form.content_type} onChange={(e) => setForm({ ...form, content_type: e.target.value })}>
          <option value="markdown">markdown（指令/说明）</option>
          <option value="python">python（脚本）</option>
          <option value="javascript">javascript</option>
          <option value="shell">shell</option>
          <option value="yaml">yaml</option>
        </select>
      </Field>
      <Field label="标签（逗号分隔）">
        <input className={INPUT} value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="pdf, extract" />
      </Field>
      <Field label="内容 *">
        <textarea rows={7} className={`${INPUT} font-mono`} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })}
          placeholder="技能主体（Markdown 指令或代码）——保存后自动进入安全+兼容评估" />
      </Field>
      {err && <p className="mt-1 text-[11px] text-red-400">{err}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>取消</button>
        <button type="button" className={BTN_EM} onClick={() => void submit()} disabled={busy || !form.name.trim() || !form.content.trim()}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} 创建
        </button>
      </div>
    </ModalShell>
  )
}

// ── 外来安装模态 ─────────────────────────────────────────────────
function InstallModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const submit = async () => {
    if (!source.trim()) return
    setBusy(true); setErr('')
    try {
      await hubPost('/api/skills-mgmt/install', { source: source.trim() })
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return (
    <ModalShell title="外来技能安装（自动评审-消化）" onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        支持 <code className="text-cyan-400">github:user/repo</code>、
        <code className="text-cyan-400">url:https://…</code>、
        <code className="text-cyan-400">local:./路径</code>、
        <code className="text-cyan-400">registry:skill-name</code> 等源；安装后自动进入评估管线。
      </p>
      <input className={INPUT} value={source} onChange={(e) => setSource(e.target.value)} placeholder="github:user/repo 或 url:https://…" />
      {err && <p className="mt-1 text-[11px] text-red-400">{err}</p>}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>取消</button>
        <button type="button" className={BTN_EM} onClick={() => void submit()} disabled={busy || !source.trim()}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : <PackagePlus size={12} />} 安装并评估
        </button>
      </div>
    </ModalShell>
  )
}

// ── 老技能整理（体检计划 + 安全自动执行）──────────────────────────────
function CurateModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [res, setRes] = useState<{
    issues?: number; plan?: { id: string; name?: string; issues?: string[] }[]
    applied_count?: number; applied?: { id?: string; action?: string; detail?: string }[]
  } | null>(null)
  const [busy, setBusy] = useState('')
  const [runMsg, setRunMsg] = useState('')
  const [dups, setDups] = useState<{ skill_a?: string; skill_b?: string; name_a?: string; name_b?: string; jaccard?: number }[]>([])
  const [backs, setBacks] = useState<{ merge_id?: string; ts?: string; src_id?: string; dst_id?: string; src_name?: string; dst_name?: string; src_content_len?: number; dst_content_len?: number }[]>([])

  const loadDups = async () => {
    try {
      const r = await hubGet<{ duplicates?: { skill_a?: string; skill_b?: string; name_a?: string; name_b?: string; jaccard?: number }[] }>('/api/skills-mgmt/duplicates?min_jaccard=0.7')
      setDups(Array.isArray(r.duplicates) ? r.duplicates : [])
    } catch { setDups([]) }
  }

  const loadBacks = async () => {
    try {
      const r = await hubGet<{ backups?: typeof backs }>('/api/skills-mgmt/digest/merge-backups?limit=50')
      setBacks(Array.isArray(r.backups) ? r.backups : [])
    } catch { setBacks([]) }
  }

  const safeMerge = async (srcId: string, dstId: string) => {
    if (!window.confirm(`安全合并：先备份快照，再删除「${srcId}」并入「${dstId}」？（可随时撤销）`)) return
    setBusy('3'); setRunMsg('')
    try {
      const r = await hubPost<{ merge_id?: string; merged_id?: string }>('/api/skills-mgmt/digest/merge-safe', { src_id: srcId, dst_id: dstId, strategy: 'auto' })
      setRunMsg(`已安全合并：${srcId} → ${dstId}${r?.merge_id ? `（备份 ${r.merge_id}）` : ''}。可在下方「合并备份」中撤销。`)
      onDone()
      void loadDups()
      void loadBacks()
    } catch (e) {
      setRunMsg(`合并失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }

  const undoOne = async (mid: string) => {
    if (!mid) return
    if (!window.confirm('撤销该次安全合并？（恢复被删技能并回滚保留方）')) return
    setBusy('4'); setRunMsg('')
    try {
      const r = await hubPost<{ restored?: string[] }>('/api/skills-mgmt/digest/merge-undo', { merge_id: mid })
      setRunMsg(`已撤销合并（${mid}），恢复：${(r?.restored ?? []).join('、') || '-'}。`)
      onDone()
      void loadDups()
      void loadBacks()
    } catch (e) {
      setRunMsg(`撤销失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }

  const plan = async () => {
    setBusy('1'); setRunMsg('')
    try { setRes(await hubPost('/api/skills-mgmt/digest/curate?dry_run=1')) }
    catch (e) { setRunMsg(`体检失败：${e instanceof Error ? e.message : String(e)}`) }
    finally { setBusy('') }
  }
  const apply = async () => {
    setBusy('2'); setRunMsg('')
    try {
      const r = await hubPost<{ applied_count?: number; applied?: { id?: string; action?: string }[] }>('/api/skills-mgmt/digest/curate?dry_run=0&auto_clean=1')
      setRes(r)
      setRunMsg(`已自动整理 ${r?.applied_count ?? 0} 项：${(r?.applied ?? []).map((a) => `${a.id ?? ''}·${a.action ?? ''}`).join('；') || '无'}。合并/拆分需人工决策，见体检计划。`)
      onDone()
    } catch (e) { setRunMsg(`执行失败：${e instanceof Error ? e.message : String(e)}`) }
    finally { setBusy('') }
  }

  useEffect(() => {
    void plan()
    void loadDups()
    void loadBacks()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <ModalShell title="老技能整理（体检 / 自动整理）" onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        对存量技能自动体检：<strong>缺中文说明</strong>（从内容自动补全）、<strong>停用且零使用</strong>（可归档）、
        <strong>内容重复</strong>（建议合并）、<strong>内容过大</strong>（建议拆分）。安全动作可一键执行，合并/拆分保留建议由你决策。
      </p>
      {busy === '1' && <div className="flex items-center gap-2 py-2 text-[11px] text-slate-500"><Loader2 size={12} className="animate-spin" /> 体检中…</div>}
      {res && (
        <>
          <div className="mb-2 text-[11px] text-slate-400">
            共发现 <span className="font-semibold text-amber-300">{res.issues ?? 0}</span> 项问题
            {res.applied_count != null && res.applied_count > 0 && <span> · 本次已自动处理 {res.applied_count} 项</span>}
          </div>
          {((res.plan ?? []).length === 0) ? (
            <p className="py-3 text-[11px] text-emerald-400">体检通过：未发现需要整理的技能。</p>
          ) : (
            <ul className="max-h-56 space-y-1 overflow-y-auto pr-1">
              {(res.plan ?? []).slice(0, 14).map((p) => (
                <li key={p.id} className="rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-[11px]">
                  <div className="font-medium text-slate-200">{p.name ?? p.id}</div>
                  <ul className="mt-0.5 space-y-0.5 text-[10px] text-slate-400">
                    {(p.issues ?? []).map((t, i) => <li key={i}>· {t}</li>)}
                  </ul>
                </li>
              ))}
              {(res.plan?.length ?? 0) > 14 && <li className="text-[10px] text-slate-500">…其余 {((res.plan?.length ?? 0) - 14)} 项略</li>}
            </ul>
          )}
        </>
      )}
      {runMsg && <p className="mt-2 rounded-md border border-cyan-900/60 bg-cyan-950/30 px-2 py-1 text-[11px] text-cyan-300">{runMsg}</p>}

      {backs.length > 0 && (
        <div className="mt-2 rounded-md border border-slate-800 bg-slate-950/40 p-2">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">合并备份（可撤销，{backs.length}）</span>
            <button type="button" className={BTN} onClick={() => void loadBacks()} disabled={busy !== ''}>
              <RefreshCw size={10} /> 刷新
            </button>
          </div>
          <ul className="max-h-40 space-y-1 overflow-y-auto pr-1">
            {backs.map((b) => (
              <li key={b.merge_id} className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-800/70 bg-slate-900/40 px-2 py-1 text-[11px]">
                <span className="font-mono text-[9px] text-slate-600">{b.ts ?? ''}</span>
                <span className="text-slate-300">{b.src_name ?? b.src_id}</span>
                <span className="text-slate-600">→</span>
                <span className="text-slate-300">{b.dst_name ?? b.dst_id}</span>
                <span className="font-mono text-[9px] text-slate-600">{b.merge_id}</span>
                <button type="button" className={BTN_EM} disabled={busy !== ''}
                  onClick={() => void undoOne(b.merge_id ?? '')} title="恢复被删技能并回滚保留方">
                  撤销
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 重复技能对：一键合并（删除被合并方并入保留方，含反馈回填与版本记录） */}
      {dups.length > 0 && (
        <div className="mt-2 rounded-md border border-slate-800 bg-slate-950/40 p-2">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">
            检测到重复技能对（{dups.length}）——合并前请确认保留方
          </div>
          <ul className="max-h-40 space-y-1 overflow-y-auto pr-1">
            {dups.map((d, i) => {
              const a = d.skill_a ?? ''
              const b = d.skill_b ?? ''
              const la = d.name_a || a
              const lb = d.name_b || b
              return (
                <li key={`${a}-${b}-${i}`} className="flex flex-wrap items-center gap-1.5 rounded-md border border-slate-800/70 bg-slate-900/40 px-2 py-1 text-[11px]">
                  <span className="max-w-[14rem] truncate text-slate-200">{la}</span>
                  <span className="text-slate-600">⇄</span>
                  <span className="max-w-[14rem] truncate text-slate-200">{lb}</span>
                  <span className="font-mono text-[10px] text-amber-300">{(d.jaccard ?? 0).toFixed(0)}%</span>
                  <span className="ml-auto flex gap-1">
                    <button type="button" className={BTN} disabled={busy !== ''} title={`安全合并：备份后删除 ${b} 并入 ${a}`}
                      onClick={() => void safeMerge(b, a)}>保留左 · 合并右</button>
                    <button type="button" className={BTN} disabled={busy !== ''} title={`安全合并：备份后删除 ${a} 并入 ${b}`}
                      onClick={() => void safeMerge(a, b)}>保留右 · 合并左</button>
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
        <button type="button" className={BTN} onClick={() => void plan()} disabled={busy !== ''} title="重新体检">
          <RefreshCw size={11} /> 重新体检
        </button>
        <button type="button" className={BTN_EM} onClick={() => void apply()} disabled={busy !== ''} title="执行安全自动整理：补全中文说明 + 归档停用零使用技能">
          {busy === '2' ? <Loader2 size={11} className="animate-spin" /> : <ListTree size={11} />} 执行自动整理
        </button>
      </div>
    </ModalShell>
  )
}

// ── 学习/迭代建议弹窗（参数优化 + 评审改进意见 + 自动修复建议）────────────────────────
function AdviceModal({ item, onClose }: { item: SkillItem; onClose: () => void }) {
  const [sug, setSug] = useState<string[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [fixList, setFixList] = useState<{ code?: string; severity?: string; finding?: string; fix?: string }[]>([])
  useEffect(() => {
    let cancelled = false
    hubPost<{ suggestions?: string[] }>(`/api/skills-mgmt/${item.id}/optimize`).then((r) => {
      if (cancelled) return
      setSug(Array.isArray(r?.suggestions) ? r.suggestions : [])
    }).catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setBusy(false) })
    hubPost<{ fixes?: { code?: string; severity?: string; finding?: string; fix?: string }[] }>(
      `/api/skills-mgmt/${item.id}/suggest-fix`,
    ).then((r) => { if (!cancelled) setFixList(Array.isArray(r?.fixes) ? r.fixes : []) }).catch(() => {})
    return () => { cancelled = true }
  }, [item.id])
  const rv = item.review
  const digestAdvice: string[] = []
  if (rv) {
    if (rv.digest_verdict === 'block') digestAdvice.push('评审存在阻断项：先处理 critical/error 发现并重新「评审-消化」后再发布。')
    else if (rv.status === 'passed') digestAdvice.push('评审通过：可「发布」使其进入运行时注入生效。')
    else digestAdvice.push('尚未通过正式评审：先「评审-消化」，达标后发布（未通过也可人工复核强制发布并留审计）。')
    if (rv.summary) digestAdvice.push(`评审摘要：${rv.summary}`)
    const warns = (rv.findings ?? []).filter((f) => f.severity === 'warn' || f.severity === 'info').slice(0, 4)
    if (warns.length) digestAdvice.push(`改进建议：${warns.map((f) => f.message).join('；')}`)
  } else digestAdvice.push('尚无评审记录：先「评审-消化」获取权限/合规/兼容性结论。')
  return (
    <ModalShell title={`学习/迭代建议 · ${item.name || item.id}`} onClose={onClose}>
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">评审-消化建议</div>
      <ul className="mb-3 space-y-1">
        {digestAdvice.map((t, i) => (
          <li key={i} className="flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-300">
            <Lightbulb size={11} className="mt-0.5 shrink-0 text-cyan-400" /> {t}
          </li>
        ))}
      </ul>
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">参数优化建议（基于使用指标）</div>
      {err && <p className="text-[11px] text-red-400">{err}</p>}
      {busy ? (
        <div className="flex items-center gap-2 py-2 text-[11px] text-slate-500">
          <Loader2 size={12} className="animate-spin" /> 分析中…
        </div>
      ) : sug.length === 0 ? (
        <p className="py-2 text-[11px] text-slate-500">暂无参数优化建议（需积累足够执行记录后自动给出）。</p>
      ) : (
        <ul className="space-y-1">
          {sug.map((t, i) => (
            <li key={i} className="rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1 text-[11px] text-slate-300">{t}</li>
          ))}
        </ul>
      )}
      {fixList.length > 0 && (
        <>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">自动修复建议（评审 → 对策）</div>
          <ul className="mb-3 space-y-1">
            {fixList.map((f, i) => (
              <li key={i} className="rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1 text-[11px]">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[10px] text-amber-300">{f.code ?? ''}</span>
                  {f.severity && chip(f.severity, 'bg-red-500/10 text-red-400 border-red-800/60')}
                </div>
                <div className="mt-0.5 text-slate-300">{f.fix ?? ''}</div>
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="mt-3 flex justify-end">
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
      </div>
    </ModalShell>
  )
}

// ── 外来其他 Agent 技能改写导入 ──────────────────────────────────────
function ImportModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [jsonText, setJsonText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [okMsg, setOkMsg] = useState('')
  const submit = async () => {
    let obj: Record<string, unknown>
    try {
      obj = JSON.parse(jsonText)
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('需要 JSON 对象')
    } catch (e) {
      setErr(`JSON 解析失败：${e instanceof Error ? e.message : String(e)}`)
      return
    }
    setBusy(true); setErr('')
    try {
      const r = await hubPost<{ skill_id?: string; skill_name?: string; source_format?: string }>(
        '/api/workflow-learning/convert-external-skill',
        { external_data: obj, llm_enabled: false },
      )
      setOkMsg(`已自动改写为云枢格式并注册：「${r?.skill_name ?? r?.skill_id ?? ''}」（skill_id=${r?.skill_id ?? '-'}，格式=${r?.source_format ?? '-'}）——已自动评审-消化，可审核后发布。`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return (
    <ModalShell title="改写导入其他 Agent 的技能（自动改写成云枢格式）" onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        粘贴其他 Agent（如 Claude / GPTs / MCP / 社区）技能的 <strong>JSON 描述</strong>，系统用规则/LLM 翻译成云枢 SKILL
        并注册 → 自动评审-消化（兼容性/重复/权限都会查）。示例：
        <code className="mt-1 block text-cyan-400">{'{ "name": "pdf-extractor", "description": "从 PDF 提取正文", "steps": ["open", "parse"] }'}</code>
      </p>
      <textarea className={`${INPUT} font-mono`} rows={8} value={jsonText} onChange={(e) => setJsonText(e.target.value)}
        placeholder='粘贴 JSON，如 {"name":"…","description":"…","steps":[…] / "prompt":…}' />
      {err && <p className="mt-1 text-[11px] text-red-400">{err}</p>}
      {okMsg && <p className="mt-1 rounded-md border border-emerald-800/60 bg-emerald-950/30 px-2 py-1.5 text-[11px] text-emerald-300">{okMsg}</p>}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {okMsg ? (
          <button type="button" className={BTN_EM} onClick={onDone}><ShieldCheck size={12} /> 完成</button>
        ) : (
          <>
            <button type="button" className={BTN} onClick={onClose}>取消</button>
            <button type="button" className={BTN_EM} onClick={() => void submit()} disabled={busy || !jsonText.trim()}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <FileInput size={12} />} 改写并评审
            </button>
          </>
        )}
      </div>
    </ModalShell>
  )
}

// ── 版本历史 / 回滚 ─────────────────────────────────────────────────
interface SkillVersionLike { version?: string; changelog?: string; created_at?: string; content?: string }
function VersionsModal({ item, onClose, onDone }: { item: SkillItem; onClose: () => void; onDone: () => void }) {
  const [versions, setVersions] = useState<SkillVersionLike[]>([])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try {
      const r = await hubGet<{ versions?: SkillVersionLike[] }>(`/api/skills-mgmt/${item.id}/versions`)
      setVersions(Array.isArray(r.versions) ? r.versions : [])
    } catch (e) {
      setErr(`版本读取失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [item.id])
  useEffect(() => { void load() }, [load])

  const bump = async (kind: 'patch' | 'minor') => {
    setBusy(kind); setErr(''); setMsg('')
    try {
      await hubPost(`/api/skills-mgmt/${item.id}/versions/bump`, { kind, changelog: `UI 升级 ${kind}` })
      setMsg(`已升级 ${kind} 版本。`)
      await load(); onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy('') }
  }
  const roll = async (v: string) => {
    if (!window.confirm(`确定回滚到 ${v}？当前内容将切到该版本快照（可再升级回来）。`)) return
    setBusy('rb'); setErr(''); setMsg('')
    try {
      await hubPost(`/api/skills-mgmt/${item.id}/versions/rollback`, { target_version: v })
      setMsg(`已回滚到 ${v}。`)
      await load(); onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy('') }
  }

  return (
    <ModalShell title={`版本历史 · ${item.name || item.id}（当前 ${item.version ?? '-'}）`} onClose={onClose}>
      {err && <p className="mb-2 text-[11px] text-red-400">{err}</p>}
      {msg && <p className="mb-2 rounded-md border border-emerald-800/60 bg-emerald-950/30 px-2 py-1 text-[11px] text-emerald-300">{msg}</p>}
      <div className="mb-2 flex flex-wrap gap-1.5">
        <button type="button" className={BTN_EM} onClick={() => void bump('patch')} disabled={busy !== ''} title="语义化 PATCH（修复级）">升级 PATCH</button>
        <button type="button" className={BTN_EM} onClick={() => void bump('minor')} disabled={busy !== ''} title="语义化 MINOR（特性级）">升级 MINOR</button>
      </div>
      {busy !== '' && <div className="flex items-center gap-2 py-2 text-[11px] text-slate-500"><Loader2 size={12} className="animate-spin" /> 处理中…</div>}
      {versions.length === 0 ? (
        <p className="py-3 text-[11px] text-slate-500">暂无版本记录（首次保存/升级后出现）。</p>
      ) : (
        <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
          {versions.map((v) => (
            <li key={v.version ?? v.created_at ?? Math.random()}
              className="flex flex-wrap items-center gap-2 rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-[11px]">
              <span className="font-mono text-cyan-300">{v.version}</span>
              {v.version === item.version && <span className="text-[10px] text-slate-500">(当前)</span>}
              <span className="font-mono text-[10px] text-slate-600">{v.created_at ?? ''}</span>
              {v.changelog && <span className="min-w-0 flex-1 truncate text-slate-400">{v.changelog}</span>}
              <span className="text-[10px] text-slate-600">{v.content ? `${v.content.length} 字符` : ''}</span>
              <button type="button" className={BTN} disabled={busy !== '' || v.version === item.version}
                onClick={() => void roll(v.version ?? '')} title={`回滚到 ${v.version}`}>回滚到此版</button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex justify-end">
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
      </div>
    </ModalShell>
  )
}

// ── 再定义草稿（LLM/规则起草 + 差异预览 + 应用）────────────────────────
function RedraftModal({ item, onClose, onDone }: { item: SkillItem; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [llm, setLlm] = useState(false)
  const [cur, setCur] = useState<{ name?: string; description?: string } | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftDesc, setDraftDesc] = useState('')
  const [source, setSource] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const gen = async (useLlm: boolean) => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ current?: { name?: string; description?: string }; proposed?: { name?: string; description?: string }; source?: string }>(
        `/api/skills-mgmt/${item.id}/redraft`, { llm: useLlm })
      setCur(r?.current ?? null)
      setDraftName(r?.proposed?.name ?? item.name ?? '')
      setDraftDesc(r?.proposed?.description ?? '')
      setSource(r?.source ?? 'rules')
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  useEffect(() => { void gen(false) }, [item.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const apply = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await fetch(`/api/skills-mgmt/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: draftName.trim(), description: draftDesc.trim() }),
      })
      if (!r.ok) {
        const b = await r.json().catch(() => null)
        throw new Error(b?.error ?? `HTTP ${r.status}`)
      }
      await hubPost(`/api/skills-mgmt/digest/${item.id}`)
      setMsg('已应用再定义草稿并重新评审-消化。')
      onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  return (
    <ModalShell title={`再定义草稿 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        起草新的中文说明/展示名（LLM 不可用自动回退规则草稿），右侧可编辑；应用后自动重新评审-消化。
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <button type="button" className={BTN} onClick={() => void gen(false)} disabled={busy} title="规则起草（快、确定）">规则起草</button>
        <button type="button" className={BTN_EM} onClick={() => void gen(true)} disabled={busy} title="尝试用 LLM 起草（失败回退规则）">LLM 起草</button>
        {source && <span className="text-[10px] text-slate-600">来源：{source === 'llm' ? 'LLM' : '规则'}</span>}
        <label className="ml-auto flex items-center gap-1 text-[11px] text-slate-400">
          <input type="checkbox" checked={llm} onChange={(e) => { setLlm(e.target.checked); void gen(e.target.checked) }} />
          起草时用 LLM
        </label>
      </div>
      {err && <p className="mb-2 text-[11px] text-red-400">{err}</p>}
      {msg && <p className="mb-2 rounded-md border border-emerald-800/60 bg-emerald-950/30 px-2 py-1 text-[11px] text-emerald-300">{msg}</p>}
      <div className="mb-2 grid gap-2 md:grid-cols-2">
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">当前</div>
          <div className="rounded-md border border-slate-800 bg-slate-950/40 p-2 text-[11px] text-slate-500">
            <div className="mb-1 font-medium text-slate-300">{cur?.name ?? item.name}</div>
            <div className="whitespace-pre-wrap">{cur?.description ?? '（无说明）'}</div>
          </div>
        </div>
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">草稿（可编辑）</div>
          <input className={INPUT} value={draftName} onChange={(e) => setDraftName(e.target.value)} placeholder="展示名" />
          <textarea className={`${INPUT} mt-1 font-mono`} rows={6} value={draftDesc}
            onChange={(e) => setDraftDesc(e.target.value)} placeholder="中文说明（≤160 字）" />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>取消</button>
        <button type="button" className={BTN_EM} onClick={() => void apply()} disabled={busy || !draftDesc.trim()}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />} 应用草稿并评审
        </button>
      </div>
    </ModalShell>
  )
}

// ── 全部动态（聚合时间线：digest 事件 + 人工复核审计）───────────────────
function FeedModal({ onClose, onPick }: { onClose: () => void; onPick?: (skillId: string) => void }) {
  type FeedItem = { kind?: string; ts?: string; skill_id?: string; tag?: string; detail?: string }
  const PAGE = 60
  const [items, setItems] = useState<FeedItem[]>([])
  const [busy, setBusy] = useState(true)
  const [hasMore, setHasMore] = useState(false)
  const [err, setErr] = useState('')
  const [filterId, setFilterId] = useState('')   // 已生效的技能过滤（含匹配）
  const [draft, setDraft] = useState('')
  const baseRef = useRef<FeedItem[]>([])
  const load = useCallback(async (offset: number, append: boolean) => {
    setErr('')
    if (!append) setBusy(true)
    try {
      const q = filterId.trim() ? `&skill_id=${encodeURIComponent(filterId.trim())}` : ''
      const r = await hubGet<{ records?: FeedItem[] }>(`/api/skills-mgmt/digest/feed?limit=${PAGE}&offset=${offset}${q}`)
      const recs = Array.isArray(r.records) ? r.records : []
      const next = append ? [...baseRef.current, ...recs] : recs
      baseRef.current = next
      setItems(next)
      setHasMore(recs.length === PAGE)
    } catch (e) { setErr(`动态加载失败：${e instanceof Error ? e.message : String(e)}`) }
    finally { setBusy(false) }
  }, [filterId])
  useEffect(() => { void load(0, false) }, [load])
  const applyFilter = () => setFilterId(draft.trim())
  return (
    <ModalShell title="全部动态（digest 评估 + 人工复核/发布审计）" onClose={onClose}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-500">已加载 {items.length} 条（时间倒序，分页加载）</span>
        <input
          className={`${INPUT} !mb-0 w-44`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') applyFilter() }}
          placeholder="按技能 id 过滤（含匹配）"
        />
        <button type="button" className={BTN} onClick={applyFilter} title="按技能 id 过滤动态">
          <Filter size={11} /> 过滤
        </button>
        {filterId && (
          <button type="button" className={BTN} onClick={() => { setDraft(''); setFilterId('') }} title="清除过滤">
            <X size={11} /> 清除
          </button>
        )}
        <button type="button" className={BTN} onClick={() => void load(0, false)}>
          <RefreshCw size={11} /> 刷新
        </button>
      </div>
      {filterId && <p className="mb-1 text-[10px] text-cyan-400/80">当前过滤：skill_id 含 “{filterId}”</p>}
      {err && <p className="mb-2 text-[11px] text-red-400">{err}</p>}
      {busy ? (
        <div className="flex items-center gap-2 py-4 text-[11px] text-slate-500"><Loader2 size={12} className="animate-spin" /> 加载中…</div>
      ) : items.length === 0 ? (
        <p className="py-4 text-[11px] text-slate-500">暂无动态。</p>
      ) : (
        <ul className="max-h-[26rem] space-y-1 overflow-y-auto pr-1">
          {items.map((r, i) => (
            <li key={`${r.ts}-${i}`} className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-[11px]">
              {chip(r.kind === 'audit' ? '审计' : '评估', r.kind === 'audit' ? 'bg-amber-500/10 text-amber-400 border-amber-800/60' : 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60')}
              {onPick && r.skill_id ? (
                <button type="button"
                  onClick={() => onPick(r.skill_id ?? '')}
                  className="shrink-0 rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300 transition-colors hover:border-cyan-600 hover:bg-cyan-500/10"
                  title="在下方资产表中定位该技能（存在则展开报告并滚动定位）">
                  {r.skill_id}
                </button>
              ) : (
                <span className="shrink-0 text-slate-300">{r.skill_id ?? '-'}</span>
              )}
              {r.tag && <span className={r.tag === 'block' ? 'text-red-400' : r.tag === '强制发布' ? 'text-amber-300' : 'text-emerald-400'}>{r.tag}</span>}
              <span className="min-w-0 flex-1 text-slate-500">{r.detail}</span>
              <span className="shrink-0 font-mono text-[9px] text-slate-600">{r.ts ?? ''}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {hasMore && (
          <button type="button" className={BTN} onClick={() => void load(baseRef.current.length, true)} disabled={busy}>
            {busy ? <Loader2 size={11} className="animate-spin" /> : <ChevronDown size={11} />} 加载更多
          </button>
        )}
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
      </div>
    </ModalShell>
  )
}

// ── 斜杠命令（Slash 解析器入口：info/versions/execute/params）─────────────
function CommandModal({ item, onClose, onDone }: { item: SkillItem; onClose: () => void; onDone?: () => void }) {
  const [cmd, setCmd] = useState('info')
  const [scriptName, setScriptName] = useState('main.py')
  const [paramsText, setParamsText] = useState('')
  const [out, setOut] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const run = async () => {
    setBusy(true); setErr(''); setOut('')
    const body: Record<string, unknown> = { command: cmd }
    if (cmd === 'execute') {
      body.script_name = scriptName || 'main.py'
      try { body.params = paramsText.trim() ? JSON.parse(paramsText) : {} } catch { setErr('params 需为合法 JSON'); setBusy(false); return }
    }
    if (cmd === 'params') {
      try { body.params = paramsText.trim() ? JSON.parse(paramsText) : {} } catch { setErr('patch 需为合法 JSON'); setBusy(false); return }
    }
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(`/api/skills-mgmt/skill/${item.id}`, body)
      setOut(JSON.stringify(r, null, 2).slice(0, 3000))
      // 成功执行/打补丁后刷新父级行数据（启用状态、描述等可能已变化）
      if (r && r.ok !== false) onDone?.()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return (
    <ModalShell title={`斜杠命令 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] text-slate-500">技能库已接入统一 Slash 解析器（/api/skills-mgmt/skill/&lt;id&gt;）——直接执行 info / versions / execute / params。</p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {['info', 'versions', 'execute', 'params'].map((c) => (
          <button key={c} type="button"
            className={cmd === c ? `${BTN_EM} border-cyan-600/70` : BTN}
            onClick={() => setCmd(c)}>{c}</button>
        ))}
      </div>
      {cmd === 'execute' && (
        <div className="mb-2 grid gap-2 md:grid-cols-2">
          <input className={INPUT} value={scriptName} onChange={(e) => setScriptName(e.target.value)} placeholder="script_name（默认 main.py）" />
          <input className={INPUT} value={paramsText} onChange={(e) => setParamsText(e.target.value)} placeholder='params JSON，如 {"file":"a.pdf"}' />
        </div>
      )}
      {cmd === 'params' && (
        <textarea className={`${INPUT} mb-2 font-mono`} rows={3} value={paramsText} onChange={(e) => setParamsText(e.target.value)}
          placeholder='patch JSON，如 {"description":"…","enabled":true}' />
      )}
      {err && <p className="mb-2 text-[11px] text-red-400">{err}</p>}
      {out && <pre className="mb-2 max-h-56 overflow-auto rounded-md border border-slate-800 bg-slate-950/60 p-2 font-mono text-[10px] text-cyan-200">{out}</pre>}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
        <button type="button" className={BTN_EM} onClick={() => void run()} disabled={busy}>
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} 执行
        </button>
      </div>
    </ModalShell>
  )
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-3 text-sm font-semibold text-slate-100">{title}</h3>
        {children}
      </div>
    </div>
  )
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mb-2.5 block">
      <span className="mb-1 block text-[11px] text-slate-400">{label}</span>
      {children}
    </label>
  )
}


