/**
 * SkillAssessManager —— 技能资产「评审-评估」全生命周期管理（skills-mgmt）
 * ------------------------------------------------------------------
 * 满足：安装 / 创建 / 修改 / 删除 全生命周期 + 自动验证评估流程：
 *   - 新建 / 外来安装 / 修改后，后端自动执行扩展评估（权限/攻击面/数据合规 +
 *     兼容性：原生冲突/操作重叠/资源竞争/交互冲突/重复建议）；本组件直接呈现
 *     评估结论（review verdict + 安全/质量/兼容分数 + findings）。
 *   - 动作：新建(手写)、外来安装(source)、评审-评估(assess)、批量审核、
 *     全量自动评审-评估(run-all)、启停、发布（发布门禁=PASSED）、删除。
 * 说明：与上方「运行时注入启停」表互补——这里是资产库（skills-mgmt 数据），
 * 通过（发布+启用）的资产会进入运行时能力集（上下文注入层消费）。
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown, ChevronRight, Download, Eye, Filter, Folder, Lightbulb, Loader2, Layers, Plus,
  RefreshCw, Rocket, ScrollText, ShieldCheck, Trash2, X, Zap,
} from 'lucide-react'
import { CallabilityBadge, hubGet, hubPost } from '../components/ui'
import { authHeader, getApiToken } from '../../../lib/apiToken'
import GenerateRequirementModal from './generate-requirement-modal'
import ClassIcon from './class-icon'
import ApiTokenPrompt from './api-token-prompt'
import SkillContentModal from './skill-content-modal'
import SkillDetailDrawer from './skill-detail-drawer'
import InstalledExtensions from '../extensions/installed-extensions'
import { fetchCapabilityManifest, indexCallability, type CallabilityInfo } from '@/lib/callability'
import { getEnvelope } from '@/api/envelope'

import {
  SKILLS,
  SKILLS_CLASSIFY_RUN_AUTO,
  SKILLS_MGMT,
  SKILLS_TOGGLE,
  SKILLS_MGMT_ASSESS_EVENTS,
  SKILLS_MGMT_ASSESS_FEED,
  SKILLS_MGMT_ASSESS_RUN_ALL,
  SKILLS_MGMT_ASSESS_STREAM,
  SKILLS_MGMT_CLASSES,
  SKILLS_MGMT_CLASSES_MOVE,
  SKILLS_MGMT_CLASSES_RUN_AUTO,
  SKILLS_MGMT_CREATE_MANUAL,
  SKILLS_MGMT_REVIEW_AUDIT,
  SKILLS_MGMT_REVIEW_BATCH,
  skillAssess,
  skillById,
  skillFixAuto,
  skillOptimize,
  skillPublish,
  skillRedraft,
  skillSlash,
  skillSuggestFix,
  skillToggle,
  skillVersions,
  skillVersionsBump,
  skillVersionsRollback,
} from '@/api/endpoints';

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = `${BTN} border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10`
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const BTN_RED = `${BTN} hover:bg-red-500/10 hover:text-red-400`

interface Finding { severity: string; category: string; code: string; message: string; location?: string }
interface ReviewLike {
  status?: string; score?: number; security_score?: number; quality_score?: number
  compatibility_score?: number; duplicate_score?: number; review_verdict?: string
  auto_assessed?: boolean; summary?: string; findings?: Finding[]
}
interface SkillItem {
  id: string; name: string; description?: string; description_zh?: string; status?: string; enabled?: boolean
  content_type?: string; category?: string; version?: string; review?: ReviewLike
  tags?: string[]; is_sensitive?: boolean; isolation_strategy?: string; source?: string
  class_name?: string; class_auto?: boolean; runtime_only?: boolean; callability?: CallabilityInfo
}

interface RuntimeSkill {
  id: string; name: string; enabled?: boolean; description?: string; description_zh?: string
  class_name?: string; class_auto?: boolean
}

/** 「仅运行时·无资产记录」的成因说明（悬停可见，避免用户以为是坏数据） */
const RUNTIME_ONLY_TIP = '该技能只存在于运行时注册表 / 文件轨（data/skills_repo/<id>/skill.md），未登记进技能资产库（skills_mgmt）：'
  + '所以没有评审、版本、发布记录，也不会被资产库的发布门禁约束；启停走 /api/skills/toggle。'

const SEV: Record<string, string> = {
  critical: 'bg-red-500/15 text-red-400 border-red-800',
  error: 'bg-orange-500/15 text-orange-400 border-orange-800',
  warn: 'bg-amber-500/15 text-amber-400 border-amber-800',
  info: 'bg-slate-500/15 text-slate-400 border-slate-700',
}
const chip = (txt: string, cls: string, title?: string) => (
  <span title={title} className={`inline-flex items-center whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[10px] ${cls}`}>{txt}</span>
)
const statusChip = (s?: string) => chip(
  s ?? '-',
  s === 'published' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-800'
    : s === 'approved' ? 'bg-cyan-500/15 text-cyan-400 border-cyan-800'
      : s === 'pending_review' ? 'bg-amber-500/15 text-amber-400 border-amber-800'
        : s === 'rejected' ? 'bg-red-500/15 text-red-400 border-red-800'
          : 'bg-slate-500/15 text-slate-400 border-slate-700',
)

export default function SkillAssessManager() {
  const [items, setItems] = useState<SkillItem[]>([])
  /** 两个数据源的条数（统一表 = 运行时 ∪ 资产，仅运行时 = 差集），显示在表头便于对账 */
  const [counts, setCounts] = useState<{ runtime: number; assets: number }>({ runtime: 0, assets: 0 })
  const [callability, setCallability] = useState<Record<string, CallabilityInfo>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [createOpen, setCreateOpen] = useState(false)
  const [genOpen, setGenOpen] = useState(false)
  const [publishTarget, setPublishTarget] = useState<SkillItem | null>(null)
  const [contentTarget, setContentTarget] = useState<SkillItem | null>(null)
  const [needAuth, setNeedAuth] = useState(false)
  const [adviceTarget, setAdviceTarget] = useState<SkillItem | null>(null)
  const [versionsTarget, setVersionsTarget] = useState<SkillItem | null>(null)
  const [redraftTarget, setRedraftTarget] = useState<SkillItem | null>(null)
  const [feedOpen, setFeedOpen] = useState(false)
  const [cmdTarget, setCmdTarget] = useState<SkillItem | null>(null)
  /** 详情抽屉的目标（运行 / 脚本 / 参数统计 / 反馈 / 重复 五件事都在抽屉里做） */
  const [detailTarget, setDetailTarget] = useState<SkillItem | null>(null)
  interface AssessmentEv { ts?: string; kind?: string; skill_id?: string; verdict?: string; summary?: string }
  const [events, setEvents] = useState<AssessmentEv[]>([])
  const loadEvents = useCallback(async () => {
    try {
      const r = await hubGet<{ records?: AssessmentEv[] }>(`${SKILLS_MGMT_ASSESS_EVENTS}?limit=10`)
      const recs = Array.isArray(r.records) ? r.records : []
      setEvents(recs)
      const mx = recs.reduce<string>((a, e) => (e.ts && e.ts > a ? e.ts : a), '')
      if (mx) sinceRef.current = mx
    } catch { /* 事件源不可用时不打扰 */ }
  }, [])
  useEffect(() => { void loadEvents() }, [loadEvents])

  // ── 评估动态：未读角标 + 轮询 + 原生通知 ──
  const [lastSeen, setLastSeen] = useState<string>(() => {
    // 兼容旧键（≤1 minor）：yunshu:digest:last-seen 为更名前键名（评审语义）
    try { return localStorage.getItem('yunshu:assess:last-seen') ?? localStorage.getItem('yunshu:digest:last-seen') ?? '' } catch { return '' }
  })
  const lastSeenRef = useRef(lastSeen)
  useEffect(() => { lastSeenRef.current = lastSeen }, [lastSeen])
  const sinceRef = useRef('')
  const unread = events.filter((e) => e.ts && (!lastSeen || e.ts > lastSeen)).length

  // 实时推送：assess/stream 长轮询（服务端 hold 至多 20s，有新事件立即返回）
  useEffect(() => {
    let alive = true
    const poll = async () => {
      let delay = 3000
      try {
        const q = sinceRef.current ? `?since=${encodeURIComponent(sinceRef.current)}&timeout_sec=20` : ''
        const ctrl = new AbortController()
        const tmr = setTimeout(() => ctrl.abort(), 30000)
        // 【2026-10-12 修】本端点带 @require_token（routes_skills_mgmt.py:665），
        // 裸 fetch 不带 Authorization ⇒ 本部署 enforce_all 下被拒 187 次（工作台日志）。
        // 同文件 281 行的 DELETE 早已带 authHeader，这里此前漏了。
        const res = await fetch(`${SKILLS_MGMT_ASSESS_STREAM}${q}`, {
          signal: ctrl.signal,
          headers: authHeader(),
        })
        clearTimeout(tmr)
        if (res.ok) {
          const d = (await res.json()) as { records?: AssessmentEv[] }
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
        new Notification('云枢 · 技能评审-评估', {
          body: `${target.skill_id ?? ''}：${target.verdict === 'block' ? '存在阻断项，需人工复核' : target.verdict === 'ok' ? '评估通过' : '评估完成'}${target.summary ? ` · ${target.summary.slice(0, 60)}` : ''}`,
        })
      }
    } catch { /* 无通知能力/被拒时静默 */ }
  }, [events])

  const markEventsSeen = () => {
    const mx = events.reduce<string>((a, e) => (e.ts && e.ts > a ? e.ts : a), '')
    setLastSeen(mx)
    try { localStorage.setItem('yunshu:assess:last-seen', mx) } catch { /* ignore */ }
  }

  /** 导出总览 CSV：审计（人工复核）+ 评估动态（评估事件）合并 */
  const exportOverview = async () => {
    try {
      const [a, e] = await Promise.all([
        hubGet<{ records?: { ts?: string; event?: string; skill_id?: string; actor?: string; reason?: string }[] }>(`${SKILLS_MGMT_REVIEW_AUDIT}?limit=500`),
        hubGet<{ records?: AssessmentEv[] }>(`${SKILLS_MGMT_ASSESS_EVENTS}?limit=200`),
      ])
      const esc = (s?: string) => `"${String(s ?? '').replace(/"/g, '""')}"`
      const rows: string[] = []
      rows.push(['type', 'ts', 'skill_id', 'actor/verdict', 'detail'].join(','))
      for (const r of a.records ?? []) rows.push(['audit', r.ts, r.skill_id, r.actor, r.reason].map(esc).join(','))
      for (const r of e.records ?? []) rows.push(['assess', r.ts, r.skill_id, r.verdict, r.summary].map(esc).join(','))
      const blob = new Blob([`\uFEFF${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' })
      const el = document.createElement('a')
      el.href = URL.createObjectURL(blob)
      el.download = `skills-assess-overview-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`
      el.click()
      URL.revokeObjectURL(el.href)
    } catch (err) {
      setMsg(`导出总览失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  // 【2026-10-19 · 队列去重；2026-10-22 维护 Tab 退役】「外部导入 · 待放行」不在本页重复实现：
  // 队列 + 审核 + 批量放行 + 驳回 统一在「吞噬 · 草稿评审」（同一份 GET /api/skills-mgmt/queue）。
  // 本页只保留统一表（运行时 + 资产）与单技能生命周期（评估/评审/发布/删除）。
  const load = useCallback(async () => {
    setError('')
    try {
      const [assetR, runtimeR, manifestR] = await Promise.allSettled([
        hubGet<{ items?: SkillItem[] }>(SKILLS_MGMT),
        getEnvelope<{ installed?: RuntimeSkill[]; available?: RuntimeSkill[] }>(SKILLS),
        fetchCapabilityManifest(),
      ])
      const assets = assetR.status === 'fulfilled' && Array.isArray(assetR.value?.items)
        ? assetR.value.items : []
      const runtime = runtimeR.status === 'fulfilled'
        ? (runtimeR.value?.installed?.length ? runtimeR.value.installed : (runtimeR.value?.available ?? []))
        : []
      const rmap = new Map(runtime.map((r) => [r.id, r]))
      const assetIds = new Set(assets.map((a) => a.id))
      const merged: SkillItem[] = assets.map((a) => {
        const r = rmap.get(a.id)
        return {
          ...a,
          description_zh: a.description_zh ?? r?.description_zh,
          class_name: r?.class_name,
          class_auto: r?.class_auto,
          enabled: r?.enabled ?? a.enabled,
        }
      })
      for (const r of runtime) {
        if (assetIds.has(r.id)) continue
        merged.push({
          id: r.id, name: r.name, description: r.description, description_zh: r.description_zh,
          enabled: r.enabled, status: 'runtime_only', runtime_only: true,
          class_name: r.class_name, class_auto: r.class_auto,
        })
      }
      setItems(merged)
      setCounts({ runtime: runtime.length, assets: assets.length })
      if (manifestR.status === 'fulfilled') {
        setCallability(indexCallability(manifestR.value?.runtime_skills, manifestR.value?.manifest?.skills))
      }
    } catch (e) {
      setError(`技能资产加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(''); setMsg(''); setNeedAuth(false)
    try {
      const r = (await fn()) as { ok?: boolean; error?: string } | undefined
      if (r && r.ok === false && r.error) setMsg(`操作未完成：${r.error}`)
      else setMsg(`${label} 完成`)
      await load()
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      if (m.includes('401') || m.includes('未授权')) setNeedAuth(true)
      setMsg(`${label} 失败：${m}`)
    } finally { setBusy(''); void loadEvents() }
  }

  const assessOne = (id: string) => act('评审-评估', () => hubPost(skillAssess(id)))
  const runAll = () => act('全量自动评审-评估', () => hubPost(SKILLS_MGMT_ASSESS_RUN_ALL))
  const batchReview = () => act('批量审核', () => hubPost(SKILLS_MGMT_REVIEW_BATCH))
  /** 发布（先经「人工复核」弹窗确认；未通过评审时须填原因强制发布并写入审计） */
  const confirmPublish = (it: SkillItem, reason?: string) => {
    setPublishTarget(null)
    const needsReason = it.review?.status !== 'passed' || it.review?.review_verdict === 'block'
    void act('发布', () =>
      needsReason
        ? hubPost(`${skillPublish(it.id)}?force=1&reason=${encodeURIComponent(reason || 'manual_review_passed')}`)
        : hubPost(skillPublish(it.id)))
  }
  const toggle = (it: SkillItem) => act('启停', () => hubPost(skillToggle(it.id), { enabled: !it.enabled }))
  /** 仅运行时技能没有资产记录，启停走 /api/skills/toggle（不是资产库 toggle） */
  const toggleRuntime = (it: SkillItem) => act('启停', () =>
    hubPost(SKILLS_TOGGLE, { id: it.id, enabled: !it.enabled }, getApiToken()))

  const remove = async (it: SkillItem) => {
    if (!window.confirm(`确定从资产库删除技能「${it.name || it.id}」？`)) return
    setBusy('删除'); setError(''); setMsg('')
    try {
      const r = await fetch(skillById(it.id), { method: 'DELETE', headers: authHeader() })
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
      const r = await hubGet<{ groups?: ClassGroup[] }>(SKILLS_MGMT_CLASSES)
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
  /**
   * 一键自动归类：同时驱动**运行时**（/api/skills/classify/run-auto，覆盖 仅运行时 行）
   * 与**资产**（/api/skills-mgmt/classes/run-auto）两个命名空间 —— 统一表把两者并在一张表里，
   * 只跑一侧会留下"点了没反应"的未分类行。两侧都失败才算失败，单侧失败如实报。
   */
  const rerunAutoClassify = async () => {
    setBusy('自动分类'); setMsg(''); setError('')
    type ClassifyResult = { classified?: number; created_classes?: number }
    const [assetR, rtR] = await Promise.allSettled([
      hubPost<ClassifyResult>(SKILLS_MGMT_CLASSES_RUN_AUTO),
      hubPost<ClassifyResult>(SKILLS_CLASSIFY_RUN_AUTO),
    ])
    const part = (label: string, r: PromiseSettledResult<ClassifyResult>) =>
      r.status === 'fulfilled'
        ? `${label} ${r.value?.classified ?? 0} 项`
        : `${label} 失败（${r.reason instanceof Error ? r.reason.message : String(r.reason)}）`
    if (assetR.status === 'rejected' && rtR.status === 'rejected') {
      setMsg(`自动归类失败：${part('资产', assetR)}；${part('运行时', rtR)}`)
    } else {
      const created = (assetR.status === 'fulfilled' ? assetR.value?.created_classes ?? 0 : 0)
        + (rtR.status === 'fulfilled' ? rtR.value?.created_classes ?? 0 : 0)
      setMsg(`自动归类完成：${part('资产', assetR)} / ${part('运行时', rtR)}，自动新建类 ${created} 个。`)
    }
    await Promise.all([load(), loadClasses()])
    setBusy('')
  }
  /** 行内移动技能到其它分类（人工选择；后续自动重判不再覆盖） */
  const moveClass = async (it: SkillItem, cls: string) => {
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(SKILLS_MGMT_CLASSES_MOVE, { skill_id: it.id, class_name: cls })
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
  /** 技能 id → 分类名（分类视图为准；视图里没有 = 未分类） */
  const idCls = useMemo(() => {
    const m = new Map<string, string>()
    for (const g of classView ?? []) for (const s of g.skills ?? []) if (s?.id) m.set(s.id, g.name)
    return m
  }, [classView])
  /** 折叠视图里实际会出现的组名（分类组 + 有未分类成员时的「未分类」组） */
  const groupNames = useMemo(() => {
    const names = (classView ?? []).map((g) => g.name)
    if (items.some((it) => !idCls.has(it.id))) names.push('未分类')
    return names
  }, [classView, items, idCls])
  const allCollapsed = grouped && groupNames.length > 0 && groupNames.every((n) => collapsedCls[n])
  const foldAll = (fold: boolean) => setCollapsedCls(Object.fromEntries(groupNames.map((n) => [n, fold])))
  /** 未分类行数（运行时侧）——工具栏「一键归类未分类(N)」的 N */
  const uncCount = items.filter((it) => !it.class_name || it.class_name === '未分类').length
  const runtimeOnlyCount = items.filter((it) => it.runtime_only).length

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
      <Fragment key={it.id}>
      <tr id={`skill-row-${it.id}`} className={`border-b border-slate-800/60 transition-colors hover:bg-slate-900/40 ${open ? 'bg-slate-900/60' : ''}`}>
        <td className="px-2 py-2">
          <button type="button" onClick={() => toggleRow(it.id)} className="text-slate-500 hover:text-slate-300" title={open ? '收起报告' : '展开评审-评估报告'}>
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
        </td>
        <td className="w-[32%] min-w-[15rem] max-w-[26rem] px-2 py-2">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="truncate font-medium text-slate-200">{it.name || it.id}</span>
            {it.class_name && (
              <span className="inline-flex items-center gap-0.5 rounded-full border border-cyan-800/50 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-300">
                <ClassIcon name={it.class_name} size={8} /> {it.class_name}
              </span>
            )}
            <CallabilityBadge info={it.callability ?? callability[it.id]} name={it.name || it.id} />
          </div>
          <div className="truncate text-[10px] text-slate-500">{it.description_zh || it.description || ''}</div>
          {/* 触发条件：发布+启用才参与运行时注入；命中方式=语义匹配(描述/标签/内容) */}
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {it.runtime_only
              ? chip('仅运行时·无资产记录', 'bg-violet-500/10 text-violet-300 border-violet-800/50', RUNTIME_ONLY_TIP)
              : it.enabled && (it.status === 'published' || it.status === 'approved')
                ? chip('触发·注入生效', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60')
                : chip(it.enabled ? '已启用·待发布不触发' : '停用·不触发', 'bg-slate-500/10 text-slate-400 border-slate-700')}
            {!it.runtime_only && chip(['python', 'javascript', 'shell', 'js'].includes(String(it.content_type ?? '').toLowerCase())
              ? '脚本型·命中后执行脚本'
              : `指令型·命中后注入提示`, 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60')}
            {it.is_sensitive && chip('敏感·隔离注入', 'bg-amber-500/10 text-amber-400 border-amber-800/60')}
            {(it.tags ?? []).slice(0, 3).map((t) => chip(`#${t}`, 'bg-indigo-500/10 text-indigo-300 border-indigo-800/50'))}
          </div>
        </td>
        <td className="px-2 py-2">
          <div className="flex flex-wrap gap-1">
            {it.runtime_only ? chip('仅运行时', 'bg-violet-500/10 text-violet-300 border-violet-800/50', RUNTIME_ONLY_TIP) : statusChip(it.status)}
            {it.enabled ? chip('启用', 'bg-emerald-500/10 text-emerald-400 border-emerald-800/60') : chip('停用', 'bg-slate-500/10 text-slate-400 border-slate-700')}
          </div>
        </td>
        <td className="px-2 py-2">
          {it.runtime_only
            ? chip('无资产记录', 'bg-slate-500/10 text-slate-400 border-slate-700', RUNTIME_ONLY_TIP)
            : rv?.auto_assessed
              ? (rv.review_verdict === 'block'
                ? chip('阻断·待复核', 'bg-red-500/15 text-red-400 border-red-800')
                : chip('已自动评估', 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60'))
              : chip('未评估', 'bg-slate-500/10 text-slate-400 border-slate-700')}
        </td>
        <td className="px-2 py-2 font-mono text-[10px] text-slate-400">
          {it.runtime_only ? '—' : `${Math.round(rv?.security_score ?? 0)}/${Math.round(rv?.quality_score ?? 0)}/${Math.round(rv?.compatibility_score ?? 0)}`}
        </td>
        <td className="min-w-[19rem] px-2 py-2">
          <div className="flex flex-wrap items-center gap-1">
            <button type="button" className={BTN} onClick={() => setContentTarget(it)} disabled={busy !== ''} title="查看该技能的具体内容（正文 / 参数 / 标签）">
              <Eye size={11} /> 查看内容
            </button>
            {it.runtime_only ? (
              <button type="button" className={BTN} onClick={() => void toggleRuntime(it)} disabled={busy !== ''}>
                {it.enabled ? '停用' : '启用'}
              </button>
            ) : (
              <>
                {grouped && classOptions.length > 0 && (
                  <select value={cur} onChange={(e) => void moveClass(it, e.target.value)}
                    title="人工移动到其它分类（之后自动重判不再覆盖人工选择）"
                    className="max-w-[7.5rem] rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-[10px] text-slate-300 outline-none focus:border-cyan-600">
                    {classOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                <button type="button" className={BTN} onClick={() => setDetailTarget(it)} disabled={busy !== ''} title="详情：使用说明 / 脚本与沙箱执行 / 参数统计 / 反馈 / 重复技能">
                  <ScrollText size={11} /> 详情
                </button>
                <button type="button" className={BTN_EM} onClick={() => void assessOne(it.id)} disabled={busy !== ''} title="执行完整评审-评估（三审+扩展评估）">
                  <Zap size={11} /> 评审-评估
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
              </>
            )}
          </div>
        </td>
      </tr>
      {/* 报告必须紧跟本行（原来渲染在表格外，展开后报告飘到页尾，看不出属于哪一行） */}
      {open && (
        <tr className="bg-slate-950/40">
          <td colSpan={6} className="px-2 pb-2">
            <ReportPanel item={it} onClose={() => toggleRow(it.id)} />
          </td>
        </tr>
      )}
      </Fragment>
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
          LLM 技能 · 统一表（运行时 + 资产）
        </h3>
        <span className="text-[11px] text-slate-500">
          运行时技能与技能资产按 id 合并：资产行带评审/评分/版本/发布，仅运行时行标注「无资产记录」，仍可查看内容与启停
        </span>
        <span className="rounded-full border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
          title="运行时条数来自 /api/skills，资产条数来自 /api/skills-mgmt；两者差集即「仅运行时·无资产记录」">
          运行时 {counts.runtime} · 资产 {counts.assets} · 仅运行时 {runtimeOnlyCount}
        </span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <button type="button" className={BTN_EM} onClick={() => setCreateOpen(true)}>
            <Plus size={12} /> 新建技能
          </button>
          <button type="button" className={BTN_EM} onClick={() => setGenOpen(true)} title="把对话提示词中的能力要求自动写成技能草稿（生成后自动评审-评估，可再审核）">
            <Lightbulb size={12} /> 从对话要求生成
          </button>
          <button type="button" className={BTN_EM} onClick={() => void runAll()} disabled={busy !== ''}>
            {busy === '全量自动评审-评估' ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
            全量自动评审-评估
          </button>
          <button type="button" className={BTN} onClick={() => void batchReview()} disabled={busy !== ''}>
            批量审核
          </button>
          <button type="button" className={grouped ? BTN_EM : BTN} onClick={toggleGrouped} disabled={busy !== ''}
            title="按自动分类分组，同类可折叠；新技能自动归类（不匹配时自动新建类）">
            <Layers size={12} /> {grouped ? '按分类折叠中' : '按分类折叠'}
          </button>
          {grouped && (
            <button type="button" className={BTN} onClick={() => foldAll(!allCollapsed)} disabled={busy !== ''}
              title={allCollapsed ? '展开全部分类' : '折叠全部分类（只留组头）'}>
              {allCollapsed ? '全部展开' : '全部折叠'}
            </button>
          )}
          <button type="button" className={uncCount > 0 ? `${BTN} border-violet-700/60 text-violet-300 hover:bg-violet-500/10` : BTN}
            onClick={() => void rerunAutoClassify()} disabled={busy !== ''}
            title="为尚未归类的技能自动判定（含自动新建类；人工移动过的保留）。同时驱动运行时与资产两个命名空间">
            {busy === '自动分类' ? <Loader2 size={12} className="animate-spin" /> : <Folder size={12} />}
            {uncCount > 0 ? `一键归类未分类(${uncCount})` : '重新自动分类'}
          </button>
          <button type="button" className={BTN} onClick={() => setFeedOpen(true)} title="评审动态：评估事件 + 人工复核/发布审计聚合时间线">
            评审动态
          </button>
          <button type="button" className={BTN} onClick={() => void load()} disabled={busy !== ''}>
            <RefreshCw size={12} /> 刷新
          </button>
        </div>
      </div>

      {error && <p className="mb-2 text-[11px] text-red-400">{error}</p>}
      {msg && <p className="mb-2 rounded-md border border-cyan-900/60 bg-cyan-950/30 px-2 py-1 text-[11px] text-cyan-300">{msg}</p>}
      {needAuth && <ApiTokenPrompt onSaved={() => { setNeedAuth(false); void load() }} />}

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
          <Loader2 size={14} className="animate-spin" /> 加载技能资产…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-800 px-4 py-8 text-center text-xs leading-relaxed text-slate-500">
          统一表暂无技能。用「新建技能」沉淀新生能力，或到「吞噬 · 获取外来能力」把外来技能/文档/插件包带进来——
          二者都会自动进入评审-评估，通过后发布即为自身能力。
        </div>
      ) : (
        // 窄屏不压扁 6 列（会挤成不可读的一团）：给表一个最小宽度，横向滚动看全列
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900/70 text-[10px] uppercase tracking-wider text-slate-500">
                <th className="w-8 px-2 py-1.5" />
                <th className="w-[30%] min-w-[15rem] px-2 py-1.5 font-medium">技能</th>
                <th className="w-[8.5rem] px-2 py-1.5 font-medium">状态</th>
                <th className="w-[7.5rem] px-2 py-1.5 font-medium">评审</th>
                <th className="w-[7rem] px-2 py-1.5 font-medium">安全/质量/兼容</th>
                <th className="min-w-[19rem] px-2 py-1.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {renderBody()}
            </tbody>
          </table>
        </div>
      )}

      {/* 扩展登记：只显示「统一表里没有」的游离条目（目录已删/漏登记），
          表里已有的同 id 条目不再重复列一遍（否则同一技能在一页出现两次，用户不知道该点哪个）。
          队列/放行不在本页：唯一入口是「吞噬 · 草稿评审」。 */}
      {!loading && (
        <div className="mt-3">
          <InstalledExtensions
            types={['skill']}
            title="扩展登记（统一表之外的游离条目）"
            hint="扩展系统登记为 skill、但统一表里没有对应行的条目（多为目录已删的残留登记）。已入表的技能不在此重复出现；这里可启停或卸载清理。"
            emptyText="没有游离的扩展登记：统一表已覆盖全部扩展注册。"
            excludeIds={items.map((it) => it.id)}
            hideWhenEmpty
          />
        </div>
      )}

      {createOpen && (
        <CreateModal onClose={() => setCreateOpen(false)} onDone={() => { setCreateOpen(false); void load() }} />
      )}
      {publishTarget && (
        <PublishModal
          item={publishTarget}
          onClose={() => setPublishTarget(null)}
          onConfirm={(reason) => confirmPublish(publishTarget, reason)}
        />
      )}
      {adviceTarget && (
        <AdviceModal item={adviceTarget} onClose={() => setAdviceTarget(null)}
          onDone={() => { void load() }} />
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
      {feedOpen && <FeedModal onClose={() => setFeedOpen(false)} onPick={handlePickSkill} />}
      {cmdTarget && (
        <CommandModal item={cmdTarget} onClose={() => setCmdTarget(null)} onDone={() => void load()} />
      )}
      {contentTarget && (
        <SkillContentModal skill={contentTarget} onClose={() => setContentTarget(null)} />
      )}
      {detailTarget && (
        <SkillDetailDrawer skill={detailTarget} onClose={() => setDetailTarget(null)} />
      )}


      {/* 评估结果动态（轻量推送源：轮询 + 未读角标 + 原生通知） */}
      <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/30">
        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          <Zap size={12} className="text-cyan-400" />
          <span className="text-xs font-medium text-slate-200">评估动态</span>
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
            <button type="button" className={BTN} onClick={() => void exportOverview()} title="导出审计(人工复核)+评估动态 合并 CSV">
              <Download size={11} /> 导出总览(审计+动态)
            </button>
            <button type="button" className={BTN} onClick={() => void loadEvents()} title="刷新 评估事件">
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

function PublishModal({ item, onClose, onConfirm }: {
  item: SkillItem; onClose: () => void; onConfirm: (reason?: string) => void
}) {
  const rv = item.review
  const needsReason = rv?.status !== 'passed' || rv?.review_verdict === 'block'
  const findings = rv?.findings ?? []
  const blockers = findings.filter((f) => f.severity === 'critical' || f.severity === 'error')
  const [reason, setReason] = useState('')

  return (
    <ModalShell title={`发布前人工复核 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-400">
        发布后技能将进入运行时能力集。请人工复核下方评审-评估结论：系统评估为启发式护栏，
        最终发布决定由复核人做出。
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
        {statusChip(item.status ?? '-')}
        {rv?.review_verdict === 'block'
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

// ── 评审-评估报告面板 ──────────────────────────────────────────────
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
          评审-评估报告 · {item.name || item.id}
          {rv?.review_verdict === 'block'
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
      await hubPost(SKILLS_MGMT_CREATE_MANUAL, {
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
    <ModalShell title="新建技能（手写，创建即自动评审-评估）" onClose={onClose}>
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
function AdviceModal({ item, onClose, onDone }: { item: SkillItem; onClose: () => void; onDone?: () => void }) {
  const [sug, setSug] = useState<string[]>([])
  const [busy, setBusy] = useState(true)
  const [err, setErr] = useState('')
  const [fixList, setFixList] = useState<{ code?: string; severity?: string; finding?: string; fix?: string }[]>([])
  const [fixBusy, setFixBusy] = useState(false)
  const [fixMsg, setFixMsg] = useState('')
  useEffect(() => {
    let cancelled = false
    hubPost<{ suggestions?: string[] }>(skillOptimize(item.id)).then((r) => {
      if (cancelled) return
      setSug(Array.isArray(r?.suggestions) ? r.suggestions : [])
    }).catch((e) => {
      if (cancelled) return
      const m = e instanceof Error ? e.message : String(e)
      // 后端 4xx（如暂无执行数据）按“暂无建议”优雅降级，不显示红色 HTTP 400
      if (!/^HTTP 4/.test(m)) setErr(m)
    })
      .finally(() => { if (!cancelled) setBusy(false) })
    hubPost<{ fixes?: { code?: string; severity?: string; finding?: string; fix?: string }[] }>(
      skillSuggestFix(item.id),
    ).then((r) => { if (!cancelled) setFixList(Array.isArray(r?.fixes) ? r.fixes : []) }).catch(() => {})
    return () => { cancelled = true }
  }, [item.id])
  const rv = item.review
  /** 评审发现中云枢可直接自动完成的项 */
  const AUTO_FIX_CODES = ['QUAL_NO_SCHEMA', 'QUAL_NO_TAGS', 'DATA_COLLECT_SENSITIVE'] as const
  const autoCodes = [...new Set((rv?.findings ?? [])
    .map((f) => f.code)
    .filter((c): c is typeof AUTO_FIX_CODES[number] => (AUTO_FIX_CODES as readonly string[]).includes(c ?? '')))]
  const runAutoFix = async () => {
    if (autoCodes.length === 0) return
    setFixBusy(true); setFixMsg('')
    try {
      const r = await hubPost<{ applied?: { code?: string; action?: string }[]; applied_codes?: string[]; already_fixed?: string[]; refreshed?: boolean }>(
        skillFixAuto(item.id), { codes: autoCodes })
      const appliedN = (r?.applied_codes ?? []).length
      const done = (r?.already_fixed ?? []).length
      setFixMsg(`云枢已完成自动修复：应用 ${appliedN} 项${done ? `，另 ${done} 项已就绪无需改动` : ''}${r?.refreshed ? '，并已重新评审（建议关闭后重新打开查看最新结论）' : ''}。`)
      onDone?.()
    } catch (e) {
      setFixMsg(`自动修复失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setFixBusy(false) }
  }
  const reviewAdvice: string[] = []
  if (rv) {
    if (rv.review_verdict === 'block') reviewAdvice.push('评审存在阻断项：先处理 critical/error 发现并重新「评审-评估」后再发布。')
    else if (rv.status === 'passed') reviewAdvice.push('评审通过：可「发布」使其进入运行时注入生效。')
    else reviewAdvice.push('尚未通过正式评审：先「评审-评估」，达标后发布（未通过也可人工复核强制发布并留审计）。')
    if (rv.summary) reviewAdvice.push(`评审摘要：${rv.summary}`)
    const warns = (rv.findings ?? []).filter((f) => f.severity === 'warn' || f.severity === 'info').slice(0, 4)
    if (warns.length) reviewAdvice.push(`改进建议：${warns.map((f) => f.message).join('；')}`)
  } else reviewAdvice.push('尚无评审记录：先「评审-评估」获取权限/合规/兼容性结论。')
  return (
    <ModalShell title={`学习/迭代建议 · ${item.name || item.id}`} onClose={onClose}>
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-500">评审-评估建议</div>
      <ul className="mb-3 space-y-1">
        {reviewAdvice.map((t, i) => (
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
      {autoCodes.length > 0 && (
        <div className="mb-3 rounded-md border border-emerald-800/60 bg-emerald-950/20 px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-medium text-emerald-300">云枢可自动完成（{autoCodes.length} 项）</span>
            <span className="text-[10px] text-emerald-500/80">
              {autoCodes.map((c) => c === 'QUAL_NO_SCHEMA' ? '补 config_schema' : c === 'QUAL_NO_TAGS' ? '补检索标签' : '标记敏感隔离').join(' · ')}
            </span>
            <button type="button" className={`${BTN_EM} ml-auto`} onClick={() => void runAutoFix()} disabled={fixBusy}
              title="一键让云枢自动补 config_schema / 标签 / is_sensitive，并重新评审">
              {fixBusy ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />} 一键自动修复并重新评审
            </button>
          </div>
          {fixMsg && <p className="mt-1.5 text-[11px] text-emerald-300">{fixMsg}</p>}
        </div>
      )}
      <div className="mt-3 flex justify-end">
        <button type="button" className={BTN} onClick={onClose}>关闭</button>
      </div>
    </ModalShell>
  )
}

interface SkillVersionLike {
  version?: string
  created_at?: string
  changelog?: string
  content?: string
}

function VersionsModal({ item, onClose, onDone }: { item: SkillItem; onClose: () => void; onDone: () => void }) {
  const [versions, setVersions] = useState<SkillVersionLike[]>([])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try {
      const r = await hubGet<{ versions?: SkillVersionLike[] }>(skillVersions(item.id))
      setVersions(Array.isArray(r.versions) ? r.versions : [])
    } catch (e) {
      setErr(`版本读取失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [item.id])
  useEffect(() => { void load() }, [load])

  const bump = async (kind: 'patch' | 'minor') => {
    setBusy(kind); setErr(''); setMsg('')
    try {
      await hubPost(skillVersionsBump(item.id), { kind, changelog: `UI 升级 ${kind}` })
      setMsg(`已升级 ${kind} 版本。`)
      await load(); onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy('') }
  }
  const roll = async (v: string) => {
    if (!window.confirm(`确定回滚到 ${v}？当前内容将切到该版本快照（可再升级回来）。`)) return
    setBusy('rb'); setErr(''); setMsg('')
    try {
      await hubPost(skillVersionsRollback(item.id), { target_version: v })
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
        skillRedraft(item.id), { llm: useLlm })
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
      const r = await fetch(skillById(item.id), {
        method: 'PATCH',
        // 同文件 245 行的 DELETE 已带 authHeader；此处 PATCH 此前漏了
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({ name: draftName.trim(), description: draftDesc.trim() }),
      })
      if (!r.ok) {
        const b = await r.json().catch(() => null)
        throw new Error(b?.error ?? `HTTP ${r.status}`)
      }
      await hubPost(skillAssess(item.id))
      setMsg('已应用再定义草稿并重新评审-评估。')
      onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  return (
    <ModalShell title={`再定义草稿 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        起草新的中文说明/展示名（LLM 不可用自动回退规则草稿），右侧可编辑；应用后自动重新评审-评估。
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

// ── 全部动态（聚合时间线：评估事件 + 人工复核审计）───────────────────
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
      const r = await hubGet<{ records?: FeedItem[] }>(`${SKILLS_MGMT_ASSESS_FEED}?limit=${PAGE}&offset=${offset}${q}`)
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
    <ModalShell title="评审动态（评估 + 发布审计）" onClose={onClose}>
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
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillSlash(item.id), body)
      setOut(JSON.stringify(r, null, 2).slice(0, 3000))
      // 成功执行/打补丁后刷新父级行数据（启用状态、描述等可能已变化）
      if (r && r.ok !== false) onDone?.()
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return (
    <ModalShell title={`斜杠命令 · ${item.name || item.id}`} onClose={onClose}>
      <p className="mb-2 text-[11px] text-slate-500">技能库已接入统一 Slash 解析器（/api/skills-mgmt/slash/&lt;id&gt;）——直接执行 info / versions / execute / params。</p>
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


