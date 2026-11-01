/**
 * 转化队列（P4）
 * ------------------------------------------------------------------
 * P2（#1398）把外来技能的转化产物落在 `data/ingestion/transforms/<sha256:12>/`
 * （raw / card / compare / review / manifest），但**界面上看不见** —— 用户没法回答
 * 「上一条外来技能被判成 duplicate 还是 conflict、审核有没有 block、最后并进了谁」。
 * 这个对话框就是那份归档的只读视图，外加**反向补丁回滚**。
 *
 * 回滚口径（后端 `agent/skills_mgmt/transform/queue.py`）：
 *   · 只删 P2 写进 `data/skills_repo/` 的文件（into → CARD.md；new → skill.md + CARD.md）；
 *   · 内容必须与归档一致（sha256），被人改过就整体拒绝 —— 不半删；
 *   · 归档目录本身保留（只写 manifest.rolled_back_at），审核证据链不销毁；
 *   · **默认只预览**：先看清"将删除哪些文件"，再点确认（§8 陷阱 31）。
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Archive, ChevronDown, ChevronRight, Loader2, RotateCcw, X } from 'lucide-react'
import { authHeader } from '../lib/apiToken'
import { SKILLS_MGMT_TRANSFORMS, skillTransform, skillTransformRollback } from '../api/endpoints'

type Target = { rel: string; role: string; skill_id: string; state: string }

type Item = {
  transform_id: string
  created_at: string
  artifact: string
  chars: number
  verdict: string
  review_verdict: string
  review_counts: { block: number; warn: number; info: number }
  identical_to: string
  absorbed_into: string
  into: string
  new: string
  next: string[]
  card_title: string
  files: string[]
  targets: Target[]
  revertible: boolean
  revert_reason: string
  rolled_back_at: string
}

type Finding = { rule: string; severity: string; line: number; evidence: string; note: string }

type Detail = {
  ok: boolean
  item: Item
  targets: Target[]
  findings: Finding[]
  compare: { verdict?: string; best?: { skill_id?: string; score?: number } }
  texts: Record<string, string>
}

type Props = { open: boolean; onClose: () => void }

const VERDICT_STYLE: Record<string, string> = {
  duplicate: 'border-amber-800/60 bg-amber-500/10 text-amber-300',
  incremental: 'border-cyan-800/60 bg-cyan-500/10 text-cyan-300',
  conflict: 'border-rose-800/60 bg-rose-500/10 text-rose-300',
  distinct: 'border-violet-800/60 bg-violet-500/10 text-violet-300',
}

const REVIEW_STYLE: Record<string, string> = {
  pass: 'border-emerald-800/60 text-emerald-300',
  warn: 'border-amber-800/60 text-amber-300',
  block: 'border-rose-800/60 text-rose-300',
}

const TARGET_LABEL: Record<string, string> = {
  pending: '将删除',
  missing: '已不在',
  modified: '被改过（拒绝）',
  outside: '越界（拒绝）',
}

function Badge({ text, style }: { text: string; style: string }) {
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${style}`}>{text}</span>
  )
}

export default function TransformQueueDialog({ open, onClose }: Props) {
  const [items, setItems] = useState<Item[]>([])
  const [broken, setBroken] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [preview, setPreview] = useState<{ id: string; files: string[]; hints: string[] } | null>(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(SKILLS_MGMT_TRANSFORMS, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setItems(Array.isArray(j.items) ? j.items : [])
      setBroken(Number(j.broken || 0))
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const toggle = async (id: string) => {
    if (openId === id) {
      setOpenId(null)
      setDetail(null)
      setPreview(null)
      return
    }
    setOpenId(id)
    setPreview(null)
    setNotice('')
    setDetailLoading(true)
    try {
      const r = await fetch(skillTransform(id), { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setDetail(j as Detail)
    } catch (e) {
      setError(String((e as Error).message || e))
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const postRollback = async (id: string, dryRun: boolean) => {
    setBusy(id + (dryRun ? ':preview' : ':apply'))
    setError('')
    try {
      const r = await fetch(skillTransformRollback(id), {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      if (dryRun) {
        setPreview({ id, files: Array.isArray(j.would_delete) ? j.would_delete : [], hints: j.hints || [] })
      } else {
        setPreview(null)
        setNotice(`已回滚 ${id}：删除 ${(j.deleted || []).length} 个文件（归档保留）`)
        setOpenId(null)
        setDetail(null)
        await load()
      }
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setBusy('')
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <Archive size={14} className="text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">转化队列</h2>
            <span className="text-[11px] text-slate-500">外来技能转化归档 · 判定/审核/卡片 · 可回滚</span>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={14} />
          </button>
        </div>

        <div className="border-b border-slate-800 bg-slate-950/40 px-4 py-2 text-[10px] leading-relaxed text-slate-500">
          每条对应 <code className="text-slate-400">data/ingestion/transforms/&lt;sha256:12&gt;/</code>：
          判定（与自身能力逐面对比）、审核（规则 + 行号证据）、卡片草案、落地目标。
          <strong className="text-slate-300"> 回滚只删 P2 写进 skills_repo 的那几个文件</strong>
          ，内容被人改过就整体拒绝；归档保留。
        </div>

        {error && (
          <div className="flex items-start gap-2 border-b border-rose-900/60 bg-rose-950/40 px-4 py-2 text-[11px] text-rose-300">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {notice && (
          <div className="border-b border-emerald-900/60 bg-emerald-950/30 px-4 py-2 text-[11px] text-emerald-300">{notice}</div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {loading && (
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <Loader2 size={12} className="animate-spin" /> 读取中…
            </div>
          )}
          {!loading && items.length === 0 && (
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-6 text-center text-[11px] text-slate-500">
              还没有转化记录。用 <code className="text-slate-400">python scripts/skill_transform.py &lt;路径&gt; --apply</code> 落地一条后再回来。
              {broken > 0 && <div className="mt-2 text-amber-400/80">{broken} 个目录的 manifest 缺失或损坏（已跳过）</div>}
            </div>
          )}

          <ul className="space-y-2">
            {items.map((it) => {
              const expanded = openId === it.transform_id
              return (
                <li key={it.transform_id} className="rounded-lg border border-slate-800 bg-slate-900/50">
                  <button
                    type="button"
                    onClick={() => void toggle(it.transform_id)}
                    className="flex w-full items-start gap-2 px-3 py-2 text-left"
                  >
                    {expanded ? <ChevronDown size={12} className="mt-0.5 text-slate-500" /> : <ChevronRight size={12} className="mt-0.5 text-slate-500" />}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate text-[12px] text-slate-200">{it.card_title || it.transform_id}</span>
                        <Badge text={it.verdict || '未判定'} style={VERDICT_STYLE[it.verdict] || 'border-slate-700 text-slate-400'} />
                        <Badge text={`审核 ${it.review_verdict || '?'}`} style={REVIEW_STYLE[it.review_verdict] || 'border-slate-700 text-slate-400'} />
                        {(it.into || it.new) && <Badge text={`${it.into ? '并入' : '新建'} ${it.into || it.new}`} style="border-slate-700 text-slate-400" />}
                        {it.rolled_back_at && <Badge text="已回滚" style="border-slate-700 text-slate-500" />}
                        {!it.revertible && !it.rolled_back_at && <Badge text="不可回滚" style="border-slate-700 text-slate-500" />}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                        <code>{it.transform_id}</code>
                        <span>{it.created_at}</span>
                        {it.identical_to && <span>最近似：{it.identical_to}</span>}
                        {it.chars > 0 && <span>{it.chars} 字符</span>}
                        <span>block {it.review_counts.block} / warn {it.review_counts.warn}</span>
                      </div>
                    </div>
                  </button>

                  {expanded && (
                    <div className="border-t border-slate-800 px-3 py-2 text-[11px] text-slate-300">
                      {detailLoading && (
                        <div className="flex items-center gap-2 text-slate-400">
                          <Loader2 size={11} className="animate-spin" /> 读取详情…
                        </div>
                      )}

                      {detail && detail.item.transform_id === it.transform_id && (
                        <>
                          <div className="text-[10px] uppercase tracking-wide text-slate-500">落地目标</div>
                          <ul className="mt-1 space-y-0.5">
                            {detail.targets.map((t) => (
                              <li key={t.rel} className="flex items-center gap-2">
                                <code className="text-slate-400">{t.rel}</code>
                                <span className={t.state === 'pending' ? 'text-amber-300' : 'text-slate-500'}>
                                  {TARGET_LABEL[t.state] || t.state}
                                </span>
                                <span className="text-slate-600">({t.role})</span>
                              </li>
                            ))}
                            {detail.targets.length === 0 && <li className="text-slate-500">只落了归档，没有写 skills_repo</li>}
                          </ul>

                          {detail.findings.length > 0 && (
                            <>
                              <div className="mt-2 text-[10px] uppercase tracking-wide text-slate-500">审核发现（行号）</div>
                              <ul className="mt-1 space-y-0.5">
                                {detail.findings.map((f, i) => (
                                  <li key={i} className={f.severity === 'block' ? 'text-rose-300' : f.severity === 'warn' ? 'text-amber-300' : 'text-slate-400'}>
                                    {f.severity} · {f.rule}
                                    {f.line > 0 ? ` · 第 ${f.line} 行` : ''} · {f.note}
                                    {f.evidence ? <code className="ml-1 text-slate-500">{f.evidence}</code> : null}
                                  </li>
                                ))}
                              </ul>
                            </>
                          )}

                          {detail.item.next.length > 0 && (
                            <>
                              <div className="mt-2 text-[10px] uppercase tracking-wide text-slate-500">下一步建议</div>
                              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-slate-400">
                                {detail.item.next.map((t, i) => (
                                  <li key={i}>{t}</li>
                                ))}
                              </ul>
                            </>
                          )}

                          {detail.texts['card.md'] && (
                            <>
                              <div className="mt-2 text-[10px] uppercase tracking-wide text-slate-500">卡片草案（CARD.md）</div>
                              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-950/60 p-2 text-[10px] leading-relaxed text-slate-400">
                                {detail.texts['card.md']}
                              </pre>
                            </>
                          )}

                          <div className="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              disabled={!it.revertible || !!busy}
                              onClick={() => void postRollback(it.transform_id, true)}
                              className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:border-amber-700 hover:bg-amber-500/10 hover:text-amber-300 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {busy === it.transform_id + ':preview' ? <Loader2 size={10} className="animate-spin" /> : <RotateCcw size={10} />}
                              回滚（先预览）
                            </button>
                            {!it.revertible && it.revert_reason && <span className="text-[10px] text-slate-500">{it.revert_reason}</span>}
                          </div>

                          {preview && preview.id === it.transform_id && (
                            <div className="mt-2 rounded border border-amber-900/60 bg-amber-950/20 p-2">
                              <div className="text-[11px] text-amber-300">将删除 {preview.files.length} 个文件（归档保留）：</div>
                              <ul className="mt-1 space-y-0.5">
                                {preview.files.map((f) => (
                                  <li key={f}><code className="text-slate-400">{f}</code></li>
                                ))}
                              </ul>
                              {preview.hints.map((h, i) => (
                                <div key={i} className="mt-1 text-[10px] text-slate-500">{h}</div>
                              ))}
                              <div className="mt-2 flex items-center gap-2">
                                <button
                                  type="button"
                                  disabled={!!busy}
                                  onClick={() => void postRollback(it.transform_id, false)}
                                  className="flex items-center gap-1 rounded border border-rose-800 px-2 py-1 text-[10px] text-rose-300 hover:bg-rose-500/10 disabled:opacity-40"
                                >
                                  {busy === it.transform_id + ':apply' ? <Loader2 size={10} className="animate-spin" /> : <RotateCcw size={10} />}
                                  确认回滚
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setPreview(null)}
                                  className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-400 hover:bg-slate-800"
                                >
                                  取消
                                </button>
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </div>
  )
}
