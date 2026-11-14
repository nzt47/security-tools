/**
 * 外部会话「全话」迁移卡片（记忆运维页）
 * -----------------------------------------------------------------
 * 与旁边「导入外部记忆」的区别：
 * - 那边把 Claude/Trae 提炼成知识卡片/向量（按批次确认）；
 * - 这边把 DSH / Claude / Trae 的**整段会话**按来源幂等追加进云枢消息流
 *   （data/messages.jsonl），只截断 + 脱敏，不摘要、不改写、不删既有消息。
 *
 * 【不易】真写判据在后端：dry_run=false 且 confirm=true。这里的勾选只是界面表达。
 */
import { useEffect, useState } from 'react'
import { DownloadCloud, Loader2, Play } from 'lucide-react'
import { Card } from '@/pages/hub/components/ui'
import {
  MemoryImportApiError, fetchMemorySessionLatest, fetchMemorySessionSources,
  importMemorySessions,
  type MemorySessionReport, type MemorySessionSource,
} from '@/lib/memoryImportApi'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-2 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_PRIMARY = 'inline-flex items-center gap-1 rounded-md bg-cyan-600 px-3 py-1.5 text-[11px] font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40'

const SOURCE_LABEL: Record<string, string> = {
  dsh: 'DeepSeek Harness',
  claude: 'Claude Code',
  trae: 'Trae',
}

function errText(e: unknown): string {
  if (e instanceof MemoryImportApiError) return e.message
  return e instanceof Error ? e.message : String(e)
}

function num(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export default function MemorySessionsImportCard() {
  const [sources, setSources] = useState<MemorySessionSource[]>([])
  const [source, setSource] = useState('dsh')
  const [limit, setLimit] = useState(0)
  const [includeSubagents, setIncludeSubagents] = useState(true)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState<'' | 'dry' | 'exec'>('')
  const [report, setReport] = useState<MemorySessionReport | null>(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    let alive = true
    fetchMemorySessionSources()
      .then((d) => {
        if (!alive) return
        const items = d?.sources ?? []
        setSources(items)
        const first = items.find((s) => s.available)
        if (first) setSource(first.id)
      })
      .catch((e) => { if (alive) setErr('探测会话来源失败：' + errText(e)) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    fetchMemorySessionLatest(source)
      .then((d) => { if (alive && d?.report) setReport(d.report) })
      .catch(() => { /* 最近报告只是便利，读不到不影响操作 */ })
    return () => { alive = false }
  }, [source])

  const run = async (dryRun: boolean) => {
    setBusy(dryRun ? 'dry' : 'exec')
    setErr(''); setMsg('')
    try {
      const result = await importMemorySessions({
        source, dry_run: dryRun, confirm: !dryRun,
        limit_sessions: limit > 0 ? limit : undefined,
        include_subagents: includeSubagents,
      })
      setReport(result)
      setMsg(dryRun
        ? 'dry-run 完成：只统计，未写入任何消息'
        : '已追加 ' + num(result.messages?.imported) + ' 条消息（重跑不会重复）')
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy('')
    }
  }

  const current = sources.find((s) => s.id === source)
  const sessions = report?.sessions ?? {}
  const messages = report?.messages ?? {}

  return (
    <Card title="② 全话导入：原始会话 → 消息流">
      <p className="mb-3 text-xs leading-6 text-slate-500">
        把 <span className="text-slate-300">DeepSeek Harness / Claude Code / Trae</span> 的整段会话
        <b className="text-slate-300">原文</b>按来源追加进云枢消息流（data/messages.jsonl，带 session_id）。
        只截断 + 脱敏，<span className="text-slate-400">不摘要、不改写、不删既有消息</span>；
        每行带幂等键，同一来源重跑只补新增。上面「① 提炼导入」产的是摘要片段，这里才是完整对话。
        <span className="text-slate-400">先「只跑 dry-run」看计数（不写任何东西），确认后再执行导入。</span>
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-300">
        {sources.length === 0 && <span className="text-slate-600">探测来源中…</span>}
        {sources.map((s) => (
          <label key={s.id} className={'inline-flex items-center gap-1 ' + (s.available ? '' : 'opacity-40')}
            title={s.detail}>
            <input
              type="radio"
              name="memory-session-source"
              checked={source === s.id}
              disabled={!s.available || busy !== ''}
              onChange={() => { setSource(s.id); setConfirmed(false) }}
              aria-label={'会话来源 ' + s.label}
            />
            {s.label}
            <span className="text-[10px] text-slate-500">
              {!s.available ? '（未找到）'
                : s.sessions > 0 ? '(' + s.sessions + ' 个会话)'
                  : '（本地仅提问轨迹）'}
            </span>
          </label>
        ))}
        <label className="inline-flex items-center gap-1"
          title="0 = 全部；填 N 则每次最多处理 N 个还没导入的会话，可反复执行直到「待导入」为 0">
          每次最多处理
          <input
            type="number"
            min={0}
            value={limit}
            disabled={busy !== ''}
            onChange={(e) => setLimit(Math.max(0, Number(e.target.value) || 0))}
            className="w-14 rounded border border-slate-700 bg-slate-900 px-1 py-0.5 text-[11px]"
            aria-label="最多待导入会话数"
          />
          个待导入会话（0=全部）
        </label>
        {source === 'dsh' && (
          <label className="inline-flex items-center gap-1" title="子代理会话也是真实对话；只导主会话时取消勾选">
            <input
              type="checkbox"
              checked={includeSubagents}
              disabled={busy !== ''}
              onChange={(e) => setIncludeSubagents(e.target.checked)}
              aria-label="包含子代理会话"
            />
            包含子代理会话
          </label>
        )}
        <button type="button" className={BTN} onClick={() => void run(true)} disabled={busy !== '' || !current?.available} aria-label="只跑 dry-run">
          {busy === 'dry' ? <Loader2 size={11} className="animate-spin" /> : <DownloadCloud size={11} />} 只跑 dry-run
        </button>
        <button type="button" className={BTN_PRIMARY} onClick={() => void run(false)}
          disabled={busy !== '' || !confirmed || !current?.available} aria-label="执行导入">
          {busy === 'exec' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 执行导入（写入）
        </button>
      </div>

      <label className="mb-2 inline-flex items-center gap-1 text-[11px] text-slate-400">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={busy !== ''}
          onChange={(e) => setConfirmed(e.target.checked)}
          aria-label="确认只追加不删除"
        />
        我已确认：只追加新行、不删既有消息（重跑幂等）
      </label>

      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      {report && (
        <div className="mt-1 text-[11px] text-slate-400">
          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>来源 <b className="text-slate-200">{SOURCE_LABEL[report.source] ?? report.source}</b></span>
            <span>{report.dry_run ? 'dry-run（未写入）' : '已写入'}</span>
            <span>耗时 <b className="text-slate-200">{num(report.duration_ms)} ms</b></span>
            {report.partial && <span className="text-amber-300">本轮达到上限，已提前停止</span>}
            {report.has_more && <span className="text-amber-300">还有待导入会话未处理（可再执行一次）</span>}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span>会话 发现 <b className="text-slate-200">{num(sessions.seen)}</b></span>
            <span>待导入 <b className="text-amber-300">{num(sessions.pending)}</b></span>
            <span>本轮扫描 <b className="text-slate-200">{num(sessions.scanned)}</b></span>
            <span>跳过（未变化）<b className="text-slate-200">{num(sessions.skipped_unchanged)}</b></span>
            <span>空会话 <b className="text-slate-200">{num(sessions.empty)}</b></span>
            <span>失败 <b className="text-slate-200">{num(sessions.failed)}</b></span>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span>消息 新增 <b className="text-cyan-300">{num(messages.imported)}</b></span>
            <span>已存在 <b className="text-slate-200">{num(messages.skipped_existing)}</b></span>
            <span>脱敏拒绝 <b className="text-amber-300">{num(messages.skipped_redacted)}</b></span>
            <span>截断 <b className="text-slate-200">{num(report.truncated_messages)}</b></span>
            <span>字符 <b className="text-slate-200">{num(report.chars?.imported)}</b></span>
          </div>
          {report.redactions && Object.keys(report.redactions).length > 0 && (
            <div className="mt-1 text-[10px] text-slate-500">
              脱敏命中：{Object.entries(report.redactions).map(([k, v]) => k + ' ' + v).join(' / ')}
            </div>
          )}
          {report.report_path && (
            <div className="mt-1 break-all font-mono text-[10px] text-slate-600">报告：{report.report_path}</div>
          )}
        </div>
      )}
    </Card>
  )
}
