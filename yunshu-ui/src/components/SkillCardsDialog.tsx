/**
 * 能力卡总览（两个 Tab：LLM 技能 / 平台能力）
 * ------------------------------------------------------------------
 * Owner 2026-11-01：①「UI 界面必须列出能力卡，使我能知道自身有什么能力」
 *                  ②「能力卡是否包括云枢自身功能的能力？」→ 原来不含，本版补上第二个 Tab。
 *
 * 两个 Tab 刻意分开：
 *   · LLM 技能 = 模型按需加载的能力（data/skills_repo，L1 卡片，来自 /api/skills-mgmt/cards）；
 *   · 平台能力 = 云枢自身功能（功能单位 / 工具 / 调度任务，来自 /api/system/platform-cards）。
 * 混成一张表会让「模型能加载的能力」和「平台提供的功能」互相冒充。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BookOpen, Loader2, Search, Server, X } from 'lucide-react'
import { authHeader } from '../lib/apiToken'
import { SKILLS_MGMT_CARDS, SYSTEM_PLATFORM_CARDS } from '../api/endpoints'

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

type PlatformItem = {
  id: string
  name: string
  summary?: string
  status?: string
  family?: string
  metrics?: Record<string, unknown>
  issues?: string[]
  zero_token?: boolean | null
}

type PlatformGroup = { kind: string; title: string; desc?: string; count: number; items: PlatformItem[] }

type Props = { open: boolean; onClose: () => void }

const STATUS_STYLE: Record<string, string> = {
  ok: 'border-emerald-800/60 text-emerald-300',
  warn: 'border-amber-800/60 text-amber-300',
  never: 'border-slate-700 text-slate-400',
  off: 'border-slate-700 text-slate-500',
  error: 'border-red-800/60 text-red-300',
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
  const [tab, setTab] = useState<'skill' | 'platform'>('skill')
  const [cards, setCards] = useState<Card[]>([])
  const [groups, setGroups] = useState<PlatformGroup[]>([])
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

  const loadPlatform = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(SYSTEM_PLATFORM_CARDS, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setGroups(Array.isArray(j.groups) ? j.groups : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!open) return
    if (tab === 'skill') void loadSkills()
    else void loadPlatform()
  }, [open, tab, loadSkills, loadPlatform])

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
        items: g.items.filter((it) => `${it.id} ${it.name} ${it.summary || ''} ${it.family || ''}`.toLowerCase().includes(kw)),
      }))
      .filter((g) => g.items.length > 0)
  }, [groups, q])

  if (!open) return null

  const platformTotal = groups.reduce((n, g) => n + g.count, 0)

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
            onClick={() => { setTab('platform'); setQ(''); setExpanded(null) }}
            className={tab === 'platform'
              ? 'rounded-md border border-emerald-700/70 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-300'
              : 'rounded-md border border-slate-800 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-slate-800'}
          >
            <Server size={10} className="mr-1 inline" />
            平台能力 {platformTotal ? `(${platformTotal})` : ''}
          </button>
        </div>

        {tab === 'skill' ? (
          <div className="border-b border-slate-800 bg-slate-950/40 px-4 py-2 text-[10px] leading-relaxed text-slate-500">
            <p>
              <strong className="text-slate-300">这张表覆盖什么：</strong>
              只覆盖<strong className="text-cyan-300"> LLM 技能</strong>（
              <code className="text-slate-400">data/skills_repo/&lt;id&gt;/skill.md</code>
              ，模型按需加载的提示 / 行为 / 流程资产），当前 {cards.length} 条。
              分四层加载：<span className="text-slate-400">L0 摘要</span> →
              <strong className="text-slate-300"> L1 能力卡（本表）</strong> →
              <span className="text-slate-400">L2 全文</span> →
              <span className="text-slate-400">L3 脚本资源</span>。
            </p>
            <p className="mt-1">
              <strong className="text-slate-300">云枢平台自身功能不在本 Tab</strong>（服务进程 / 审计链 / 调度 /
              记忆库 / 知识库 / 工具登记 / 工作流 / 监控）—— 它们在旁边的
              <strong className="text-emerald-300">「平台能力」</strong>；也不含只留主轨的两条（
              <code className="text-amber-300">global-core-principles</code> /
              <code className="text-amber-300">skill</code>）。
            </p>
          </div>
        ) : (
          <div className="border-b border-slate-800 bg-slate-950/40 px-4 py-2 text-[10px] leading-relaxed text-slate-500">
            <p>
              <strong className="text-slate-300">平台能力 = 云枢自己提供的功能</strong>（不是模型加载的技能）：
              功能单位（运行状况）+ 工具（模型可调用）+ 调度任务（定时执行）。
              与「系统监控 → 功能单位」同源，所以面板上是黄的，这里也是黄的。
            </p>
          </div>
        )}

        <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2">
          <Search size={12} className="text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={tab === 'skill' ? '按 id / 名称 / 触发条件 / 步骤 过滤…' : '按名称 / 家族 / 说明 过滤…'}
            className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
          />
          <span className="shrink-0 text-[10px] text-slate-500">
            {tab === 'skill'
              ? `${filtered.length} / ${cards.length} 张`
              : `${filteredGroups.reduce((n, g) => n + g.items.length, 0)} / ${platformTotal} 项`}
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
          ) : filteredGroups.length === 0 ? (
            <div className="py-8 text-center text-slate-500">没有匹配的平台能力</div>
          ) : (
            <div className="space-y-4">
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
                          <span className={`shrink-0 rounded border px-1 text-[9px] ${STATUS_STYLE[String(it.status || 'ok')] || 'border-slate-700 text-slate-400'}`}>
                            {it.status || 'ok'}
                          </span>
                          {it.zero_token ? (
                            <span className="shrink-0 rounded border border-cyan-800/60 px-1 text-[9px] text-cyan-300">0-token</span>
                          ) : null}
                        </div>
                        {it.summary ? (
                          <div className="mt-0.5 line-clamp-2 text-[10px] text-slate-500">{it.summary}</div>
                        ) : null}
                        {it.issues && it.issues.length > 0 ? (
                          <div className="mt-0.5 text-[10px] text-amber-300/80">{it.issues[0]}</div>
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
