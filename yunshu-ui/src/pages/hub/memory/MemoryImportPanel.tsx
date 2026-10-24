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
import { DownloadCloud, Loader2, RefreshCw, ShieldCheck } from 'lucide-react'
import { Card } from '../components/ui'
import {
  MemoryImportApiError, commitMemoryImport, fetchMemoryImportSources,
  previewMemoryImport, type MemoryImportCommitResult, type MemoryImportPlan,
  type MemoryImportSource,
} from '@/lib/memoryImportApi'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-2 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_PRIMARY = 'inline-flex items-center gap-1 rounded-md bg-cyan-600 px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40'

function errText(e: unknown): string {
  if (e instanceof MemoryImportApiError) return e.message
  return e instanceof Error ? e.message : String(e)
}

export default function MemoryImportPanel() {
  const [sources, setSources] = useState<MemoryImportSource[]>([])
  const [note, setNote] = useState('')
  const [pickedSources, setPickedSources] = useState<string[]>(['claude', 'trae'])
  const [includeTranscripts, setIncludeTranscripts] = useState(true)
  const [plan, setPlan] = useState<MemoryImportPlan | null>(null)
  const [pickedBatches, setPickedBatches] = useState<string[]>([])
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState<'' | 'preview' | 'commit' | 'dry'>('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [result, setResult] = useState<MemoryImportCommitResult | null>(null)

  useEffect(() => {
    let alive = true
    fetchMemoryImportSources()
      .then((d) => {
        if (!alive) return
        setSources(d?.sources ?? [])
        setNote(d?.note ?? '')
        const available = (d?.sources ?? []).filter((s) => s.available).map((s) => s.id)
        if (available.length) setPickedSources(available)
      })
      .catch((e) => { if (alive) setErr('探测来源失败：' + errText(e)) })
    return () => { alive = false }
  }, [])

  const toggleSource = (id: string) => {
    setPickedSources((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const doPreview = async () => {
    if (!pickedSources.length) { setErr('请至少选择一个来源'); return }
    setBusy('preview'); setErr(''); setMsg(''); setResult(null)
    try {
      const d = await previewMemoryImport({ sources: pickedSources, include_transcripts: includeTranscripts })
      setPlan(d)
      setPickedBatches([])
      setConfirmed(false)
      setMsg('预览已生成（' + (d?.batches?.length ?? 0) + ' 个批次，未写入任何记忆）')
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
        把 Claude / Trae 的规则与会话轨迹导入云枢：规则进知识卡片，会话/提问进长期记忆。
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
        <button type="button" className={BTN} onClick={() => void doPreview()} disabled={busy !== ''} aria-label="生成预览">
          {busy === 'preview' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 生成预览
        </button>
      </div>
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
    </Card>
  )
}
