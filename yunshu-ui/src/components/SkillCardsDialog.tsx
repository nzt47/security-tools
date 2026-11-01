/**
 * 能力卡总览（两个 Tab：LLM 技能 / 元生功能）
 * ------------------------------------------------------------------
 * Owner 2026-11-01：①「UI 界面必须列出能力卡，使我能知道自身有什么能力」；
 *                  ②「我要的是云枢自身功能，那些可能与技能造成重叠的元生功能」
 *                     —— 平台能力（服务/审计/调度/监控）是运维对象，另开叶子，不混进来。
 *
 * 两个 Tab 的口径：
 *   · LLM 技能   = 模型按需加载的能力（data/skills_repo，L1 卡）；
 *   · 元生功能   = 智能体**天生就会做**的事（内置工具 + 常驻机制），是技能重复的来源；
 *                  并给出「技能 × 元生功能」的疑似重叠清单，供人判断该技能是否在重复造轮子。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BookOpen, Cpu, Loader2, Search, X } from 'lucide-react'
import { authHeader } from '../lib/apiToken'
import { SKILLS_MGMT_CARDS, SKILLS_MGMT_NATIVE } from '../api/endpoints'

type Card = {
  skill_id: string
  name: string
  summary: string
  category?: string
  tags?: string[]
  layer: 'card' | 'derived'
  trigger: string[]
  steps: string[]
  params: string[]
  example: string
  guard: string[]
  sections: string[]
  body_chars: number
}

type NativeItem = { id: string; name: string; summary?: string; status?: string }
type NativeGroup = { kind: string; title: string; desc?: string; count: number; items: NativeItem[] }
type Overlap = { skill_id: string; name?: string; matches: { native_id: string; kind: string; score: number; reason?: string }[] }

type Props = { open: boolean; onClose: () => void }

const STATUS_STYLE: Record<string, string> = {
  ok: 'border-emerald-800/60 text-emerald-300',
  off: 'border-slate-700 text-slate-500',
  warn: 'border-amber-800/60 text-amber-300',
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div className="mt-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{title}</div>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-slate-300">
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  )
}

export default function SkillCardsDialog({ open, onClose }: Props) {
  const [tab, setTab] = useState<'skill' | 'native'>('skill')
  const [cards, setCards] = useState<Card[]>([])
  const [groups, setGroups] = useState<NativeGroup[]>([])
  const [overlaps, setOverlaps] = useState<Overlap[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const loadSkills = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(SKILLS_MGMT_CARDS, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setCards(Array.isArray(j.cards) ? j.cards : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadNative = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(SKILLS_MGMT_NATIVE, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setGroups(Array.isArray(j.groups) ? j.groups : [])
      setOverlaps(Array.isArray(j.overlaps) ? j.overlaps : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    if (tab === 'skill') void loadSkills()
    else void loadNative()
  }, [open, tab, loadSkills, loadNative])

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return cards
    return cards.filter((c) =>
      [c.skill_id, c.name, c.summary, ...(c.tags || []), ...c.trigger, ...c.steps]
        .join(' ')
        .toLowerCase()
        .includes(kw),
    )
  }, [cards, q])

  const filteredGroups = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return groups
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((it) => `${it.id} ${it.name} ${it.summary || ''}`.toLowerCase().includes(kw)),
      }))
      .filter((g) => g.items.length > 0)
  }, [groups, q])

  const filteredOverlaps = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return overlaps
    return overlaps.filter((o) =>
      `${o.skill_id} ${o.name || ''} ${o.matches.map((m) => m.native_id).join(' ')}`.toLowerCase().includes(kw),
    )
  }, [overlaps, q])

  if (!open) return null

  const nativeTotal = groups.reduce((n, g) => n + g.count, 0)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">能力卡</h2>
            <span className="text-[11px] text-slate-500">我到底有哪些能力</span>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={14} />
          </button>
        </div>

        <div className="flex items-center gap-1 border-b border-slate-800 px-4 py-2">
          <button
            type="button"
            onClick={() => { setTab('skill'); setQ(''); setExpanded(null) }}
            className={tab === 'skill'
              ? 'rounded-md border border-cyan-700/70 bg-cyan-500/10 px-2.5 py-1 text-[11px] text-cyan-300'
              : 'rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
          >
            LLM 技能 {cards.length ? `(${cards.length})` : ''}
          </button>
          <button
            type="button"
            onClick={() => { setTab('native'); setQ(''); setExpanded(null) }}
            className={tab === 'native'
              ? 'rounded-md border border-violet-700/70 bg-violet-500/10 px-2.5 py-1 text-[11px] text-violet-300'
              : 'rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
          >
            <Cpu size={10} className="mr-1 inline" />
            元生功能 {nativeTotal ? `(${nativeTotal})` : ''}
          </button>
        </div>

        <div className="border-b border-slate-800 bg-slate-950/40 px-4 py-2 text-[10px] leading-relaxed text-slate-500">
          {tab === 'skill' ? (
            <p>
              <strong className="text-slate-300">只覆盖 LLM 技能</strong>（
              <code className="text-slate-400">data/skills_repo/&lt;id&gt;/skill.md</code>
              ），当前 {cards.length} 条。四层加载：L0 摘要 →
              <strong className="text-slate-300"> L1 能力卡（本表）</strong> → L2 全文 → L3 脚本资源。
              <br />
              平台能力（服务 / 审计 / 调度 / 监控）不在这里 —— 它们是运维对象，归「系统监控」；
              智能体天生就会做的事在旁边的<strong className="text-violet-300">「元生功能」</strong>。
            </p>
          ) : (
            <p>
              <strong className="text-slate-300">元生功能 = 智能体天生就会做的事</strong>（不是技能）：
              内置工具（模型直接可调用）+ 常驻机制（不建文件轨但始终生效）。
              下面一并列出<strong className="text-violet-300">「技能 × 元生功能」的疑似重叠</strong> ——
              某条技能若在重复造轮子，会出现在那张清单里。
            </p>
          )}
        </div>

        <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2">
          <Search size={12} className="text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={tab === 'skill' ? '按 id / 名称 / 触发条件 / 步骤 过滤…' : '按工具名 / 技能 id 过滤…'}
            className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
          />
          <span className="shrink-0 text-[10px] text-slate-500">
            {tab === 'skill'
              ? `${filtered.length} / ${cards.length} 张`
              : `${filteredGroups.reduce((n, g) => n + g.items.length, 0)} / ${nativeTotal} 项`}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[11px] leading-relaxed">
          {error ? (
            <div className="mb-2 flex items-start gap-2 rounded-md border border-red-900/60 bg-red-950/30 px-2.5 py-2 text-red-200">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          {loading ? (
            <div className="flex items-center gap-2 py-8 text-slate-500">
              <Loader2 size={12} className="animate-spin" /> 读取中…
            </div>
          ) : tab === 'skill' ? (
            filtered.length === 0 ? (
              <div className="py-8 text-center text-slate-500">没有匹配的能力卡</div>
            ) : (
              <ul className="space-y-2">
                {filtered.map((c) => {
                  const isOpen = expanded === c.skill_id
                  return (
                    <li key={c.skill_id} className="rounded-lg border border-slate-800 bg-slate-950/40">
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : c.skill_id)}
                        className="flex w-full items-start gap-2 px-3 py-2 text-left"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate font-medium text-slate-100">{c.name}</span>
                            <code className="shrink-0 text-[10px] text-slate-500">{c.skill_id}</code>
                            <span className={c.layer === 'card'
                              ? 'shrink-0 rounded border border-emerald-800/60 px-1 text-[9px] text-emerald-300'
                              : 'shrink-0 rounded border border-slate-700 px-1 text-[9px] text-slate-400'}>
                              {c.layer}
                            </span>
                            <span className="shrink-0 text-[9px] text-slate-600">{Math.round(c.body_chars / 1024)} KB</span>
                          </div>
                          <div className="mt-0.5 line-clamp-2 text-slate-400">{c.summary || '（无描述）'}</div>
                        </div>
                        <span className="shrink-0 text-[10px] text-slate-500">{isOpen ? '收起' : '展开'}</span>
                      </button>
                      {isOpen ? (
                        <div className="border-t border-slate-800 px-3 py-2">
                          <Section title="何时使用 / 触发" items={c.trigger} />
                          <Section title="步骤" items={c.steps} />
                          <Section title="参数" items={c.params} />
                          <Section title="约束 / 反例" items={c.guard} />
                          {c.example ? (
                            <div className="mt-2">
                              <div className="text-[10px] uppercase tracking-wide text-slate-500">示例</div>
                              <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-950 p-2 text-[10px] text-slate-300">
                                {c.example}
                              </pre>
                            </div>
                          ) : null}
                          {c.sections?.length ? (
                            <div className="mt-2 text-[10px] text-slate-500">目录：{c.sections.join(' · ')}</div>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  )
                })}
              </ul>
            )
          ) : (
            <div className="space-y-4">
              {filteredOverlaps.length > 0 ? (
                <div className="rounded-lg border border-violet-900/50 bg-violet-950/20 px-3 py-2">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-[11px] font-medium text-violet-200">疑似重叠：技能 × 元生功能</span>
                    <span className="text-[10px] text-slate-500">{filteredOverlaps.length} 条技能</span>
                    <span className="text-[10px] text-slate-600">· 点名工具=强信号(0.6)；其余看文本覆盖率</span>
                  </div>
                  <ul className="space-y-1">
                    {filteredOverlaps.slice(0, 20).map((o) => (
                      <li key={o.skill_id} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                        <code className="text-slate-300">{o.skill_id}</code>
                        <span className="text-slate-600">→</span>
                        {o.matches.map((m) => (
                          <span key={m.native_id} className="rounded border border-violet-800/60 px-1 text-violet-300">
                            {m.native_id} {m.score}
                            {m.reason === 'explicit_mention' ? ' ·点名' : ''}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {filteredGroups.map((g) => (
                <div key={g.kind}>
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-[11px] font-medium text-slate-200">{g.title}</span>
                    <span className="text-[10px] text-slate-500">{g.count} 项</span>
                    {g.desc ? <span className="text-[10px] text-slate-600">· {g.desc}</span> : null}
                  </div>
                  <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                    {g.items.map((it) => (
                      <li key={`${g.kind}-${it.id}`} className="rounded-md border border-slate-800 bg-slate-950/40 px-2.5 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-slate-200">{it.name}</span>
                          {it.status ? (
                            <span className={`shrink-0 rounded border px-1 text-[9px] ${STATUS_STYLE[it.status] || 'border-slate-700 text-slate-400'}`}>
                              {it.status}
                            </span>
                          ) : null}
                        </div>
                        {it.summary ? (
                          <div className="mt-0.5 line-clamp-2 text-[10px] text-slate-500">{it.summary}</div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
