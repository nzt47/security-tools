/**
 * GenerateRequirementModal —— 把“对话提示词中的能力要求”自动写成技能
 * ------------------------------------------------------------------
 * 会话页快捷入口与技能中心共用：粘贴要求 → create/ai 生成草稿（失败自动
 * 回退模板）→ 服务端自动执行评审-评估（权限/合规/兼容性），有审核兜底。
 * 成功后可到顶层导航「技能中心 → LLM 技能」查看报告、编辑并发布。
 */
import { useState } from 'react'
import { Loader2, Lightbulb, ShieldCheck } from 'lucide-react'
import { Button, hubPost, Input, ModalBase, Notice, Textarea } from '../../hub/components/ui'

import {
  SKILLS_MGMT_CREATE_AI,
} from '@/api/endpoints';

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
      const r = await hubPost<{ skill?: { id?: string; name?: string } }>(SKILLS_MGMT_CREATE_AI, {
        name: name.trim() || `auto-req-${Date.now() % 1000000}`,
        intent: intent.trim(),
        tags: ['auto', 'requirement'],
        category: 'custom',
      })
      const s = r?.skill
      setCreatedId(s?.id)
      setOkMsg(
        s
          ? `已生成并自动评审-评估：「${s.name}」（${s.id}）——请到「技能中心 → LLM 技能」查看报告，通过后发布即成为自身能力。`
          : '已生成（未返回详情）。',
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    // 【2026-11-18 封装收口】外壳交给 ui/ModalBase（portal + 遮罩 + Esc 捕获 + 滚动锁定 + × 关闭）
    <ModalBase
      open
      onClose={onClose}
      title="从对话要求生成技能"
      header={<span className="flex items-center gap-1.5"><Lightbulb size={15} className="text-primary" /> 从对话要求生成技能（自动评审-评估）</span>}
      width="max-w-xl"
      scrollable
      closeButton
    >
        <p className="mb-3 text-caption leading-relaxed text-slate-500">
          对话提示词里出现的“能力要求 / 新处理规则”可直接沉淀为技能草稿：AI 生成（失败自动回退模板）→
          自动进入权限 / 合规 / 兼容性评审；有审核兜底，不满意可编辑或删除。
        </p>
        <label className="mb-2.5 block">
          <span className="mb-1 block text-caption text-slate-400">技能名称（可选）</span>
          <Input size="sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="留空自动命名" />
        </label>
        <label className="mb-2.5 block">
          <span className="mb-1 block text-caption text-slate-400">对话中的要求 / 能力描述 *</span>
          <Textarea size="sm" className="font-mono" rows={5} value={intent}
            onChange={(e) => setIntent(e.target.value)}
            placeholder="例如：当用户提到『查天气』时先调用 weather_api，超时 5s 内重试一次并返回结构化结果" />
        </label>
        {err && <Notice tone="danger" size="sm" className="mt-1">{err}</Notice>}
        {okMsg && <Notice tone="success" size="sm" className="mt-1">{okMsg}</Notice>}
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          {okMsg ? (
            <Button type="button" onClick={() => { onDone?.(createdId); onClose() }} variant="accent" size="xs">
              <ShieldCheck size={12} /> 完成
            </Button>
          ) : (
            <>
              <Button type="button" onClick={onClose} variant="default" size="xs">取消</Button>
              <Button type="button" disabled={busy || !intent.trim()} onClick={() => void submit()} variant="accent" size="xs">
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Lightbulb size={12} />} 生成并评审
              </Button>
            </>
          )}
        </div>
    </ModalBase>
  )
}
