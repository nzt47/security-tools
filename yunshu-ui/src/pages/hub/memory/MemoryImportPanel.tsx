/**
 * 导入外部记忆 —— Claude / Trae → 云枢（记忆管理页入口）
 * -----------------------------------------------------------------
 * 界面口径与后端契约一一对应（见 agent/server_routes/routes_memory_import.py）：
 *   1. 先选来源 → 「生成预览」：只读解析 + 去噪/去重/脱敏/分批，**不写任何记忆**；
 *   2. 预览里逐批勾选（默认不勾，避免"一键全导入"的误操作）→ 勾「已核对」→ 「写入云枢」；
 *   3. 「只回放」= dry-run：让用户看清"将写什么"，不产生任何写入。
 *
 * 【不易】真写与否由后端判据决定（confirm 且 dry_run=false）；本页的勾选框只是
 * 二次确认的界面表达，不能、也不应该成为唯一防线。
 */
import { useEffect, useState } from 'react'
import { Brain, DownloadCloud, Loader2, RefreshCw, ShieldCheck, Undo2 } from 'lucide-react'
import { Card } from '../components/ui'
import {
  MemoryImportApiError, commitMemoryImport, fetchImportedVectorDocs,
  fetchMemoryImportBatches, fetchMemoryImportSources, previewMemoryImport,
  rollbackMemoryImport,
  type ImportedVectorDoc, type MemoryImportBatchEntry,
  type MemoryImportCommitResult, type MemoryImportPlan, type MemoryImportSource,
  type MemoryImportRollbackResult,
} from '@/lib/memoryImportApi'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-2 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_PRIMARY = 'inline-flex items-center gap-1 rounded-md bg-cyan-600 px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40'

function errText(e: unknown): string {
  if (e instanceof MemoryImportApiError) return e.message
  return e instanceof Error ? e.message : String(e)
}

/** 降级原因码 -> 人话（后端只回白名单码，不回异常原文） */
function digestReasonText(reason?: string): string {
  switch (reason) {
    case 'llm_unconfigured':
      return '未配置 LLM（.env 里没有 LLM_API_KEY / DEEPSEEK_API_KEY），本次用规则式摘要'
    case 'llm_budget_exhausted':
      return 'LLM 调用次数已达上限（CP_MEMORY_IMPORT_LLM_MAX_BATCHES），其余批次用规则式'
    case 'llm_input_too_large':
      return '部分批次内容超过单次输入上限（CP_MEMORY_IMPORT_LLM_MAX_CHARS），已用规则式'
    case 'llm_no_message_batch':
      return '本次没有会话批次（只有规则/提问轨迹），LLM 摘要未参与'
    case 'llm_error':
      return 'LLM 调用失败，已回退规则式（详见服务日志）'
    case 'llm_empty_output':
      return 'LLM 返回空摘要，已回退规则式'
    case 'llm_fallback':
      return '部分批次回退为规则式摘要（原因见各批次的摘要元数据）'
    default:
      return ''
  }
}

export default function MemoryImportPanel() {
  const [sources, setSources] = useState<MemoryImportSource[]>([])
  const [note, setNote] = useState('')
  const [pickedSources, setPickedSources] = useState<string[]>(['claude', 'trae'])
  const [includeTranscripts, setIncludeTranscripts] = useState(true)
  // LLM 摘要默认关闭：它要花钱、且改写过的正文不能当原话核对
  const [useLlm, setUseLlm] = useState(false)
  const [llmAvailable, setLlmAvailable] = useState(false)
  const [llmReason, setLlmReason] = useState('')
  const [plan, setPlan] = useState<MemoryImportPlan | null>(null)
  const [pickedBatches, setPickedBatches] = useState<string[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState<'' | 'preview' | 'commit' | 'dry'>('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [result, setResult] = useState<MemoryImportCommitResult | null>(null)
  const [batches, setBatches] = useState<MemoryImportBatchEntry[]>([])
  const [batchBusy, setBatchBusy] = useState('')
  const [batchMsg, setBatchMsg] = useState('')
  const [batchErr, setBatchErr] = useState('')
  const [pendingRollback, setPendingRollback] = useState('')
  // §10 第 3 步：向量文档列表（来源徽章 = 一眼看出这条是外部记忆、哪个来源）
  const [vectors, setVectors] = useState<ImportedVectorDoc[]>([])
  const [vecErr, setVecErr] = useState('')
  const [vecBusy, setVecBusy] = useState(false)
  // 回滚前预览：点「回滚」先拉 dry-run，把将删掉的向量正文摆出来再让人确认
  const [rollbackPreview, setRollbackPreview] = useState<MemoryImportRollbackResult | null>(null)
  const [previewBusy, setPreviewBusy] = useState('')

  const loadBatches = () => {
    fetchMemoryImportBatches()
      .then((d) => setBatches(d?.batches ?? []))
      .catch((e) => setBatchErr('读取已写入批次失败：' + errText(e)))
  }

  useEffect(() => {
    let alive = true
    fetchMemoryImportSources()
      .then((d) => {
        if (!alive) return
        setSources(d?.sources ?? [])
        setNote(d?.note ?? '')
        setLlmAvailable(Boolean(d?.digest_llm?.available))
        setLlmReason(d?.digest_llm?.reason ?? '')
        const available = (d?.sources ?? []).filter((s) => s.available).map((s) => s.id)
        if (available.length) setPickedSources(available)
      })
      .catch((e) => { if (alive) setErr('探测来源失败：' + errText(e)) })
    return () => { alive = false }
  }, [])

  const loadVectors = () => {
    setVecBusy(true); setVecErr('')
    fetchImportedVectorDocs()
      .then((rows) => setVectors(rows))
      .catch((e) => setVecErr('读取向量文档失败：' + errText(e)))
      .finally(() => setVecBusy(false))
  }

  useEffect(() => { loadBatches(); loadVectors() }, [])

  const beginRollback = async (batchId: string) => {
    // 第一下只做"看清要删什么"：dry-run 会逐条回读向量正文（服务端只读，不删任何东西）
    setPendingRollback(batchId)
    setRollbackPreview(null)
    setBatchErr(''); setBatchMsg('')
    setPreviewBusy(batchId)
    try {
      const r = await rollbackMemoryImport({ batch_id: batchId, confirm: false, dry_run: true })
      setRollbackPreview(r)
    } catch (e) {
      setBatchErr('读取回滚预览失败：' + errText(e))
    } finally { setPreviewBusy('') }
  }

  const cancelRollback = () => { setPendingRollback(''); setRollbackPreview(null) }

  const doRollback = async (batchId: string) => {
    setBatchBusy(batchId); setBatchErr(''); setBatchMsg(''); setPendingRollback(''); setRollbackPreview(null)
    try {
      const r = await rollbackMemoryImport({ batch_id: batchId, confirm: true, dry_run: false })
      if (r.manual) {
        setBatchErr('该批次需人工回滚：' + (r.error || '没有可删的凭据（vector_ids / msg_id 都没有）'))
      } else if (r.ok) {
        // 404（本来就不在）单独说清楚：不把它算成"刚删掉"，也不当成失败
        const missing = (r.missing ?? []).length
        setBatchMsg('已回滚 ' + batchId + (r.deleted ? '（已删除 ' + r.deleted + '）' : '')
          + (missing ? '（' + missing + ' 条在删除前已不存在）' : '')
          + (typeof r.audit?.seq === 'number' ? ' · 审计 seq ' + r.audit.seq : ''))
        loadBatches()
        loadVectors()  // 回滚删掉的是向量 ⇒ 列表必须跟着消失，而不是留一条幽灵记录
      } else {
        setBatchErr('回滚未生效：' + (r.error || '未知原因'))
      }
    } catch (e) {
      setBatchErr('回滚失败：' + errText(e))
    } finally { setBatchBusy('') }
  }

  const toggleSource = (id: string) => {
    setPickedSources((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const doPreview = async () => {
    if (!pickedSources.length) { setErr('请至少选择一个来源'); return }
    setBusy('preview'); setErr(''); setMsg(''); setResult(null)
    try {
      const d = await previewMemoryImport({
        sources: pickedSources, include_transcripts: includeTranscripts, use_llm: useLlm,
      })
      setPlan(d)
      setPickedBatches([])
      setConfirmed(false)
      const llmN = d?.digest?.llm_batches ?? 0
      const ruleN = d?.digest?.rule_batches ?? (d?.batches?.length ?? 0)
      const mix = d?.digest?.requested ? '；LLM 摘要 ' + llmN + ' 批 / 规则 ' + ruleN + ' 批' : ''
      setMsg('预览已生成（' + (d?.batches?.length ?? 0) + ' 个批次' + mix + '，未写入任何记忆）')
    } catch (e) {
      setErr('预览失败：' + errText(e))
    } finally { setBusy('') }
  }

  const doCommit = async (dryRun: boolean) => {
    if (!plan) { setErr('请先生成预览'); return }
    if (!pickedBatches.length) { setErr('请至少勾选一个批次'); return }
    if (!dryRun && !confirmed) { setErr('请先勾选「已核对预览内容」'); return }
    setBusy(dryRun ? 'dry' : 'commit'); setErr(''); setMsg('')
    try {
      const r = await commitMemoryImport({ plan_id: plan.plan_id, batch_ids: pickedBatches, confirm: !dryRun, dry_run: dryRun })
      setResult(r)
      if (r?.dry_run) {
        setMsg('回放完成：将写入 ' + (r?.would?.length ?? 0) + ' 个批次（未写入）')
      } else {
        const okCount = r?.committed?.length ?? 0
        const failCount = r?.failed?.length ?? 0
        setMsg('写入完成：成功 ' + okCount + ' 个批次' + (failCount ? '，失败 ' + failCount + ' 个' : ''))
        if (okCount) {
          const seqs = (r?.committed ?? []).map((c) => c.audit?.seq).filter((s) => typeof s === 'number')
          if (seqs.length) setMsg((prev) => prev + '（审计 seq ' + seqs[0] + '-' + seqs[seqs.length - 1] + '）')
          loadBatches()  // 写入后刷新"已写入批次"，回滚入口立刻可见
        }
      }
    } catch (e) {
      setErr((dryRun ? '回放失败：' : '写入失败：') + errText(e))
    } finally { setBusy('') }
  }

  const allBatchIds = (plan?.batches ?? []).map((b) => b.batch_id)
  const allPicked = allBatchIds.length > 0 && pickedBatches.length === allBatchIds.length

  const stats = (plan?.stats ?? {}) as Record<string, number>
  const hits = (plan?.redaction?.hits ?? {}) as Record<string, number>
  const hitText = Object.keys(hits).length
    ? Object.entries(hits).map(([k, v]) => k + ' ' + v).join(' / ')
    : '无'

  return (
    <Card title="导入外部记忆">
      <p className="mb-3 text-xs leading-6 text-slate-500">
        把 Claude / Trae 的规则与会话轨迹导入云枢：规则进知识卡片，会话/提问进<span className="text-slate-300">向量记忆</span>（可语义检索、可按批回滚）。
        <span className="text-slate-400">预览是只读的</span>，勾选批次并确认后才会写入；每次写入都会写审计，可按批次回滚。
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-300">
        {sources.length === 0 && <span className="text-slate-600">探测来源中…</span>}
        {sources.map((s) => (
          <label key={s.id} className={'inline-flex items-center gap-1 ' + (s.available ? '' : 'opacity-40')}>
            <input
              type="checkbox"
              checked={pickedSources.includes(s.id)}
              disabled={!s.available || busy !== ''}
              onChange={() => toggleSource(s.id)}
              aria-label={'来源 ' + s.label}
            />
            {s.label}
            {!s.available && <span className="text-[10px] text-slate-500">（未找到）</span>}
          </label>
        ))}
        <label className="inline-flex items-center gap-1">
          <input
            type="checkbox"
            checked={includeTranscripts}
            disabled={busy !== '' || !pickedSources.includes('claude')}
            onChange={(e) => setIncludeTranscripts(e.target.checked)}
            aria-label="解析会话转录"
          />
          解析会话转录（51 MB，约 10 秒）
        </label>
        <label
          className={'inline-flex items-center gap-1 ' + (llmAvailable ? '' : 'opacity-40')}
          title={llmAvailable ? '只作用于会话批次；规则/提问轨迹不会被改写' : '未配置 LLM_API_KEY / DEEPSEEK_API_KEY'}
        >
          <input
            type="checkbox"
            checked={useLlm}
            disabled={busy !== '' || !llmAvailable}
            onChange={(e) => setUseLlm(e.target.checked)}
            aria-label="用 LLM 摘要"
          />
          用 LLM 摘要（会话批次）
          {!llmAvailable && <span className="text-[10px] text-slate-500">（未配置）</span>}
        </label>
        <button type="button" className={BTN} onClick={() => void doPreview()} disabled={busy !== ''} aria-label="生成预览">
          {busy === 'preview' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 生成预览
        </button>
      </div>
      {useLlm && plan?.digest?.reason && (
        <div className="mb-2 text-[11px] text-amber-400/90">{digestReasonText(plan.digest.reason)}</div>
      )}
      {!llmAvailable && llmReason === 'llm_unconfigured' && (
        <div className="mb-2 text-[10px] text-slate-500">LLM 摘要不可用：.env 未配置 LLM_API_KEY / DEEPSEEK_API_KEY（不影响规则式导入）</div>
      )}
      {note && <div className="mb-2 text-[11px] text-slate-500">{note}</div>}

      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      {plan && (
        <div className="mt-2">
          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span>plan <span className="font-mono text-slate-300">{plan.plan_id}</span></span>
            <span>扫描 <b className="text-slate-200">{stats.scanned ?? '-'}</b></span>
            <span>去噪 <b className="text-slate-200">{stats.denoised ?? 0}</b></span>
            <span>去重 <b className="text-slate-200">{stats.duplicates ?? 0}</b></span>
            <span>保留 <b className="text-cyan-300">{stats.kept ?? '-'}</b></span>
            <span>脱敏命中 <b className="text-amber-300">{hitText}</b></span>
            <span>闸门拒绝 <b className="text-slate-200">{plan.redaction?.blocked ?? 0}</b></span>
          </div>

          <div className="mb-2 flex items-center gap-2 text-[11px]">
            <button type="button" className={BTN} onClick={() => setPickedBatches(allPicked ? [] : allBatchIds)} aria-label="全选批次">
              {allPicked ? '清空选择' : '全选批次'}
            </button>
            <span className="text-slate-500">已选 {pickedBatches.length} / {allBatchIds.length}</span>
          </div>

          <div className="max-h-72 overflow-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-900 text-slate-400">
                <tr>
                  <th className="w-8 px-2 py-1" />
                  <th className="px-2 py-1">批次</th>
                  <th className="px-2 py-1">载体</th>
                  <th className="px-2 py-1">条数</th>
                  <th className="px-2 py-1">字符</th>
                  <th className="px-2 py-1">摘要</th>
                </tr>
              </thead>
              <tbody>
                {plan.batches.map((b) => {
                  const checked = pickedBatches.includes(b.batch_id)
                  return (
                    <tr key={b.batch_id} className="border-t border-slate-800/70 align-top">
                      <td className="px-2 py-1">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => setPickedBatches((prev) => (checked ? prev.filter((x) => x !== b.batch_id) : [...prev, b.batch_id]))}
                          aria-label={'选择批次 ' + b.batch_id}
                        />
                      </td>
                      <td className="px-2 py-1 font-mono text-slate-400">{b.batch_id}</td>
                      <td className="px-2 py-1 text-slate-300">{b.carrier_label}</td>
                      <td className="px-2 py-1 text-slate-300">{b.records}</td>
                      <td className="px-2 py-1 text-slate-400">{b.chars}</td>
                      <td className="px-2 py-1 text-slate-400">
                        <div>{b.title}</div>
                        <span
                          className={'mt-1 inline-block rounded px-1 text-[9px] ' + (b.digest_source === 'llm'
                            ? 'bg-violet-500/15 text-violet-300'
                            : 'bg-slate-700/40 text-slate-400')}
                          title={b.digest_source === 'llm'
                            ? 'LLM 改写：可能漏项，原文仍在批次记录里可核对'
                            : '规则式：原句按天汇总，未改写'}
                        >
                          {b.digest_source === 'llm' ? 'LLM' : '规则'}
                        </span>
                        <details className="mt-1">
                          <summary className="cursor-pointer text-[10px] text-cyan-500">看摘要</summary>
                          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[10px] leading-5 text-slate-400">{b.digest.slice(0, 600)}</pre>
                        </details>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="inline-flex items-center gap-1 text-[11px] text-slate-300">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                aria-label="我确认已核对预览内容"
              />
              我确认已核对预览内容
            </label>
            <button type="button" className={BTN} onClick={() => void doCommit(true)} disabled={busy !== '' || !pickedBatches.length} aria-label="只回放">
              {busy === 'dry' ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />} 只回放（dry-run）
            </button>
            <button
              type="button"
              className={BTN_PRIMARY}
              onClick={() => void doCommit(false)}
              disabled={busy !== '' || !pickedBatches.length || !confirmed}
              aria-label="写入云枢"
            >
              {busy === 'commit' ? <Loader2 size={11} className="animate-spin" /> : <DownloadCloud size={11} />} 写入云枢
            </button>
          </div>

          {result?.dry_run && result.would && (
            <div className="mt-2 text-[11px] text-slate-400">
              将写入：{result.would.map((w) => w.batch_id + '（' + w.records + ' 条）').join('、')}
            </div>
          )}
          {result && !result.dry_run && (result.committed?.length ?? 0) > 0 && (
            <ul className="mt-2 space-y-1 text-[11px] text-slate-400">
              {result.committed!.map((c) => (
                <li key={c.batch_id}>
                  <span className="font-mono">{c.batch_id}</span> → {c.carrier_label ?? c.carrier}
                  {c.ref ? ' · ' + c.ref : ''}
                  {typeof c.audit?.seq === 'number' ? ' · 审计 seq ' + c.audit.seq : ''}
                </li>
              ))}
            </ul>
          )}
          {result?.failed && result.failed.length > 0 && (
            <ul className="mt-2 space-y-1 text-[11px] text-red-300">
              {result.failed.map((f) => <li key={f.batch_id}>{f.batch_id}：{f.error}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="mt-5 border-t border-slate-800 pt-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Undo2 size={13} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">已写入批次（可回滚）</h3>
          <span className="text-[10px] text-slate-500">回滚 = 物理删除，不可恢复：知识卡片凭 ref，向量记忆凭 vector_ids 按 id 真删（迁移前老批次才用 msg_id；两者都没有则需人工）</span>
          <button type="button" className={BTN} onClick={() => loadBatches()} disabled={batchBusy !== ''} aria-label="刷新批次">
            <RefreshCw size={11} /> 刷新
          </button>
        </div>
        {batchErr && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{batchErr}</div>}
        {batchMsg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{batchMsg}</div>}
        {batches.length === 0 ? (
          <div className="py-3 text-center text-[11px] text-slate-600">还没有导入批次</div>
        ) : (
          <div className="max-h-64 overflow-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-900 text-slate-400">
                <tr>
                  <th className="px-2 py-1">批次</th>
                  <th className="px-2 py-1">载体</th>
                  <th className="px-2 py-1">条数</th>
                  <th className="px-2 py-1">提交时间</th>
                  <th className="px-2 py-1">ref</th>
                  <th className="px-2 py-1">操作</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.batch_id} className="border-t border-slate-800/70">
                    <td className="px-2 py-1 font-mono text-slate-400">{b.batch_id}</td>
                    <td className="px-2 py-1 text-slate-300">{b.carrier_label}</td>
                    <td className="px-2 py-1 text-slate-300">{b.records}</td>
                    <td className="px-2 py-1 text-slate-500">{b.committed_at || '-'}</td>
                    <td className="px-2 py-1 text-slate-500">{b.ref || '-'}</td>
                    <td className="px-2 py-1">
                      {b.rollback === 'manual' ? (
                        <span className="text-[10px] text-slate-500" title="该批次没有 vector_ids / msg_id 可删（老批次），无法按 id 自动删除">需人工</span>
                      ) : (
                        <button type="button" className={BTN} onClick={() => void beginRollback(b.batch_id)} disabled={batchBusy !== '' || previewBusy !== ''} aria-label={'回滚 ' + b.batch_id} title="先看将删除的内容，再确认（物理删除、不可恢复）">
                          <Undo2 size={11} /> 回滚（先看内容）
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>


      {/* 回滚前预览：两个入口（已写入批次 / 向量文档）共用同一条预览条 */}
      {pendingRollback && (
        <div className="mt-4 rounded-lg border border-red-900/60 bg-red-500/5 px-3 py-2 text-[11px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-red-300">即将物理删除批次 {pendingRollback}</span>
            <span className="text-slate-500">不可恢复：向量按 id 真删，批次清单同时摘除</span>
          </div>
          {previewBusy === pendingRollback ? (
            <div className="mt-2 flex items-center gap-1 text-slate-400">
              <Loader2 size={11} className="animate-spin" /> 正在读取将删除的内容…
            </div>
          ) : rollbackPreview ? (
            <>
              <div className="mt-1 text-slate-400">
                {rollbackPreview.items_preview?.length ? (
                  <>将删除 <b className="text-red-300">{rollbackPreview.items_preview.length}</b> 条向量，共{' '}
                    <b className="text-red-300">{rollbackPreview.items_preview.reduce((n, it) => n + (it.chars || 0), 0)}</b> 字符</>
                ) : (
                  <>将删除：<span className="font-mono text-slate-300">{rollbackPreview.would_delete || '-'}</span></>
                )}
              </div>
              {(rollbackPreview.items_preview ?? []).map((it) => (
                <div key={it.id} className="mt-2 rounded border border-slate-800 bg-slate-950/60 p-2">
                  <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                    <span className="font-mono text-slate-400">{it.id}</span>
                    <span>{it.chars} 字符</span>
                    {it.metadata?.origin ? (
                      <span className="rounded bg-sky-500/15 px-1 text-sky-300">{String(it.metadata.origin)}</span>
                    ) : null}
                  </div>
                  <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[10px] leading-5 text-slate-300">
                    {it.preview || '（内容为空或读取失败）'}
                  </pre>
                </div>
              ))}
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  className={BTN_PRIMARY + ' !bg-red-600 hover:!bg-red-500'}
                  onClick={() => void doRollback(pendingRollback)}
                  disabled={batchBusy !== ''}
                  aria-label={'确认回滚 ' + pendingRollback}
                >
                  {batchBusy === pendingRollback
                    ? <Loader2 size={11} className="animate-spin" />
                    : <Undo2 size={11} />} 确认删除（不可恢复）
                </button>
                <button type="button" className={BTN} onClick={cancelRollback} aria-label={'取消回滚 ' + pendingRollback}>取消</button>
              </div>
            </>
          ) : null}
        </div>
      )}

      {/* §10 第 3 步：外部导入的向量文档（按 metadata.source 过滤 + 来源徽章） */}
      <div className="mt-5 border-t border-slate-800 pt-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Brain size={13} className="text-emerald-400" />
          <h3 className="text-xs font-semibold text-slate-200">向量文档（外部导入）</h3>
          <span className="text-[10px] text-slate-500">
            只列 metadata.source=memory_import 的那些；徽章标来源与摘要方式。回滚 = 物理删除（先点一次，再确认）
          </span>
          <button type="button" className={BTN} onClick={() => loadVectors()} disabled={vecBusy} aria-label="刷新向量文档">
            {vecBusy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
        {vecErr && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{vecErr}</div>}
        {vectors.length === 0 ? (
          <div className="py-3 text-center text-[11px] text-slate-600">
            还没有外部导入的向量文档（写入或迁移完成后会出现在这里）
          </div>
        ) : (
          <div className="max-h-64 overflow-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-900 text-slate-400">
                <tr>
                  <th className="px-2 py-1">来源</th>
                  <th className="px-2 py-1">批次</th>
                  <th className="px-2 py-1">周期</th>
                  <th className="px-2 py-1">摘要</th>
                  <th className="px-2 py-1">字符</th>
                  <th className="px-2 py-1">向量 id</th>
                  <th className="px-2 py-1">操作</th>
                </tr>
              </thead>
              <tbody>
                {vectors.map((v) => (
                  <tr key={v.id} className="border-t border-slate-800/70">
                    <td className="px-2 py-1">
                      <span
                        className={'rounded px-1 text-[9px] ' + (v.origin === 'trae'
                          ? 'bg-violet-500/15 text-violet-300'
                          : 'bg-sky-500/15 text-sky-300')}
                        title={'外部来源：' + (v.origin || '未知')}
                      >
                        {v.origin || '未知'}
                      </span>
                      {v.migrated_from && (
                        <span className="ml-1 rounded bg-amber-500/15 px-1 text-[9px] text-amber-300" title={'从 ' + v.migrated_from + ' 迁移而来'}>
                          迁移
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1 font-mono text-slate-400">{v.batch_id || '-'}</td>
                    <td className="px-2 py-1 text-slate-400">{v.period || '-'}</td>
                    <td className="px-2 py-1">
                      <span className={'rounded px-1 text-[9px] ' + (v.digest_source === 'llm'
                        ? 'bg-violet-500/15 text-violet-300'
                        : 'bg-slate-700/40 text-slate-400')}>
                        {v.digest_source === 'llm' ? 'LLM' : '规则'}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-slate-400">{v.char_count}</td>
                    <td className="px-2 py-1 font-mono text-slate-500">{v.id}</td>
                    <td className="px-2 py-1">
                      {/* 【2026-10-25 修正】此处原先是"一键真删"（用户实测误点过）。
                          现在与批次区统一：点一下 → 拉 dry-run 预览内容 → 在预览条里确认/取消。 */}
                      <button
                        type="button"
                        className={BTN}
                        onClick={() => void beginRollback(v.batch_id)}
                        disabled={batchBusy !== '' || previewBusy !== '' || !v.batch_id}
                        aria-label={'回滚向量批次 ' + v.batch_id}
                        title="回滚 = 物理删除（不可恢复）：先看将删除的内容，再确认"
                      >
                        <Undo2 size={11} /> 回滚（先看内容）
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  )
}