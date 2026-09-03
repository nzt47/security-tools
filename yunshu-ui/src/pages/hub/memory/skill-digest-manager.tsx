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
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown, ChevronRight, Download, FileInput, Lightbulb, Loader2, PackagePlus, Plus,
  RefreshCw, Rocket, ScrollText, ShieldCheck, Trash2, X, Zap,
} from 'lucide-react'
import { hubGet, hubPost } from '../../hub/components/ui'

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
  interface DigestEv { ts?: string; kind?: string; skill_id?: string; verdict?: string; summary?: string }
  const [events, setEvents] = useState<DigestEv[]>([])
  const loadEvents = useCallback(async () => {
    try {
      const r = await hubGet<{ records?: DigestEv[] }>('/api/skills-mgmt/digest/events?limit=10')
      setEvents(Array.isArray(r.records) ? r.records : [])
    } catch { /* 事件源不可用时不打扰 */ }
  }, [])
  useEffect(() => { void loadEvents() }, [loadEvents])

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
  const toggleRow = (id: string) => setExpanded((p) => ({ ...p, [id]: !p[id] }))

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
              {items.map((it) => {
                const rv = it.review
                const open = !!expanded[it.id]
                return (
                  <tr key={it.id} className={`border-b border-slate-800/60 transition-colors hover:bg-slate-900/40 ${open ? 'bg-slate-900/60' : ''}`}>
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
                        {chip(`语义匹配·${it.content_type ?? 'markdown'}`, 'bg-cyan-500/10 text-cyan-400 border-cyan-800/60')}
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
                      <div className="flex flex-wrap gap-1">
                        <button type="button" className={BTN_EM} onClick={() => void digestOne(it.id)} disabled={busy !== ''} title="执行完整评审-消化（三审+扩展评估）">
                          <Zap size={11} /> 评审-消化
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
              })}
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
      {genOpen && (
        <GenerateModal onClose={() => setGenOpen(false)} onDone={() => { setGenOpen(false); void load(); void loadEvents() }} />
      )}
      {importOpen && (
        <ImportModal onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); void load(); void loadEvents() }} />
      )}

      {/* 发布审计（人工复核/强制发布记录）可视化 */}
      <AuditPanel />

      {/* digest 结果动态（轻量推送源） */}
      <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/30 px-3 py-2">
        <Zap size={12} className="text-cyan-400" />
        <span className="text-xs font-medium text-slate-200">消化动态</span>
        <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">{events.length}</span>
        <button type="button" className={BTN} onClick={() => void loadEvents()} title="刷新 digest 事件">
          <RefreshCw size={11} /> 刷新
        </button>
        <span className="text-[10px] text-slate-600">自动评估 / 评审-消化结果推送（进行新评估或发布后自动更新）</span>
        {events.length > 0 && (
          <span className="flex w-full flex-wrap items-center gap-1.5">
            {events.map((e, i) => (
              <span key={i} className="inline-flex items-center gap-1 rounded border border-slate-800 bg-slate-950/60 px-1.5 py-0.5 text-[10px] text-slate-400">
                <span className="font-mono text-[9px] text-slate-600">{e.kind ?? ''}</span>
                <span className="text-cyan-300">{e.skill_id ?? ''}</span>
                <span className={e.verdict === 'block' ? 'text-red-400' : 'text-emerald-400'}>
                  {e.verdict === 'block' ? '阻断' : e.verdict === 'ok' ? '通过' : e.verdict ?? ''}
                </span>
                {e.summary && <span className="max-w-[26rem] truncate text-slate-500">{e.summary}</span>}
              </span>
            ))}
          </span>
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

// ── 从对话要求生成技能 ─────────────────────────────────────────────
function GenerateModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState('')
  const [intent, setIntent] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [okMsg, setOkMsg] = useState('')
  const submit = async () => {
    if (!intent.trim()) return
    setBusy(true); setErr('')
    try {
      const r = await hubPost<{ skill?: SkillItem }>('/api/skills-mgmt/create/ai', {
        name: name.trim() || `auto-req-${Date.now() % 1000000}`,
        intent: intent.trim(),
        tags: ['auto', 'requirement'],
        category: 'custom',
      })
      const s = r?.skill
      setOkMsg(s ? `已生成并自动评审-消化：「${s.name}」（${s.id}）——可展开查看报告，通过后发布即成为自身能力。` : '已生成（未返回详情）')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return (
    <ModalShell title="从对话要求生成技能（自动评审-消化）" onClose={onClose}>
      <p className="mb-2 text-[11px] leading-relaxed text-slate-500">
        对话提示词里出现的“能力要求/新的处理规则”可直接写成技能草稿：粘贴要求→生成（AI 生成失败自动回退模板）→
        自动进入权限/合规/兼容性评审；有审核兜底，不满意可编辑或删除。
      </p>
      <Field label="技能名称（可选）">
        <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} placeholder="留空自动命名" />
      </Field>
      <Field label="对话中的要求 / 能力描述 *">
        <textarea className={`${INPUT} font-mono`} rows={5} value={intent} onChange={(e) => setIntent(e.target.value)}
          placeholder="例如：当用户提到『查天气』时先调用 weather_api，超时 5s 内重试一次并返回结构化结果" />
      </Field>
      {err && <p className="mt-1 text-[11px] text-red-400">{err}</p>}
      {okMsg && <p className="mt-1 rounded-md border border-emerald-800/60 bg-emerald-950/30 px-2 py-1.5 text-[11px] text-emerald-300">{okMsg}</p>}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {okMsg ? (
          <button type="button" className={BTN_EM} onClick={onDone}><ShieldCheck size={12} /> 完成</button>
        ) : (
          <>
            <button type="button" className={BTN} onClick={onClose}>取消</button>
            <button type="button" className={BTN_EM} onClick={() => void submit()} disabled={busy || !intent.trim()}>
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />} 生成并评审
            </button>
          </>
        )}
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

