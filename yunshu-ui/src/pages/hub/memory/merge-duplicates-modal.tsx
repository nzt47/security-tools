/**
 * 合并重复技能（2026-10-30）—— 把「知情的行内入口」补上。
 *
 * 背景：合并能力其实早就有（后端 /assess/merge-safe 先写两侧快照、返回 merge_id，可 undo），
 * 但入口只藏在「详情抽屉 → 重复 Tab」里，而那个 Tab 的提示语还写着「只报告；合并/归档到
 * 吞噬 · 存量治理」——两处都不指向真实出口，用户自然找不到「合并」。
 *
 * 这个弹窗被两个地方调用：
 *   · 技能行「更多 → 合并重复…」；
 *   · 评审-评估报告里出现重复/重叠发现项时的「合并重复…」。
 *
 * 语义（与后端一致，别改口径）：合并到**本技能** = 把对方内容/标签并进来并删除对方；
 * 先写两侧快照，可用「撤销合并（merge_id）」回滚。判据：同一触发面只留一个主人。
 */
import { useCallback, useEffect, useState } from 'react'
import { Copy, Loader2, RefreshCw, X } from 'lucide-react'
import { hubGet, hubPost } from '../components/ui'
import {
  SKILLS_MGMT_ASSESS_MERGE_SAFE,
  SKILLS_MGMT_ASSESS_MERGE_UNDO,
  skillDuplicates,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = `${BTN} border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10`

interface DupRow {
  // 后端 find_duplicates_for 的字段是 other_id / other_name（不是 skill_id/name）——2026-10-30 实测踩坑：
  // 旧代码只认 skill_id/id，取不到就回退成合成键 `dup-N`，合并时后端报「技能不存在: dup-9」（HTTP 400）。
  other_id?: string; other_name?: string
  skill_id?: string; id?: string; name?: string
  jaccard?: number; content_hash_match?: boolean; recommend_action?: string
}
interface DupsResp { ok?: boolean; error?: string; duplicates?: DupRow[]; total?: number }
interface MergeResp { ok?: boolean; error?: string; merge_id?: string }

export default function MergeDuplicatesModal({ skillId, skillName, onClose, onDone }: {
  skillId: string
  skillName?: string
  onClose: () => void
  onDone?: () => void
}) {
  const [dups, setDups] = useState<DupsResp | null>(null)
  const [minJaccard, setMinJaccard] = useState('0.7')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [pending, setPending] = useState('')
  const [mergeId, setMergeId] = useState('')

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const q = minJaccard.trim() ? `?min_jaccard=${encodeURIComponent(minJaccard.trim())}` : ''
      const r = await hubGet<DupsResp>(skillDuplicates(skillId) + q)
      if (r?.ok === false) setErr(r.error || '读取重复技能失败')
      else setDups(r)
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy('') }
  }, [skillId, minJaccard])

  useEffect(() => { void load() }, [load])

  const mergeInto = async (srcId: string) => {
    if (!srcId) { setErr('缺少源技能 id：请「重新查重」后再试'); return }
    setBusy('merge'); setErr(''); setMsg('')
    try {
      const r = await hubPost<MergeResp>(SKILLS_MGMT_ASSESS_MERGE_SAFE,
        { src_id: srcId, dst_id: skillId, strategy: 'auto' })
      if (r?.ok === false) { setErr('合并失败：' + (r.error || '后端返回 ok=false')); return }
      setMergeId(r?.merge_id ?? '')
      setPending('')
      setMsg(`已把「${srcId}」合并进本技能（两侧快照已写，可用下方「撤销合并」回滚）。`)
      onDone?.()
      await load()
    } catch (e) { setErr('合并失败：' + (e instanceof Error ? e.message : String(e))) }
    finally { setBusy('') }
  }

  const undo = async () => {
    if (!mergeId) return
    setBusy('undo'); setErr(''); setMsg('')
    try {
      const r = await hubPost<MergeResp>(SKILLS_MGMT_ASSESS_MERGE_UNDO, { merge_id: mergeId })
      if (r?.ok === false) { setErr('撤销失败：' + (r.error || '后端返回 ok=false')); return }
      setMsg(`已撤销合并 ${mergeId}（被合并的技能已恢复）。`)
      setMergeId('')
      onDone?.()
      await load()
    } catch (e) { setErr('撤销失败：' + (e instanceof Error ? e.message : String(e))) }
    finally { setBusy('') }
  }

  const rows = dups?.duplicates ?? []
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()} data-testid="merge-duplicates-modal">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
            <Copy size={13} className="text-cyan-400" /> 合并重复技能 · {skillName || skillId}
          </h3>
          <button type="button" onClick={onClose} aria-label="关闭合并重复弹窗" className="text-slate-500 hover:text-slate-300"><X size={14} /></button>
        </div>
        <p className="mb-3 text-[11px] leading-relaxed text-slate-400">
          「合并到本技能」= 把对方的内容/标签并进来并删除对方；后端**先写两侧快照**，
          可用「撤销合并」按 merge_id 回滚。判据是<strong className="text-slate-300">同一触发面只留一个主人</strong>；
          若对方确实是重复复述，也可以直接在行内「停用/删除」。
        </p>
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1 text-[10px] text-slate-500">
            Jaccard ≥
            <input value={minJaccard} onChange={(e) => setMinJaccard(e.target.value)} aria-label="重复阈值"
              className="w-16 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-hidden focus:border-cyan-600" />
          </label>
          <button type="button" className={BTN} onClick={() => void load()} disabled={busy !== ''} aria-label="重新查重">
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 重新查重
          </button>
          {dups?.total !== undefined && <span className="text-[10px] text-slate-500">共 {dups.total} 条候选</span>}
        </div>
      {busy === 'load' && !dups && (
        <div className="flex items-center gap-2 py-4 text-[11px] text-slate-500">
          <Loader2 size={12} className="animate-spin" /> 读取重复技能…
        </div>
      )}
      {dups && rows.length === 0 && (
        <p className="py-3 text-[11px] text-slate-500">没有超过阈值的重复技能（阈值可调低再试）。</p>
      )}
      {rows.length > 0 && (
        <ul className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {rows.map((d) => {
            // 只认真实 id：合成键绝不能进合并请求（那正是 dup-9 那个 400 的来源）
            const key = d.other_id || d.skill_id || d.id || ''
            const label = d.other_name || d.name || ''
            return (
              <li key={key} className="flex flex-wrap items-center gap-2 rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1.5 text-[11px]">
                <span className="font-mono text-cyan-300">{key}</span>
                <span className="truncate text-slate-300">{label}</span>
                {d.content_hash_match && <span className="rounded-full border border-emerald-800/60 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-300">内容完全相同</span>}
                {d.recommend_action === 'merge' && !d.content_hash_match && <span className="rounded-full border border-cyan-800/60 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-300">建议合并</span>}
                <span className="ml-auto font-mono text-[10px] text-amber-300">
                  {d.jaccard !== undefined ? Number(d.jaccard).toFixed(3) : ''}
                </span>
                {!key ? (
                  <span className="text-[10px] text-red-400" title="接口没有返回 other_id/id ⇒ 无法安全合并">缺少技能 id，无法合并</span>
                ) : pending === key ? (
                  <span className="flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-amber-300">确认合并？会删除它并写备份</span>
                    <button type="button" className={BTN} onClick={() => void mergeInto(key)} disabled={busy !== ''}>确认合并</button>
                    <button type="button" className={BTN} onClick={() => setPending('')}>取消</button>
                  </span>
                ) : (
                  <button type="button" className={BTN} onClick={() => setPending(key)} disabled={busy !== ''}
                    title="把该技能合并进本技能：先写两侧快照（可撤销），再删源技能">
                    合并到本技能
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {err && <p className="mt-2 text-[11px] text-red-400">{err}</p>}
      {msg && <p className="mt-2 text-[11px] text-emerald-300">{msg}</p>}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {mergeId && (
          <button type="button" className={BTN} onClick={() => void undo()} disabled={busy !== ''}>
            撤销合并（{mergeId}）
          </button>
        )}
        <button type="button" className={BTN_EM} onClick={onClose}>关闭</button>
      </div>
      </div>
    </div>
  )
}
