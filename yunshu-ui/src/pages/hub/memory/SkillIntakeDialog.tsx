/**
 * 入轨助手（2026-10-31 首版 / 2026-11-01 加自动化）
 * ------------------------------------------------------------------
 * 技能文件写进 data/skills_repo 只是"本机可用"；要进仓库还得 git add + 干净 worktree
 * 重派生能力清单 + 刷新能力面基线 —— 这些原本只存在于命令行，UI 里没有任何入口，
 * 结果是"本机 47 条 / CI 44 条"的漂移（§11.32 C · N5 实测）。
 *
 * 2026-11-01 起，一键入轨升级成**一条龙**（后端 scripts/intake_skill.py）：
 *   ① 自动修/自动补 front matter（只补可确定性推导的字段：version/status/category/
 *      author/tags/content_type/enabled/id，中文 description 照抄成 description_zh，
 *      清掉 name 的「（自动整理）」后缀；**不翻译、不编造「何时使用」**）；
 *   ② 校验 → git add（只该技能目录）→ 干净 worktree 重派生清单/盘点表 → 刷新基线；
 *   ③ 可选「入轨并建 PR」：commit → push 分支 → gh pr create（要求当前在 master，
 *      且改动全部落在白名单内，否则后端直接拒绝，绝不会顺带提交别的草稿）。
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, GitBranch, Loader2, X } from 'lucide-react'
import { authHeader } from '../../../lib/apiToken'
import { SKILLS_MGMT } from '../../../api/endpoints'

type IntakeItem = {
  skill_id: string
  tracked: boolean
  errors: string[]
  warnings: string[]
  fixable?: string[]
  needs_human?: string[]
}

type Props = { open: boolean; onClose: () => void }

type IntakeMode = 'workspace' | 'pr'

export default function SkillIntakeDialog({ open, onClose }: Props) {
  const [items, setItems] = useState<IntakeItem[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [autoFix, setAutoFix] = useState(true)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ id: string; text: string; prUrl?: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`${SKILLS_MGMT}/intake/status`, { headers: authHeader() })
      const j = await r.json()
      if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`)
      setItems(Array.isArray(j.items) ? j.items : [])
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  if (!open) return null

  const pending = items.filter((i) => !i.tracked)
  const tracked = items.length - pending.length

  const runIntake = async (id: string, mode: IntakeMode) => {
    setBusy(id)
    setError('')
    setDone(null)
    try {
      const r = await fetch(`${SKILLS_MGMT}/${encodeURIComponent(id)}/intake`, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          execute: true,
          fix_baselines: true,
          fix_meta: autoFix,
          open_pr: mode === 'pr',
        }),
      })
      const j = await r.json()
      if (!r.ok || j.ok === false) {
        const detail = j.error || (Array.isArray(j.errors) ? j.errors.join('；') : '')
        setError(detail || `入轨失败：HTTP ${r.status}`)
        return
      }
      const changed = (j.derived_changed || []).join('、') || '（无变化）'
      const base = Array.isArray(j.baseline_changes) ? j.baseline_changes.length : 0
      const fixed = (j.meta_fixed || []).join('、')
      const prUrl: string | undefined = j.pr && j.pr.ok ? j.pr.pr_url : undefined
      const parts = [
        fixed ? `自动补全：${fixed}` : '',
        `已 git add 并重派生：${changed}；基线更新 ${base} 处`,
        prUrl ? '已提交并开 PR' : '未提交（工作区已就绪，review 后再提交）',
      ].filter(Boolean)
      setDone({ id, text: parts.join('；'), prUrl })
      await load()
    } catch (e) {
      setError(String((e as Error).message || e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <GitBranch size={14} className="text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">入轨助手</h2>
            <span className="text-[11px] text-slate-500">把技能从「只在本机」变成「在仓库里」</span>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={14} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[11px] leading-relaxed text-slate-400">
          <div className="mb-3 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2">
            <p className="font-medium text-slate-200">为什么要入轨？</p>
            <p className="mt-1">
              技能文件（<code className="text-cyan-300">data/skills_repo/&lt;id&gt;/skill.md</code>）默认只写在本机。
              不入轨：换机器 / 重建即丢，CI 也永远校验不到它。
              入轨后：进 git（可 review、可回滚）、进 CI 形状门禁、进能力清单（能力面 +1）。
            </p>
            <p className="mt-2 font-medium text-slate-200">一键入轨现在做到哪一步？</p>
            <p className="mt-1">
              校验 → <strong className="text-slate-200">自动补全元数据</strong> → git add（只该技能目录）→
              干净 worktree 重派生清单与盘点表 → 刷新能力面基线。勾选「入轨后直接建 PR」则继续
              commit → push 分支 → 开 PR；不勾就停在工作区，等你 review。
            </p>
            <p className="mt-2 text-slate-500">
              自动补全只补<strong className="text-slate-300">能确定性推导</strong>的字段（version/status/
              category/author/tags/content_type/description_zh），<strong className="text-amber-300">不翻译、不编造
              </strong>「何时使用」—— 缺的会列在「需人补」里。
            </p>
            <p className="mt-2 text-slate-500">
              只留主轨、不该建文件轨的技能：<code className="text-amber-300">global-core-principles</code>（常驻准则）、
              <code className="text-amber-300">skill</code>（易之三义，注入型指令）—— 服务端会直接拒绝。
            </p>
          </div>

          {error ? (
            <div className="mb-2 flex items-start gap-2 rounded-md border border-red-900/60 bg-red-950/30 px-2.5 py-2 text-red-200">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          {done ? (
            <div className="mb-2 flex items-start gap-2 rounded-md border border-emerald-900/60 bg-emerald-950/30 px-2.5 py-2 text-emerald-200">
              <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
              <span>
                <strong>{done.id}</strong>：{done.text}
                {done.prUrl ? (
                  <a href={done.prUrl} target="_blank" rel="noreferrer" className="ml-1 text-cyan-300 underline">
                    查看 PR
                  </a>
                ) : null}
              </span>
            </div>
          ) : null}

          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-slate-300">
              未入轨 <strong className="text-amber-300">{pending.length}</strong> 条 · 已入轨 {tracked} 条
            </span>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={autoFix} onChange={(e) => setAutoFix(e.target.checked)} />
                自动补全元数据
              </label>
              <button
                type="button"
                onClick={() => void load()}
                className="rounded-md border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800"
              >
                刷新
              </button>
            </div>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 py-6 text-slate-500">
              <Loader2 size={12} className="animate-spin" /> 读取中…
            </div>
          ) : pending.length === 0 ? (
            <div className="py-6 text-center text-slate-500">
              所有文件轨技能都已入轨 👍（新增技能后这里会出现「一键入轨」）
            </div>
          ) : (
            <ul className="divide-y divide-slate-800 rounded-lg border border-slate-800">
              {pending.map((it) => (
                <li key={it.skill_id} className="flex items-start gap-2 px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-slate-200">{it.skill_id}</div>
                    {it.errors.length > 0 ? (
                      <div className="mt-0.5 text-red-300">先修：{it.errors.join('；')}</div>
                    ) : null}
                    {(it.fixable || []).length > 0 ? (
                      <div className="mt-0.5 text-emerald-300/80">可自动补全：{(it.fixable || []).join('、')}</div>
                    ) : null}
                    {(it.needs_human || []).length > 0 ? (
                      <div className="mt-0.5 text-amber-300/80">需人补：{(it.needs_human || []).join('；')}</div>
                    ) : null}
                    {it.warnings.length > 0 ? (
                      <div className="mt-0.5 text-amber-300/80">{it.warnings.length} 条形状告警（不阻断）</div>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 flex-col gap-1">
                    <button
                      type="button"
                      disabled={busy !== null || it.errors.length > 0}
                      onClick={() => void runIntake(it.skill_id, 'workspace')}
                      className="rounded-md border border-cyan-700/70 px-2.5 py-1 text-[10px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:opacity-40"
                    >
                      {busy === it.skill_id ? '入轨中…' : '一键入轨'}
                    </button>
                    <button
                      type="button"
                      disabled={busy !== null || it.errors.length > 0}
                      onClick={() => void runIntake(it.skill_id, 'pr')}
                      className="rounded-md border border-emerald-700/70 px-2.5 py-1 text-[10px] text-emerald-300 transition-colors hover:bg-emerald-500/10 disabled:opacity-40"
                      title="commit → push 分支 → 开 PR（要求当前在 master，且改动全在白名单内）"
                    >
                      {busy === it.skill_id ? '处理中…' : '入轨并建 PR'}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
