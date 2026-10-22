/**
 * 从记忆抽象技能草稿（原「技能中心 · 维护」的抽象区）。
 *
 * 这是“生成/获取”来源，不是维护动作；迁到吞噬的获取页。
 * 默认 auto_register=false：只出草稿，不自动注册。
 */
import { useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { hubPost } from '../components/ui'
import { SKILLS_MGMT_ABSTRACT_FROM_MEMORY } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-2.5 py-1.5 text-xs text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

interface DraftRow {
  draft_name?: string
  draft_skill_id?: string
  cluster_size?: number
  success_rate?: number
  quality_gate_passed?: boolean
  registered?: boolean
  duplicate_of?: string
  quality_gate_reasons?: string[]
}

interface AbstractResult {
  ok?: boolean
  error?: string
  total_clusters?: number
  passed_clusters?: number
  registered_count?: number
  drafts?: DraftRow[]
}

export default function MemoryDraftPanel() {
  const [days, setDays] = useState('30')
  const [maxSkills, setMaxSkills] = useState('5')
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<AbstractResult | null>(null)

  const run = async () => {
    setBusy(true); setErr(''); setResult(null)
    try {
      const r = await hubPost<AbstractResult>(SKILLS_MGMT_ABSTRACT_FROM_MEMORY, {
        days: Number(days || 30),
        max_skills: Number(maxSkills || 5),
        auto_register: register,
      })
      if (r?.ok === false) { setErr('从记忆抽象失败：' + (r.error || '后端返回 ok=false')); return }
      setResult(r)
    } catch (e) {
      setErr('从记忆抽象失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Sparkles size={13} className="text-cyan-400" />
        <h3 className="text-sm font-medium text-slate-200">从记忆生成技能草稿</h3>
        <span className="text-[10px] text-slate-500">聚类最近记忆 → 质量闸 → 草稿（默认不自动注册）</span>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-slate-500">
          回溯天数
          <input className={INPUT + ' w-20'} value={days} onChange={(e) => setDays(e.target.value)} aria-label="抽象回溯天数" />
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-slate-500">
          最多草稿
          <input className={INPUT + ' w-20'} value={maxSkills} onChange={(e) => setMaxSkills(e.target.value)} aria-label="最多草稿数" />
        </label>
        <label className="mb-1 flex items-center gap-1 text-[11px] text-slate-400">
          <input type="checkbox" checked={register} onChange={(e) => setRegister(e.target.checked)} aria-label="自动注册草稿" />
          自动注册草稿
        </label>
        <button type="button" className={BTN} onClick={() => void run()} disabled={busy} aria-label="从记忆抽象技能">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />} 生成草稿
        </button>
      </div>
      {err && <p className="mt-2 text-[11px] text-red-400">{err}</p>}
      {result && (
        <div className="mt-2 rounded border border-slate-800 bg-slate-950/60 p-2 text-[11px] text-slate-300">
          <div className="text-[10px] text-slate-500">
            聚类 {result.total_clusters ?? 0} · 过闸 {result.passed_clusters ?? 0} · 注册 {result.registered_count ?? 0}
          </div>
          {(result.drafts ?? []).length === 0 ? (
            <div className="mt-1 text-slate-500">没有生成草稿。</div>
          ) : (
            <div className="mt-1 flex flex-col gap-0.5">
              {(result.drafts ?? []).map((d) => (
                <span key={d.draft_skill_id || d.draft_name}>
                  {d.draft_name || d.draft_skill_id}
                  {d.duplicate_of ? '（重复：' + d.duplicate_of + '）' : ''}
                  {d.quality_gate_passed === false ? '（质量闸未过）' : ''}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
