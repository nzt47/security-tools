/**
 * GenerateRequirementModal —— 把“对话提示词中的能力要求”自动写成技能
 * ------------------------------------------------------------------
 * 会话页快捷入口与技能中心共用：粘贴要求 → create/ai 生成草稿（失败自动
 * 回退模板）→ 服务端自动执行评审-消化（权限/合规/兼容性），有审核兜底。
 * 成功后可到「记忆管理 → 技能中心 → LLM 技能」查看报告、编辑并发布。
 */
import { useState } from 'react'
import { Loader2, Lightbulb, ShieldCheck } from 'lucide-react'
import { hubPost } from '../../hub/components/ui'

const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

interface Props {
  initialIntent?: string
  onClose: () => void
  onDone?: (skillId?: string) => void
}

export default function GenerateRequirementModal({ initialIntent = '', onClose, onDone }: Props) {
  const [name, setName] = useState('')
  const [intent, setIntent] = useState(initialIntent)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [okMsg, setOkMsg] = useState('')
  const [createdId, setCreatedId] = useState<string | undefined>(undefined)

  const submit = async () => {
    if (!intent.trim()) return
    setBusy(true); setErr(''); setOkMsg('')
    try {
      const r = await hubPost<{ skill?: { id?: string; name?: string } }>('/api/skills-mgmt/create/ai', {
        name: name.trim() || `auto-req-${Date.now() % 1000000}`,
        intent: intent.trim(),
        tags: ['auto', 'requirement'],
        category: 'custom',
      })
      const s = r?.skill
      setCreatedId(s?.id)
      setOkMsg(
        s
          ? `已生成并自动评审-消化：「${s.name}」（${s.id}）——请到「技能中心 → LLM 技能」查看报告，通过后发布即成为自身能力。`
          : '已生成（未返回详情）。',
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-slate-100">
          <Lightbulb size={15} className="text-cyan-400" /> 从对话要求生成技能（自动评审-消化）
        </h3>
        <p className="mb-3 text-[11px] leading-relaxed text-slate-500">
          对话提示词里出现的“能力要求 / 新处理规则”可直接沉淀为技能草稿：AI 生成（失败自动回退模板）→
          自动进入权限 / 合规 / 兼容性评审；有审核兜底，不满意可编辑或删除。
        </p>
        <label className="mb-2.5 block">
          <span className="mb-1 block text-[11px] text-slate-400">技能名称（可选）</span>
          <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} placeholder="留空自动命名" />
        </label>
        <label className="mb-2.5 block">
          <span className="mb-1 block text-[11px] text-slate-400">对话中的要求 / 能力描述 *</span>
          <textarea className={`${INPUT} font-mono`} rows={5} value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="例如：当用户提到『查天气』时先调用 weather_api，超时 5s 内重试一次并返回结构化结果" />
        </label>
        {err && <p className="mt-1 text-[11px] text-red-400">{err}</p>}
        {okMsg && <p className="mt-1 rounded-md border border-emerald-800/60 bg-emerald-950/30 px-2 py-1.5 text-[11px] text-emerald-300">{okMsg}</p>}
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {okMsg ? (
            <button type="button"
              className="inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-3 py-1.5 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
              onClick={() => { onDone?.(createdId); onClose() }}>
              <ShieldCheck size={12} /> 完成
            </button>
          ) : (
            <>
              <button type="button"
                className="inline-flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1.5 text-[11px] text-slate-300 hover:bg-slate-800"
                onClick={onClose}>取消</button>
              <button type="button"
                className="inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-3 py-1.5 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
                disabled={busy || !intent.trim()}
                onClick={() => void submit()}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />} 生成并评审
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
