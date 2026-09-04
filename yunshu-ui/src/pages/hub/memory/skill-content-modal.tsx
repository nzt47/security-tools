/**
 * SkillContentModal —— 「查看技能具体内容」弹层
 * ------------------------------------------------------------------
 * 技能中心两处共用：
 *   - LLM 技能行（MemorySkillsTable）→ GET /api/skills/content?id=<id>
 *   - 技能资产库行（SkillDigestManager）→ 同上（资产 id 命中资产正文）
 * 后端逐级解析正文来源（资产库正文 → skills_repo/skill.md → 扩展/内置注册
 * → 仅元数据兜底），本组件只负责展示；无正文时如实提示「没有指令正文」。
 */
import { useEffect, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { hubGet } from '../components/ui'

interface ContentSkillRow {
  id: string
  name?: string
  description?: string
  enabled?: boolean
}

interface ContentSkill extends ContentSkillRow {
  source?: string
  content?: string
  content_type?: string
  params?: Record<string, unknown>
  extra?: Record<string, unknown>
}

const SOURCE_LABEL: Record<string, string> = {
  'asset-library': '技能资产库正文',
  'skills-repo': '技能仓库 skills_repo/skill.md',
  extension: '扩展注册表',
  'builtin-registry': '内置注册表',
  'runtime-only': '仅运行时元数据',
}

export default function SkillContentModal({ skill, onClose }: { skill: ContentSkillRow; onClose: () => void }) {
  const [data, setData] = useState<ContentSkill | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    hubGet<{ skill?: ContentSkill }>(`/api/skills/content?id=${encodeURIComponent(skill.id)}`)
      .then((r) => { if (alive) setData(r.skill ?? null) })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [skill.id])

  const title = data?.name || skill.name || skill.id
  const rawParams = data?.params && typeof data.params === 'object' && Object.keys(data.params).length > 0
    ? data.params
    : (data?.extra && typeof data.extra.params === 'object' && data.extra.params !== null
      ? (data.extra.params as Record<string, unknown>) : null)
  const params = rawParams && typeof rawParams === 'object' && Object.keys(rawParams).length > 0 ? rawParams : null
  const tags = Array.isArray(data?.extra?.tags) ? (data.extra?.tags as string[]) : []
  const content = data?.content ?? ''
  const source = data?.source || 'runtime-only'
  const enabled = data ? data.enabled !== false : skill.enabled !== false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 sm:p-6" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 px-5 py-3.5">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
              <code className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">{skill.id}</code>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] ${enabled ? 'border-emerald-800/70 bg-emerald-500/10 text-emerald-400' : 'border-slate-700 bg-slate-500/10 text-slate-400'}`}>
                {enabled ? '启用' : '停用'}
              </span>
              <span className="inline-flex items-center rounded-full border border-cyan-800/60 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-300" title="正文解析来源">
                {SOURCE_LABEL[source] ?? source}
              </span>
              {data?.content_type && (
                <span className="inline-flex items-center rounded-full border border-indigo-800/60 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">{data.content_type}</span>
              )}
              {tags.map((t) => (
                <span key={String(t)} className="inline-flex items-center rounded-full border border-indigo-800/50 bg-indigo-500/10 px-1.5 py-0.5 text-[10px] text-indigo-300">#{String(t)}</span>
              ))}
            </div>
          </div>
          <button type="button" onClick={onClose} className="shrink-0 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200" title="关闭">
            <X size={16} />
          </button>
        </div>

        {/* 主体 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex items-center gap-2 py-8 text-xs text-slate-500">
              <Loader2 size={14} className="animate-spin" /> 加载技能内容…
            </div>
          ) : error ? (
            <p className="rounded-md border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">{error}</p>
          ) : (
            <div className="space-y-3">
              {data?.description && <p className="text-xs leading-relaxed text-slate-400">{data.description}</p>}

              {content.trim() ? (
                <div>
                  <div className="mb-1 text-[11px] font-medium text-slate-500">技能内容（注入正文 / 指令）</div>
                  <pre className="max-h-[24rem] overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-800 bg-slate-950/70 p-3 font-mono text-[11px] leading-relaxed text-slate-300">{content}</pre>
                </div>
              ) : (
                <div className="rounded-md border border-dashed border-amber-800/60 bg-amber-950/20 px-3 py-2.5 text-[11px] leading-relaxed text-amber-200/90">
                  该技能没有可查看的指令正文（仅运行时元数据）。可能是启停占位 / 测试残留行——
                  缺中文说明的可在上方「+ 补中文说明」补写，确认无用的请删除。
                </div>
              )}

              {params && (
                <div>
                  <div className="mb-1 text-[11px] font-medium text-slate-500">参数（default_params / params）</div>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-800 bg-slate-950/70 p-3 font-mono text-[11px] text-slate-300">{JSON.stringify(params, null, 2)}</pre>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end border-t border-slate-800 px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-md border border-slate-700 px-4 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200">
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
