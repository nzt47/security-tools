/**
 * 外来草稿评审 / 放行（从技能中心·维护整体迁入）。
 *
 * 安全语义保持：阻断项单条不放行；force 豁免要「勾选 → 勾豁免 → 填原因 → 二次确认」；
 * 批量放行只认显式勾选；驳回 = 删除草稿，先 window.confirm。
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Inbox, Loader2, RefreshCw, Trash2 } from 'lucide-react'
import { Badge, ErrorBox, hubDelete, hubGet, hubPost } from '../components/ui'
import { authHeader } from '../../../lib/apiToken'
import {
  INGESTION_DRAFTS,
  ingestionDraft,
  ingestionDraftAutobind,
  ingestionDraftImplement,
  ingestionDraftRegister,
  SKILLS_MGMT_QUEUE,
  SKILLS_MGMT_QUEUE_PUBLISH_BATCH,
  skillById,
  skillPublish,
  skillReview,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
/** 草稿形态 → 人读标签（此前把 skill 也画成「工具」，点「AI 实现」会 400 form-mismatch）。 */
const FORM_LABEL: Record<string, string> = { skill: '技能', tool: '工具', workflow: '工作流' }
/** 生成时间：ISO → 本地「YYYY-MM-DD HH:mm」（解析失败就原样显示，不显示 Invalid Date）。 */
function fmtTime(iso?: string): string {
  const raw = (iso ?? '').trim()
  if (!raw) return ''
  const t = Date.parse(raw)
  if (Number.isNaN(t)) return raw
  const d = new Date(t)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

interface QueueRow {
  id?: string
  name?: string
  source?: string
  review?: { blocked?: boolean; review_verdict?: string } | null
}
interface QueueResult { ok?: boolean; error?: string; total?: number; items?: QueueRow[] }
/** 吞噬骨架草稿（GET /api/ingestion/drafts）：隔离区，不注册、不进技能队列。 */
interface DraftRow {
  draft_id: string
  form?: string
  status?: string
  enabled?: boolean
  skill_id?: string
  skill_name?: string
  created_at?: string
  warnings?: string[]
  draft_dir?: string
  source_ref?: string
  bound_steps?: number
  total_steps?: number
  implemented?: boolean
}

interface BindReport {
  bound: number
  total: number
  model?: string
  /** 其中"没有工具、按 LLM 步骤执行"的步数（need_llm=true） */
  llm_steps?: number
  unbound?: { step_id?: string; reason?: string }[]
}
interface BatchResult { ok?: boolean; error?: string; published?: string[]; failed?: { id?: string; error?: string }[] }

export default function ReviewPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [queue, setQueue] = useState<QueueResult | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [draftErr, setDraftErr] = useState('')
  const [bind, setBind] = useState<{ id: string; report: BindReport } | null>(null)
  const [impl, setImpl] = useState<{
    id: string
    validation: { ok?: boolean; errors?: string[]; warnings?: string[] }
    code: string
  } | null>(null)
  const [sel, setSel] = useState<string[]>([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchForce, setBatchForce] = useState(false)
  const [batchReason, setBatchReason] = useState('')
  const [batchResult, setBatchResult] = useState<BatchResult | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadQueue = useCallback(async () => {
    setBusy('queue'); setErr('')
    try {
      const r = await hubGet<QueueResult>(SKILLS_MGMT_QUEUE + '?limit=100')
      if (r?.ok === false) { bad('读取待放行队列失败', r); setQueue(null); setSel([]) } else {
        setQueue(r); setSel((prev) => prev.filter((id) => (r?.items ?? []).some((q) => q.id === id)))
      }
    } catch (e) { fail('读取待放行队列失败', e) } finally { setBusy('') }
  }, [])

  /** 骨架草稿不走技能队列（那只是技能草稿），必须单独列 —— 否则生成完看起来像"没生成"。 */
  const loadDrafts = useCallback(async () => {
    setBusy('drafts'); setDraftErr('')
    try {
      const r = await hubGet<{ data?: { items?: DraftRow[] }; items?: DraftRow[] }>(INGESTION_DRAFTS)
      const items = r?.data?.items ?? r?.items ?? []
      setDrafts(items.filter((d) => d && d.draft_id))
    } catch (e) {
      setDraftErr('读取骨架草稿失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadQueue() }, [loadQueue])
  useEffect(() => { void loadDrafts() }, [loadDrafts])

  /**
   * 工作流草稿能不能点「登记」。
   *
   * 【2026-11-08 修】以前是「每步都绑到**工具**才允许登记」，于是 code-review 这类
   * 「一半步骤靠人/模型判断」的工作流在界面上被**永久锁死**（19 步只能绑 10 步，按钮恒灰），
   * 而服务端 register_draft 早就支持把没绑工具的步骤**就地补成 LLM 步**
   * （need_llm=true + prompt_template=原描述原文，#1483）。⇒ 这里只拦「0 步」这种真没法登记的，
   * 其余放行，并在 title 里说清"没绑工具的那些步骤会按 LLM 步骤登记"。
   */
  const regBlocked = (d: DraftRow): string => {
    if (d.form !== 'workflow') return ''
    if ((d.total_steps ?? 0) === 0) return '工作流 0 步：先补步骤（或改用技能形态）'
    return ''
  }

  /** 登记按钮的补充说明：还有多少步没绑工具、它们登记时会被当成什么。 */
  const regHint = (d: DraftRow): string => {
    if (d.form !== 'workflow') return ''
    const total = d.total_steps ?? 0
    const bound = d.bound_steps ?? 0
    if (total === 0 || bound >= total) return ''
    return '（还有 ' + (total - bound) + ' 步没有对应工具：登记时会按「LLM 步骤」处理，'
      + '执行时由模型完成这几步）'
  }

  const registerDraft = async (d: DraftRow, enable: boolean) => {
    const label = enable ? '登记并启用' : '登记'
    if (enable && !window.confirm('登记并**启用**「' + (d.skill_name || d.skill_id || d.draft_id) + '」？启用后模型即可调用。')) return
    setBusy((enable ? 'reg-on:' : 'reg:') + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await hubPost<{ data?: { result?: Record<string, unknown> }; message?: string }>(
        ingestionDraftRegister(d.draft_id), { enable, confirm: enable })
      const result = (r?.data?.result ?? {}) as Record<string, unknown>
      const target = String(result.skill_id || result.tool_name || result.workflow_id || '')
      setMsg(label + '成功：' + (target || d.draft_id) + (enable ? '（已启用）' : '（未启用）'))
      await loadDrafts()
    } catch (e) {
      setDraftErr(label + '失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const removeDraft = async (id: string) => {
    if (!window.confirm('确定删除骨架草稿「' + id + '」？整个目录会删除，不可恢复。')) return
    setBusy('draft-del:' + id); setDraftErr(''); setMsg('')
    try {
      await hubDelete(ingestionDraft(id))
      setMsg('已删除骨架草稿 ' + id)
      await loadDrafts()
    } catch (e) {
      setDraftErr('删除骨架草稿失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  /** 后端 RFC9457 的 detail 比 hubPost 的 "HTTP 400" 有用得多（会列出未绑定步骤）。 */
  const postJson = async <T,>(url: string, body: unknown): Promise<T> => {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeader() },
      body: JSON.stringify(body),
    })
    const json = await res.json().catch(() => null)
    if (!res.ok) {
      const detail = json?.detail || json?.error || json?.title || ('HTTP ' + res.status)
      throw new Error(String(detail))
    }
    return (json?.data ?? json) as T
  }

  /** AI 自动绑定（先 dry-run 给人看；确认后才写回 + 登记）。 */
  const bindDraft = async (d: DraftRow) => {
    setBusy('bind:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ report: BindReport }>(ingestionDraftAutobind(d.draft_id), { apply: false })
      setBind({ id: d.draft_id, report: r.report })
    } catch (e) {
      setDraftErr('AI 绑定失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const applyBind = async (d: DraftRow) => {
    if (!bind || bind.id !== d.draft_id) return
    const { bound, total } = bind.report
    const full = total > 0 && bound >= total
    if (!window.confirm(full
      ? `将把 ${bound}/${total} 步写回草稿（写回后再点「登记为工作流」）。继续？`
      : `只绑定 ${bound}/${total} 步（仍有未绑定）；先写回草稿，不会登记。继续？`)) return
    setBusy('apply:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{ report: BindReport }>(ingestionDraftAutobind(d.draft_id), { apply: true, confirm: true })
      const rep = r.report
      setMsg('已写回绑定 ' + rep.bound + '/' + rep.total +
        (rep.llm_steps ? '（' + rep.llm_steps + ' 步为 LLM 步骤）' : '') +
        (rep.unbound && rep.unbound.length
          ? '；未绑定：' + rep.unbound.map((u) => u.step_id).join('、')
          : '；再点「登记为工作流」'))
      setBind(null); await loadDrafts()
    } catch (e) {
      setDraftErr('应用绑定失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  /** tool 草稿：AI 生成实现（先 dry-run 看校验，再写回）。 */
  const implementDraft = async (d: DraftRow) => {
    setBusy('impl:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      const r = await postJson<{
        validation: { ok?: boolean; errors?: string[]; warnings?: string[] }
        code: string
      }>(ingestionDraftImplement(d.draft_id), { apply: false })
      setImpl({ id: d.draft_id, validation: r.validation, code: r.code })
    } catch (e) {
      setDraftErr('AI 实现失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const applyImpl = async (d: DraftRow) => {
    if (!impl || impl.id !== d.draft_id) return
    if (!impl.validation.ok) {
      setDraftErr('校验未通过，不能写回：' + (impl.validation.errors || []).join('；'))
      return
    }
    if (!window.confirm('把 AI 实现写回草稿（工具文件 / schema / 测试），不登记、不启用。继续？')) return
    setBusy('impl-apply:' + d.draft_id); setDraftErr(''); setMsg('')
    try {
      await postJson(ingestionDraftImplement(d.draft_id), { apply: true, confirm: true })
      setMsg('已写回实现草稿；点「登记为工具」才会进仓库')
      setImpl(null); await loadDrafts()
    } catch (e) {
      setDraftErr('写回实现失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }


  const review = async (id: string) => {
    setBusy('review:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillReview(id), {})
      if (r?.ok === false) { bad('审核失败', r); return }
      setMsg('已审核 ' + id); await loadQueue()
    } catch (e) { fail('审核失败', e) } finally { setBusy('') }
  }

  const publish = async (id: string) => {
    setBusy('publish:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillPublish(id))
      if (r?.ok === false) { bad('放行失败', r); return }
      setMsg('已放行 ' + id); await loadQueue()
    } catch (e) { fail('放行失败', e) } finally { setBusy('') }
  }

  const reject = async (id: string) => {
    if (!window.confirm('确定驳回并删除草稿「' + id + '」？删除后不可恢复。')) return
    setBusy('reject:' + id); setErr(''); setMsg('')
    try {
      const res = await fetch(skillById(id), { method: 'DELETE', headers: authHeader() })
      const body = await res.json().catch(() => null)
      if (!res.ok) { setErr('驳回失败：' + (body?.error ?? ('HTTP ' + res.status))); return }
      setMsg('已驳回 ' + id); setSel((prev) => prev.filter((x) => x !== id)); await loadQueue()
    } catch (e) { fail('驳回失败', e) } finally { setBusy('') }
  }

  const rows = queue?.items ?? []
  const selected = rows.filter((q) => q.id && sel.includes(q.id))
  const blocked = selected.filter((q) => q.review?.blocked === true)
  const hasBlocked = blocked.length > 0
  const canBatch = selected.length > 0 && (!hasBlocked || (batchForce && batchReason.trim().length >= 4))
  const loading = busy !== ''

  const submitBatch = async () => {
    setBusy('batch'); setErr(''); setMsg(''); setBatchResult(null)
    try {
      const body: Record<string, unknown> = { ids: selected.map((r) => r.id as string) }
      if (hasBlocked) { body.force = true; body.reason = batchReason.trim() }
      const r = await hubPost<BatchResult>(SKILLS_MGMT_QUEUE_PUBLISH_BATCH, body)
      setBatchResult(r)
      const okN = (r?.published ?? []).length
      const badN = (r?.failed ?? []).length
      setMsg(badN === 0 ? '已放行 ' + okN + ' 条' : '已放行 ' + okN + ' 条，失败 ' + badN + ' 条')
      setSel([]); setBatchOpen(false); setBatchForce(false); setBatchReason(''); await loadQueue()
    } catch (e) { fail('批量放行失败', e) } finally { setBusy('') }
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium text-slate-200">外来草稿 · 待评审</h3>
        <span className="text-[10px] text-slate-500">
          外来来源先落 draft；人工审核通过后才能发布
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <button type="button" className={BTN} disabled={loading || sel.length === 0}
            aria-label="批量放行所选技能"
            onClick={() => { setBatchResult(null); setBatchForce(false); setBatchReason(''); setBatchOpen(true) }}>
            批量放行（{sel.length}）
          </button>
          <button type="button" className={BTN} onClick={() => void loadQueue()} disabled={loading} aria-label="刷新待放行队列">
            {busy === 'queue' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <div className="mb-2"><ErrorBox message={err} /></div>}
      {msg && <div className="mb-2 flex items-center gap-1.5 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300"><CheckCircle2 size={12} /> {msg}</div>}
      {!queue && busy !== 'queue' && <div className="text-[11px] text-slate-500">未取到队列。</div>}
      {queue && rows.length === 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-slate-500"><Inbox size={11} /> 队列为空 —— 没有待评审/放行的外来技能。</div>
      )}

      {batchOpen && (
        <div className="mb-2 rounded-sm border border-amber-800/60 bg-amber-950/20 p-2 text-[11px] text-slate-300">
          <div>将放行 {selected.length} 条：<span className="font-mono text-[10px] text-cyan-300">{selected.map((r) => r.id).join('、')}</span></div>
          {hasBlocked && (
            <>
              <div className="mt-1 text-red-300">
                其中 {blocked.length} 条被评审阻断（{blocked.map((r) => r.id).join('、')}）：force 会绕过强制审核链并写审计。
              </div>
              <label className="mt-1 flex items-center gap-1 text-[10px] text-slate-300">
                <input type="checkbox" checked={batchForce} onChange={(e) => setBatchForce(e.target.checked)}
                  aria-label="确认对阻断项使用 force 豁免" />
                我确认使用 force 豁免（写审计，可追溯）
              </label>
              <label className="mt-1 flex flex-col gap-1 text-[10px] text-slate-400">
                force 豁免原因（至少 4 字）
                <textarea className={INPUT} rows={2} value={batchReason}
                  onChange={(e) => setBatchReason(e.target.value)} aria-label="force 豁免原因" />
              </label>
            </>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <button type="button" className={BTN_EM} onClick={() => void submitBatch()}
              disabled={loading || !canBatch} aria-label="确认批量放行">
              {busy === 'batch' ? <Loader2 size={11} className="animate-spin" /> : null} 确认放行
            </button>
            <button type="button" className={BTN} onClick={() => setBatchOpen(false)} disabled={loading}>取消</button>
            {hasBlocked && !canBatch && <span className="text-[10px] text-amber-300">先勾选豁免并填写原因</span>}
          </div>
        </div>
      )}

      {batchResult && (batchResult.failed ?? []).length > 0 && (
        <div className="mb-2 flex flex-col gap-0.5 text-[10px] text-red-300">
          {(batchResult.failed ?? []).map((f) => <span key={'f-' + (f.id ?? '')}>{(f.id ?? '?') + '：' + (f.error ?? '未知错误')}</span>)}
        </div>
      )}
      {batchResult && (batchResult.failed ?? []).length === 0 && (
        <div className="mb-2 text-[10px] text-emerald-300">已放行：{(batchResult.published ?? []).join('、') || '(无)'}</div>
      )}

      {rows.length > 0 && (
        <div className="flex flex-col gap-1">
          {rows.map((q) => {
            const id = q.id ?? ''
            const isBlocked = q.review?.blocked === true
            const verdict = q.review?.review_verdict || ''
            return (
              <div key={'q-' + id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <input type="checkbox" checked={sel.includes(id)} onChange={() => setSel((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id])}
                  disabled={loading} aria-label={'选择外来技能 ' + id} />
                <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                <span className="text-slate-300">{q.name ?? ''}</span>
                <span className="truncate text-[10px] text-slate-500">{q.source ?? ''}</span>
                <span className={'text-[10px] ' + (isBlocked ? 'text-red-300' : verdict ? 'text-emerald-300' : 'text-slate-400')}>
                  {isBlocked ? '评审阻断' : verdict ? '评审：' + verdict : '未审核'}
                </span>
                <span className="ml-auto flex items-center gap-1.5">
                  <button type="button" className={BTN_EM} onClick={() => void review(id)} disabled={loading} aria-label={'审核外来技能 ' + id}>
                    {busy === 'review:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 审核
                  </button>
                  <button type="button" className={BTN} onClick={() => void publish(id)} disabled={loading || isBlocked}
                    aria-label={'放行外来技能 ' + id}
                    title={isBlocked ? '阻断项单条不放行；豁免走批量放行并填原因' : '走强制审核链发布'}>
                    {busy === 'publish:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 放行
                  </button>
                  <button type="button" className={BTN_RED} onClick={() => void reject(id)} disabled={loading} aria-label={'驳回外来技能 ' + id}>
                    {busy === 'reject:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 驳回
                  </button>
                </span>
              </div>
            )
          })}
          {rows.some((q) => q.review?.blocked) && (
            <div className="text-[10px] text-red-300/80">阻断项单条不放行：先处理评审问题；豁免只能「勾选 → 勾豁免 → 填原因 → 二次确认」。</div>
          )}
        </div>
      )}

      {/* 吞噬骨架草稿：tool/workflow 形态的产物不进上面的技能队列，单独列出来 */}
      <div className="mt-4 border-t border-slate-800 pt-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-slate-200">吞噬生成的草稿（隔离区 · 只读预览）</h3>
          <span className="text-[10px] text-slate-500">
            技能 / 工具 / 工作流都写到隔离目录；<b>登记与启用就在这一行</b>（登记默认不启用）
          </span>
          <button type="button" className={BTN + ' ml-auto'} onClick={() => void loadDrafts()} disabled={loading}
            aria-label="刷新骨架草稿">
            {busy === 'drafts' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
        {draftErr && <div className="mb-2"><ErrorBox message={draftErr} /></div>}
        {drafts.length === 0 ? (
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <Inbox size={11} /> 还没有草稿。在「能力获取」里生成草稿后会出现在这里；登记直接在这一行点（三种形态共用同一个登记口）。
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {drafts.map((d) => {
              const bound = d.bound_steps ?? 0
              const total = d.total_steps ?? 0
              const isWorkflow = d.form === 'workflow'
              const ready = total > 0 && bound >= total
              return (
                <div key={'d-' + d.draft_id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                  <span className="font-mono text-[10px] text-cyan-300">{d.draft_id}</span>
                  <Badge color="cyan">{FORM_LABEL[d.form ?? ''] ?? d.form ?? '草稿'}</Badge>
                  <span className="text-slate-300">{d.skill_name || d.skill_id || '—'}</span>
                  {d.created_at ? (
                    <span className="text-[10px] text-slate-500" title={d.created_at}>生成 {fmtTime(d.created_at)}</span>
                  ) : null}
                  {isWorkflow && (
                    <span className={'text-[10px] ' + (ready ? 'text-emerald-300' : 'text-amber-300')}
                      title={ready ? '每一步都有工具或用 LLM 步骤兜住，可以直接登记' : '没绑工具的步骤登记时会按「LLM 步骤」处理'}>
                      绑定 {bound}/{total}
                    </span>
                  )}
                  {(d.warnings ?? []).length > 0 && (
                    <span className="text-[10px] text-amber-300" title={(d.warnings ?? []).join('\n')}>
                      警告 {(d.warnings ?? []).length} 条
                    </span>
                  )}
                  <span className="ml-auto flex flex-wrap items-center gap-1.5">
                    <span className="max-w-[300px] truncate font-mono text-[10px] text-slate-500" title={d.draft_dir}>{d.draft_dir}</span>
                    {isWorkflow && (
                      <button type="button" className={BTN_EM} disabled={loading} onClick={() => void bindDraft(d)}
                        title="用 LLM 把步骤映射到真实工具与参数；判断/评审/写作类步骤会标成「LLM 步骤」（need_llm），执行时由模型完成。先预览，不写盘"
                        aria-label={'AI 绑定 ' + d.draft_id}>
                        {busy === 'bind:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} AI 绑定
                      </button>
                    )}
                    {d.form === 'tool' && (
                      <button type="button" className={BTN_EM} disabled={loading}
                        onClick={() => void implementDraft(d)}
                        title="用 LLM 读取来源 SKILL.md/脚本并生成实现（先校验，不写盘）"
                        aria-label={'AI 实现 ' + d.draft_id}>
                        {busy === 'impl:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} AI 实现
                      </button>
                    )}
                    {/* 2026-11-07：这里原是一句**死提示**「登记去「转化队列」Tab」—— 转化队列只认
                        /api/skills-mgmt/queue 里的技能草稿，tool/workflow 骨架根本不在那儿，
                        按提示走过去只会看到空队列。改为真实登记（三种形态同一个端点）。 */}
                    <button type="button" className={BTN_EM} disabled={loading || regBlocked(d) !== ''}
                      onClick={() => void registerDraft(d, false)}
                      title={regBlocked(d) || (d.form === 'tool'
                        ? '把实现写进 agent/tools/custom + 治理 YAML + 单测，过验证门后进台账（不启用）'
                        : '登记进仓库/台账，不启用（启用要再过一道确认）') + regHint(d)}
                      aria-label={'登记草稿 ' + d.draft_id}>
                      {busy === 'reg:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null}
                      {d.form === 'tool' ? '登记为工具' : d.form === 'workflow' ? '登记为工作流' : '登记为技能'}
                    </button>
                    <button type="button" className={BTN} disabled={loading || regBlocked(d) !== ''}
                      onClick={() => void registerDraft(d, true)}
                      title={'登记并直接启用（会再过一次确认；工具仍受审批边界约束）' + regHint(d)}
                      aria-label={'登记并启用草稿 ' + d.draft_id}>
                      {busy === 'reg-on:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null} 登记并启用
                    </button>
                    <button type="button" className={BTN_RED} disabled={loading}
                      onClick={() => void removeDraft(d.draft_id)}
                      aria-label={'删除骨架草稿 ' + d.draft_id}>
                      {busy === 'draft-del:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} 删除
                    </button>
                  </span>
                  {bind?.id === d.draft_id && (
                    <div className="mt-1 flex w-full flex-col gap-1 rounded-sm border border-cyan-900/60 bg-cyan-950/20 p-2 text-[10px] text-slate-300">
                      <div>
                        AI 建议：落地 <b className="text-cyan-300">{bind.report.bound}/{bind.report.total}</b> 步
                        {bind.report.llm_steps
                          ? '（其中 ' + bind.report.llm_steps + ' 步没有对应工具，标成「LLM 步骤」，执行时会调模型）'
                          : ''}
                        {bind.report.model ? '（模型 ' + bind.report.model + '）' : ''}
                      </div>
                      {(bind.report.unbound ?? []).length > 0 && (
                        <div className="text-amber-300">
                          未绑定：{(bind.report.unbound ?? []).slice(0, 6)
                            .map((u) => u.step_id + (u.reason ? '（' + u.reason + '）' : '')).join('、')}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-1.5">
                        <button type="button" className={BTN_EM} disabled={loading}
                          onClick={() => void applyBind(d)} aria-label={'应用绑定 ' + d.draft_id}>
                          {busy === 'apply:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null}
                          {bind.report.total > 0 && bind.report.bound >= bind.report.total ? '应用并登记' : '仅写回'}
                        </button>
                        <button type="button" className={BTN} disabled={loading} onClick={() => setBind(null)}>取消</button>
                      </div>
                    </div>
                  )}
                  {impl?.id === d.draft_id && (
                    <div className="mt-1 flex w-full flex-col gap-1 rounded-sm border border-cyan-900/60 bg-cyan-950/20 p-2 text-[10px] text-slate-300">
                      <div>
                        AI 实现：
                        <b className={impl.validation.ok ? 'text-emerald-300' : 'text-red-300'}>
                          {impl.validation.ok ? '静态校验通过' : '校验未通过'}
                        </b>
                        {impl.validation.ok ? '（写回后仍需点「登记为工具」）' : ''}
                      </div>
                      {(impl.validation.errors ?? []).length > 0 && (
                        <div className="text-red-300">
                          {(impl.validation.errors ?? []).slice(0, 5).join('；')}
                        </div>
                      )}
                      {(impl.validation.warnings ?? []).length > 0 && (
                        <div className="text-amber-300">{(impl.validation.warnings ?? []).slice(0, 3).join('；')}</div>
                      )}
                      <div className="flex flex-wrap items-center gap-1.5">
                        <button type="button" className={BTN_EM} disabled={loading || !impl.validation.ok}
                          onClick={() => void applyImpl(d)} aria-label={'应用实现 ' + d.draft_id}>
                          {busy === 'impl-apply:' + d.draft_id ? <Loader2 size={11} className="animate-spin" /> : null}
                          应用实现
                        </button>
                        <button type="button" className={BTN} disabled={loading} onClick={() => setImpl(null)}>取消</button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}