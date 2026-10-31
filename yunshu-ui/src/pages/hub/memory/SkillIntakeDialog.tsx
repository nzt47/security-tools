/**
 * 入轨助手（2026-10-31）
 * ------------------------------------------------------------------
 * 技能文件写进 data/skills_repo 只是"本机可用"；要进仓库还得 git add + 干净 worktree
 * 重派生能力清单 + 刷新能力面基线 —— 这些原本只存在于命令行，UI 里没有任何入口，
 * 结果是"本机 47 条 / CI 44 条"的漂移（§11.32 C · N5 实测）。
 *
 * 本对话框做三件事：
 * ① 讲清**为什么**要入轨（不入轨 = 换机器就没了、CI 看不见）；
 * ② 列出「未入轨」的技能（GET /api/skills-mgmt/intake/status）；
 * ③ 一键入轨（POST /api/skills-mgmt/<id>/intake）—— **不 commit / 不 push / 不建 PR**，
 *    只把工作区与派生物准备好，最后一步仍由人 review 后提交。
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
}

type Props = { open: boolean; onClose: () => void }

export default function SkillIntakeDialog({ open, onClose }: Props) {
  const [items, setItems] = useState<IntakeItem[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ id: string; text: string } | null>(null)

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

  const runIntake = async (id: string) => {
    setBusy(id)
    setError('')
    setDone(null)
    try {
      const r = await fetch(`${SKILLS_MGMT}/${encodeURIComponent(id)}/intake`, {
        method: 'POST',
        headers: { ...authHeader(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ execute: true, fix_baselines: true }),
      })
      const j = await r.json()
      if (!r.ok || j.ok === false) {
        const detail = j.error || (Array.isArray(j.errors) ? j.errors.join('；') : '')
        setError(detail || `入轨失败：HTTP ${r.status}`)
        return
      }
      const changed = (j.derived_changed || []).join('、') || '（无变化）'
      const base = Array.isArray(j.baseline_changes) ? j.baseline_changes.length : 0
      setDone({
        id,
        text: `已 git add 并重派生：${changed}；能力面基线更新 ${base} 处。最后一步请 review 后 commit/PR。`,
      })
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
            <p className="mt-2 font-medium text-slate-200">以后有新技能怎么办？</p>
            <p className="mt-1">
              吞噬 / 导入 / 手写生成 skill.md 之后：先补齐元数据（author / status / description_zh /「何时使用」），
              再点下面的「一键入轨」。按钮会做 校验 → git add → 干净 worktree 重派生清单与盘点表 → 刷新基线；
              <strong className="text-slate-200">它不会替你 commit / push / 建 PR</strong>，那一步仍需人 review。
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
              </span>
            </div>
          ) : null}

          <div className="mb-1 flex items-center justify-between">
            <span className="text-slate-300">
              未入轨 <strong className="text-amber-300">{pending.length}</strong> 条 · 已入轨 {tracked} 条
            </span>
            <button
              type="button"
              onClick={() => void load()}
              className="rounded-md border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800"
            >
              刷新
            </button>
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
                      <div className="mt-0.5 text-red-300">
                        先修：{it.errors.join('；')}
                      </div>
                    ) : null}
                    {it.warnings.length > 0 ? (
                      <div className="mt-0.5 text-amber-300/80">{it.warnings.length} 条形状告警（不阻断）</div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    disabled={busy !== null || it.errors.length > 0}
                    onClick={() => void runIntake(it.skill_id)}
                    className="shrink-0 rounded-md border border-cyan-700/70 px-2.5 py-1 text-[10px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:opacity-40"
                  >
                    {busy === it.skill_id ? '入轨中…' : '一键入轨'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
