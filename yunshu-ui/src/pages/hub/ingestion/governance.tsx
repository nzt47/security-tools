/**
 * 存量治理面板（P6）：整理 / 合并 / 拆分 / 剔除 / 重构。
 *
 * 纪律：
 * - 扫描只读；计划只给建议；执行默认 dry-run，真执行必须显式勾确认；
 * - 归档优先、拆分只写草稿；物理删除只走 delete_orphans，且二次 window.confirm；
 * - 每个动作展示 evidence/reason，不在前端重算规则。
 */
import { useState } from 'react'
import {
  AlertTriangle, Archive, CheckCircle2, GitBranch, Loader2, Merge, RefreshCw, Scissors,
  ShieldCheck, Trash2, Wrench,
} from 'lucide-react'
import { Badge, Card, ErrorBox } from '../components/ui'
import {
  applyGovernance,
  planGovernance,
  scanGovernance,
  type GovernanceAction,
  type GovernanceApplyResult,
  type GovernancePlan,
  type GovernanceScan,
} from './governanceApi'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'

const ACTION_META: Record<string, { label: string; icon: typeof Merge; tone: 'green' | 'amber' | 'red' | 'cyan' | 'slate' }> = {
  merge: { label: '合并', icon: Merge, tone: 'green' },
  archive: { label: '归档', icon: Archive, tone: 'amber' },
  split: { label: '拆分', icon: Scissors, tone: 'cyan' },
  refactor: { label: '重构', icon: Wrench, tone: 'slate' },
  delete_orphans: { label: '剔除孤儿', icon: Trash2, tone: 'red' },
}

function meta(action: string) {
  return ACTION_META[action] ?? { label: action, icon: GitBranch, tone: 'slate' as const }
}

export default function GovernancePanel() {
  const [scan, setScan] = useState<GovernanceScan | null>(null)
  const [plan, setPlan] = useState<GovernancePlan | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [picked, setPicked] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [preview, setPreview] = useState<GovernanceApplyResult | null>(null)
  const [applied, setApplied] = useState<GovernanceApplyResult | null>(null)

  const load = async () => {
    setBusy('scan'); setError(''); setMsg(''); setPicked(''); setConfirmed(false)
    setPreview(null); setApplied(null)
    try {
      const s = await scanGovernance()
      const p = await planGovernance()
      setScan(s); setPlan(p)
      setMsg('盘点 ' + s.total + ' 个技能；计划 ' + p.actions.length + ' 条动作（未执行）')
    } catch (e) {
      setError('盘点/计划失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy('')
    }
  }

  const run = async (a: GovernanceAction, dryRun: boolean) => {
    if (!plan) return
    if (!dryRun && !confirmed) { setError('先勾选「我已核对」'); return }
    if (!dryRun && a.destructive) {
      const ok = window.confirm('该动作会物理清理数据：' + a.title + '。确定继续？')
      if (!ok) return
    }
    setBusy(a.action + ':' + a.target); setError(''); setMsg('')
    try {
      const out = await applyGovernance({
        plan_id: plan.plan_id, action: a.action, target: a.target,
        confirm: dryRun ? false : true, dry_run: dryRun,
      })
      if (dryRun) { setPreview(out); setMsg('dry-run 完成（未改任何数据）') }
      else { setApplied(out); setPreview(null); setMsg('已执行并审计：' + a.title) }
    } catch (e) {
      setError('执行失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card title="存量资产治理" actions={(
        <button type="button" className={BTN_EM} onClick={() => void load()} disabled={busy !== ''}>
          {busy === 'scan' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          盘点并生成计划
        </button>
      )}>
        <p className="text-[11px] leading-relaxed text-slate-500">
          扫描现有技能，给出合并 / 归档 / 拆分 / 补描述 / 清理孤儿建议。默认 dry-run；
          归档不删除、拆分只写子草稿，真执行需勾确认并留审计。
        </p>
        {error && <div className="mt-2"><ErrorBox message={error} /></div>}
        {msg && <div className="mt-2 rounded border border-cyan-900/60 bg-cyan-950/20 px-2 py-1 text-[11px] text-cyan-300">{msg}</div>}
        {scan && (
          <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            {[
              ['技能', scan.total],
              ['重复', scan.summary.duplicate ?? 0],
              ['可归档', scan.summary.archive ?? 0],
              ['可拆分', scan.summary.split ?? 0],
              ['缺描述', scan.summary.refactor ?? 0],
              ['孤儿', scan.summary.orphan ?? 0],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded border border-slate-800 bg-slate-950/50 px-2 py-1.5">
                <div className="text-[10px] text-slate-500">{label}</div>
                <div className="font-mono text-sm text-cyan-300">{value}</div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {plan && (
        <Card title={'治理计划 · ' + plan.plan_id} actions={(
          <span className="text-[10px] text-slate-500">{plan.actions.length} 条动作</span>
        )}>
          {plan.actions.length === 0 ? (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <ShieldCheck size={14} /> 没有需要治理的动作。
            </div>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={confirmed}
                    aria-label="我已核对治理计划"
                    onChange={(e) => setConfirmed(e.target.checked)} />
                  我已核对计划（动作/目标/证据）
                </label>
                <button type="button" className={BTN_EM}
                  disabled={!picked || !confirmed || busy !== ''}
                  onClick={() => {
                    const a = plan.actions.find((x) => x.action + ':' + x.target === picked)
                    if (a) void run(a, false)
                  }}>
                  <CheckCircle2 size={12} /> 执行所选
                </button>
                {picked && <span className="font-mono text-[10px] text-cyan-300">{picked}</span>}
              </div>
              <div className="flex flex-col divide-y divide-slate-800 overflow-hidden rounded border border-slate-800">
                {plan.actions.map((a) => {
                  const m = meta(a.action)
                  const Icon = m.icon
                  const key = a.action + ':' + a.target
                  return (
                    <div key={key} className="flex flex-wrap items-center gap-2 bg-slate-950/40 px-2 py-1.5 text-[11px]">
                      <input type="radio" name="governance-action" checked={picked === key}
                        aria-label={'选择 ' + a.title}
                        onChange={() => { setPicked(key); setConfirmed(false); setPreview(null); setApplied(null) }} />
                      <Badge color={m.tone}><Icon size={10} /> {m.label}</Badge>
                      <span className="font-mono text-cyan-300">{a.target}</span>
                      <span className="text-slate-300">{a.title}</span>
                      <span className="max-w-[45%] truncate text-slate-500" title={a.reason}>{a.reason}</span>
                      <span className="ml-auto flex items-center gap-1.5">
                        <button type="button" className={BTN}
                          aria-label={'dry-run ' + a.title}
                          onClick={() => void run(a, true)} disabled={busy !== ''}>
                          {busy === key ? <Loader2 size={11} className="animate-spin" /> : null} dry-run
                        </button>
                        {a.destructive && <Badge color="red"><AlertTriangle size={10} /> 破坏性</Badge>}
                      </span>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </Card>
      )}

      {preview && (
        <Card title="dry-run 影响（未执行）">
          <div className="text-[11px] text-slate-300">
            <span className="font-mono text-cyan-300">{preview.action}</span> / {preview.target}
          </div>
          <pre className="mt-2 max-h-48 overflow-auto rounded border border-slate-800 bg-slate-950 p-2 text-[10px] text-slate-400">
            {JSON.stringify(preview.would ?? {}, null, 2)}
          </pre>
        </Card>
      )}

      {applied && (
        <Card title="执行结果">
          <div className="text-[11px] text-slate-300">
            <span className="font-mono text-cyan-300">{applied.action}</span> / {applied.target}
            {applied.audit?.seq ? <span className="ml-2 text-slate-500">audit seq {applied.audit.seq}</span> : null}
          </div>
          <pre className="mt-2 max-h-48 overflow-auto rounded border border-slate-800 bg-slate-950 p-2 text-[10px] text-slate-400">
            {JSON.stringify(applied.result ?? {}, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  )
}
