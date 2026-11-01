/**
 * 能力卡总览（L1 层）
 * ------------------------------------------------------------------
 * Owner 2026-11-01：「UI 界面必须列出能力卡，使我能知道自身有什么能力」。
 * 统一表只有 id/描述/状态，看不出「这条能力到底怎么用」；本对话框按
 * docs/rfc/技能转化器_2026-11-01.md §3 的 L1 口径展示：触发 / 步骤 / 参数 / 示例 / 约束。
 *
 * 数据来自 GET /api/skills-mgmt/cards：有 CARD.md 用卡（layer=card），没有就从
 * skill.md 按标题派生（layer=derived）—— 老技能不必先补文件也能看到卡。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BookOpen, Loader2, Search, X } from 'lucide-react'
import { authHeader } from '../lib/apiToken'
import { SKILLS_MGMT } from '../api/endpoints'

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

type Props = { open: boolean; onClose: () => void }

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
  const [cards, setCards] = useState<Card[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${SKILLS_MGMT}/cards`, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setCards(Array.isArray(j.cards) ? j.cards : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

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

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">能力卡</h2>
            <span className="text-[11px] text-slate-500">
              我到底有哪些能力 —— 触发 / 步骤 / 参数 / 示例 / 约束
            </span>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={14} />
          </button>
        </div>

        {/* 范围说明（Owner 2026-11-01 追问：「能力卡是否包括云枢自身功能的能力？」）
            答案在这块里：本卡只覆盖「LLM 技能」；平台自身功能（服务/审计/调度/记忆/知识库/工具/工作流）
            走各自的专门入口，不混进这张表 —— 否则「模型能加载的能力」和「平台提供的功能」会被读成同一件事。 */}
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
            <strong className="text-slate-300">不包含云枢平台自身功能</strong>（服务进程 / 审计链 / 调度任务 /
            记忆库 / 知识库 / 工具登记 / 工作流 / 监控）—— 它们是<strong className="text-slate-300">平台功能</strong>，
            不是模型加载的能力，入口分别在不同叶子：「功能单位看板」在<strong className="text-slate-300">系统监控</strong>、
            工具在<strong className="text-slate-300">工具与接入</strong>、工作流在<strong className="text-slate-300">工作流</strong>。
          </p>
          <p className="mt-1">
            也不含<strong className="text-slate-300">只留主轨</strong>的两条（
            <code className="text-amber-300">global-core-principles</code> /
            <code className="text-amber-300">skill</code>）—— 它们按 H-3 裁定不建文件轨，不进这个列表。
          </p>
        </div>

        <div className="flex items-center gap-2 border-b border-slate-800 px-4 py-2">
          <Search size={12} className="text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="按 id / 名称 / 触发条件 / 步骤 过滤…"
            className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
          />
          <span className="shrink-0 text-[10px] text-slate-500">
            {filtered.length} / {cards.length} 张
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
              <Loader2 size={12} className="animate-spin" /> 读取能力卡…
            </div>
          ) : filtered.length === 0 ? (
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
                          <span
                            className={
                              c.layer === 'card'
                                ? 'shrink-0 rounded border border-emerald-800/60 px-1 text-[9px] text-emerald-300'
                                : 'shrink-0 rounded border border-slate-700 px-1 text-[9px] text-slate-400'
                            }
                            title={c.layer === 'card' ? '有 CARD.md（转化器产出）' : '无 CARD.md，按 skill.md 标题派生'}
                          >
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
                        <div className="mt-1 text-[10px] text-slate-600">
                          L1 卡片（{Math.round(c.body_chars / 1024)} KB 全文按需展开，见 docs/rfc/技能转化器_2026-11-01.md §3）
                        </div>
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
