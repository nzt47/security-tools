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
import { AlertTriangle, Archive, CheckCircle2, ChevronDown, ChevronRight, Loader2, RotateCcw, Sparkles, X } from 'lucide-react'
import { authHeader } from '../lib/apiToken'
import {
  SKILLS_MGMT_TRANSFORMS,
  SKILLS_MGMT_TRANSFORM_REGISTER,
  skillTransform,
  skillTransformRedraft,
  skillTransformRollback,
} from '../api/endpoints'

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

/** S1：统一转化作业（三套入口收敛后的同一种记录；后端 data/transforms/jobs.json） */
type Job = {
  job_id: string
  source: string
  source_ref: string
  form: string
  title: string
  status: string
  target?: { kind?: string; id?: string }
  artifacts?: string
  updated_at?: string
}

type Props = { open: boolean; onClose: () => void }

const JOB_STATUS_STYLE: Record<string, string> = {
  candidate: 'border-slate-700 text-slate-300',
  normalized: 'border-cyan-800/60 text-cyan-300',
  reviewed: 'border-violet-800/60 text-violet-300',
  registered: 'border-emerald-800/60 text-emerald-300',
  enabled: 'border-emerald-700 text-emerald-200',
  archived: 'border-slate-700 text-slate-500',
  failed: 'border-rose-800/60 text-rose-300',
}

const SOURCE_LABEL: Record<string, string> = {
  ingestion: '能力获取',
  transform: '技能转化',
  intake: '入轨',
}

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
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [preview, setPreview] = useState<{ id: string; files: string[]; hints: string[] } | null>(null)
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [redraft, setRedraft] = useState<{
    id: string
    ok: boolean
    reason?: string
    card?: string
    total?: number
    verified?: number
  } | null>(null)

  const [registerJob, setRegisterJob] = useState<Job | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(SKILLS_MGMT_TRANSFORMS, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setItems(Array.isArray(j.items) ? j.items : [])
      setBroken(Number(j.broken || 0))
      setJobs(Array.isArray(j.jobs) ? j.jobs : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const doRegister = async (openPr: boolean) => {
    const job = registerJob
    if (!job) return
    setBusy(job.job_id)
    setNotice('')
    setError('')
    try {
      const r = await fetch(SKILLS_MGMT_TRANSFORM_REGISTER, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: job.job_id, confirm: true, open_pr: openPr }),
      })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || j.code || `HTTP ${r.status}`)
      const pr = j.pr && j.pr.pr_url ? `，PR：${j.pr.pr_url}` : '（未开 PR，改动停在工作区）'
      setNotice(`已登记 ${j.skill_id} → 文件轨${pr}；只登记未启用，启用请到技能中心`)
      setRegisterJob(null)
      await load()
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setBusy('')
    }
  }

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

  const postRedraft = async (id: string, dryRun: boolean) => {
    setBusy(id + (dryRun ? ':redraft' : ':redraft-apply'))
    setError('')
    try {
      const r = await fetch(skillTransformRedraft(id), {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: dryRun }),
      })
      const j = await r.json()
      if (!r.ok || j.ok === false) {
        setRedraft({ id, ok: false, reason: j.reason || j.error || `HTTP ${r.status}` })
        return
      }
      setRedraft({
        id,
        ok: true,
        card: j.card_md || '',
        total: j.stats?.total ?? 0,
        verified: j.stats?.verified ?? 0,
      })
      if (!dryRun) {
        setNotice(`已写入 ${j.written || 'card_llm.md'}（卡片归档；正文与 card.md 未改）`)
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
          {jobs.length > 0 && (
            <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/40">
              <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5">
                <span className="text-[11px] text-slate-300">统一作业（S1 · 三套入口收敛）</span>
                <span className="text-[10px] text-slate-500">{jobs.length} 条</span>
              </div>
              <ul className="divide-y divide-slate-800/70">
                {jobs.map((j) => (
                  <li key={j.job_id} className="flex flex-wrap items-center gap-1.5 px-3 py-1 text-[10px]">
                    <Badge text={SOURCE_LABEL[j.source] || j.source} style="border-slate-700 text-slate-400" />
                    <span className="truncate text-[11px] text-slate-200">{j.title || j.source_ref}</span>
                    <Badge text={j.status} style={JOB_STATUS_STYLE[j.status] || 'border-slate-700 text-slate-400'} />
                    {j.target?.id && (
                      <span className="text-slate-500">{j.form} → {j.target.kind}:{j.target.id}</span>
                    )}
                    {j.form === 'skill' && ['candidate', 'normalized', 'reviewed'].includes(j.status) && (
                      <button type="button"
                        className="rounded border border-emerald-800/70 px-1.5 py-0.5 text-[10px] text-emerald-300 hover:bg-emerald-950/40"
                        onClick={() => { setRegisterJob(j); setNotice('') }}
                        title="把这条草稿登记进文件轨（只登记、不启用）">
                        登记为技能
                      </button>
                    )}
                    <code className="ml-auto text-slate-600">{j.job_id}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
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

          {registerJob && (
            <div className="mb-3 rounded-lg border border-emerald-900/60 bg-emerald-950/20 px-3 py-2">
              <div className="text-[11px] text-emerald-200">
                登记 <span className="font-mono">{registerJob.title || registerJob.source_ref}</span> 为技能？
              </div>
              <div className="mt-1 text-[10px] leading-relaxed text-slate-400">
                只写文件轨（data/skills_repo/&lt;id&gt;/）并跑入轨校验，<b>不启用</b>；目标已存在会被拒绝。
                「只到工作区」需要你之后自己 commit / 开 PR。
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" disabled={!!busy} onClick={() => void doRegister(true)}
                  className="flex items-center gap-1 rounded border border-emerald-800 px-2 py-1 text-[10px] text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40">
                  {busy === registerJob.job_id ? <Loader2 size={10} className="animate-spin" /> : <CheckCircle2 size={10} />}
                  登记并开 PR
                </button>
                <button type="button" disabled={!!busy} onClick={() => void doRegister(false)}
                  className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800 disabled:opacity-40">
                  只登记到工作区
                </button>
                <button type="button" onClick={() => setRegisterJob(null)}
                  className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-400 hover:bg-slate-800">
                  取消
                </button>
              </div>
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
                            <button
                              type="button"
                              disabled={!!busy}
                              onClick={() => void postRedraft(it.transform_id, true)}
                              title="让 LLM 只从原文摘录分节；每条必须能逐字指回原文，验不过的整条丢弃。只写 card_llm.md，不动正文与 card.md。"
                              className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:border-cyan-700 hover:bg-cyan-500/10 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              {busy === it.transform_id + ':redraft' ? <Loader2 size={10} className="animate-spin" /> : <Sparkles size={10} />}
                              LLM 重写（带引用校验）
                            </button>
                          </div>

                          {redraft && redraft.id === it.transform_id && (
                            <div className="mt-2 rounded border border-cyan-900/60 bg-cyan-950/20 p-2">
                              {redraft.ok ? (
                                <>
                                  <div className="text-[11px] text-cyan-300">
                                    verified {redraft.verified} / {redraft.total}（未通过已丢弃）
                                  </div>
                                  <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded border border-slate-800 bg-slate-950/60 p-2 text-[10px] leading-relaxed text-slate-400">
                                    {redraft.card}
                                  </pre>
                                  <div className="mt-2 flex items-center gap-2">
                                    <button
                                      type="button"
                                      disabled={!!busy}
                                      onClick={() => void postRedraft(it.transform_id, false)}
                                      className="rounded border border-cyan-800 px-2 py-1 text-[10px] text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40"
                                    >
                                      写入 card_llm.md
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => setRedraft(null)}
                                      className="rounded border border-slate-700 px-2 py-1 text-[10px] text-slate-400 hover:bg-slate-800"
                                    >
                                      关闭
                                    </button>
                                  </div>
                                </>
                              ) : (
                                <div className="flex items-start gap-2 text-[10px] text-rose-300">
                                  <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                                  <span>LLM 没补上：{redraft.reason}</span>
                                </div>
                              )}
                            </div>
                          )}

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
