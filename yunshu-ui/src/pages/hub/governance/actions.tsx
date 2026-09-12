/**
 * 七动作 UI 接线 + 永不自动化五类确认 UI（v7.2 §7 / §5.7 机制 5）
 * ------------------------------------------------------------------
 * 【红线】
 *   ① **写动作一律走既有审批**：本组件只调 `POST /api/cp/actions/*`，
 *      由后端做 §7.0 矩阵鉴权 + 审批链路；前端**不拥有额外权限**。
 *   ② **永不自动化五类**（发布/转账/删库/改权限/push --force）：
 *      - 类别词表取自 `GET /api/cp/security/render-state`（★ U1：不得自定义）；
 *      - 显式确认绑定**单次 action + 60s 时效**（倒计时到点即失效，需重新确认）；
 *      - 不接受任何文本形式的"已批准"（`accepts_text_approval === false`）；
 *      - 即便确认通过，后端**也不执行**该动作（只转审批提案）——
 *        UI 必须如实显示"已提交审批，未执行"。
 *   ③ **熔断/回滚走审批**：回滚只接受整包 `bundle_hash`；
 *      L5 `requires_approval` 由后端返回，前端据此显式标注。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Ban, CircleAlert, FileDiff, Loader2, RotateCcw, ShieldCheck, Snowflake,
  Trash2, Zap,
} from 'lucide-react'
import { issueConfirmation, runAction } from '@/lib/cpPanelsApi'
import type { ActionName, ActionResponse } from '@/lib/cpPanelsTypes'
import { ApprovalZone, StatusBadge } from './components'
import { useSecurityState } from './usePanel'

/** 七动作（§7 逐字：熔断/回滚/降级/摘除/审批/溯源 Diff/熔炉开关） */
const SEVEN_ACTIONS: Array<{
  action: ActionName
  label: string
  hint: string
  icon: React.ReactNode
}> = [
  { action: 'circuit_break', label: '熔断', hint: '限流/断流该能力（走矩阵鉴权）', icon: <Zap size={12} /> },
  { action: 'rollback', label: '回滚', hint: '整包回滚（唯一原子单位；L5 强制审批）', icon: <RotateCcw size={12} /> },
  { action: 'degrade', label: '降级', hint: 'native→borrowed 走回上游，不改代码', icon: <Snowflake size={12} /> },
  { action: 'remove_source', label: '摘除来源', hint: '先算级联；reason 必填', icon: <Trash2 size={12} /> },
  { action: 'trace_diff', label: '溯源 Diff', hint: '只读：呈现 stage 差异与依据', icon: <FileDiff size={12} /> },
  { action: 'switch_forge', label: '熔炉开关', hint: '策略/熔炉切换；需二次认证', icon: <ShieldCheck size={12} /> },
]

/** 永不自动化五类的可判定文本模板（提示用；**类别词表仍以后端为准**） */
const NEVER_TEXT_TEMPLATE: Record<string, string> = {
  'force-push': 'git push --force origin main',
  'drop-database': 'drop database prod',
  'permission-change': 'chmod 777 /etc/shadow',
  transfer: 'transfer funds 1000 usd to account',
  publish: 'deploy to production',
}

export interface ActionsBarProps {
  /** 当前作用对象（能力 id / bundle_hash / 记录 id） */
  target: string
  /** 目标类型提示（仅用于文案，不作判定） */
  targetLabel?: string
  onDone?: (resp: ActionResponse) => void
}

/**
 * 七动作工具条 + 五类确认 UI
 *
 * ★ 常量驱动：`never_automated` / `labels` / `max_ttl_seconds` 全部来自
 *   `useSecurityState()`；常量未就绪时**拒绝渲染五类按钮**（U1）。
 */
export function AbsoluteActionBar({ target, targetLabel, onDone }: ActionsBarProps) {
  const { state: sec, loading: secLoading } = useSecurityState()
  const [reason, setReason] = useState('')
  const [risk, setRisk] = useState('')
  const [secondFactor, setSecondFactor] = useState('')
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState<ActionResponse | null>(null)
  const [error, setError] = useState('')
  const [neverOpen, setNeverOpen] = useState(false)

  const run = useCallback(
    async (action: ActionName, body: Record<string, unknown> = {}) => {
      setBusy(action); setError(''); setResult(null)
      try {
        const resp = await runAction(action, { target, reason, risk, second_factor: secondFactor, ...body })
        setResult(resp)
        onDone?.(resp)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy('')
      }
    },
    [target, reason, risk, secondFactor, onDone],
  )

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <CircleAlert size={13} />
        <span>七动作（写动作一律走后端 §7.0 矩阵 + 既有审批，前端无额外权限）</span>
        {targetLabel && <span className="text-slate-500">作用于 {targetLabel}：</span>}
        <span className="font-mono text-cyan-300">{target || '（未选择目标）'}</span>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="reason（强制推进/摘除来源必填）"
          className="w-56 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600" />
        <select value={risk} onChange={(e) => setRisk(e.target.value)}
          className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          <option value="">风险未标注</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="destructive">destructive（需二次认证）</option>
        </select>
        <input value={secondFactor} onChange={(e) => setSecondFactor(e.target.value)}
          placeholder="二次认证码（destructive 必填）"
          className="w-44 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600" />
      </div>

      {/* ★ U2：七动作按钮区同样在 Shadow DOM 自治单元内（固定 z-index） */}
      <ApprovalZone inline>
        <div className="flex flex-wrap items-center gap-2">
          {SEVEN_ACTIONS.map((a) => (
            <button
              key={a.action}
              type="button"
              title={a.hint}
              disabled={busy !== '' || !target}
              data-primary={a.action === 'rollback' ? 'true' : undefined}
              onClick={() => run(a.action)}
            >
              {busy === a.action ? <Loader2 size={11} className="inline animate-spin" /> : a.icon}
              <span className="ml-1">{a.label}</span>
            </button>
          ))}
          <button
            type="button"
            onClick={() => setNeverOpen((v) => !v)}
            disabled={secLoading}
            title="永不自动化五类（§7）：需 UI 显式确认，单次 action + 60s"
          >
            <Ban size={11} />
            <span className="ml-1">永不自动化五类</span>
          </button>
        </div>
      </ApprovalZone>

      {neverOpen && sec && (
        <NeverAutomatedPanel
          target={target}
          labels={sec.boundary_words.labels}
          categories={sec.boundary_words.never_automated}
          maxTtl={sec.boundary_words.max_ttl_seconds}
          acceptsTextApproval={sec.boundary_words.accepts_text_approval}
          onResult={(r) => { setResult(r); onDone?.(r) }}
        />
      )}
      {neverOpen && !sec && (
        <div className="mt-2 text-[11px] text-amber-400">
          安全常量未就绪（U1）：未拿到 `never_automated` / `60s` 常量前，本 UI 不渲染五类按钮
        </div>
      )}

      {error && (
        <div className="mt-2 rounded border border-red-900/60 bg-red-950/30 px-2 py-1 text-[11px] text-red-300">
          {error}
        </div>
      )}
      {result && <ActionResultBox result={result} />}
    </div>
  )
}

function ActionResultBox({ result }: { result: ActionResponse }) {
  return (
    <div className="mt-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px] text-slate-300">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={result.ok === false ? 'red' : 'green'}>
          {result.ok === false ? (result.code || '被拒') : '已受理'}
        </StatusBadge>
        {result.level && <StatusBadge tone="red">{result.level}</StatusBadge>}
        {result.requires_approval && <StatusBadge tone="red">强制审批</StatusBadge>}
        {result.read_only && <StatusBadge tone="blue">只读</StatusBadge>}
        {result.executed === false && <StatusBadge tone="yellow">未执行</StatusBadge>}
        {result.submitted_for_approval && <StatusBadge tone="blue">已转审批提案</StatusBadge>}
        {result.dry_run && <StatusBadge tone="gray">dry-run</StatusBadge>}
      </div>
      {result.message && <div className="mt-1 text-red-300">{result.message}</div>}
      {result.decision?.reason && <div className="mt-1 text-slate-400">判定：{result.decision.reason}</div>}
      {result.executor && <div className="mt-1 font-mono text-[10px] text-slate-500">执行入口：{result.executor}</div>}
      {result.note && <div className="mt-1 text-[10px] text-amber-400/80">{result.note}</div>}
      {result.plan && (
        <div className="mt-1 font-mono text-[10px] text-slate-500">
          计划：{String((result.plan as { bundle_id?: string }).bundle_id)} ·
          组件 {(result.plan as { component_count?: number }).component_count} 个（整包）
        </div>
      )}
      {result.stage_events && result.stage_events.length > 0 && (
        <div className="mt-1 space-y-0.5">
          {result.stage_events.slice(0, 5).map((e, i) => (
            <div key={i} className="font-mono text-[10px] text-slate-500">
              {String(e.ts)} {String(e.from_stage ?? '—')} → {String(e.to_stage ?? '—')}（{String(e.verdict ?? '')}）
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  永不自动化五类确认 UI（单次 action + 60s 时效）
// ═══════════════════════════════════════════════════════════

function NeverAutomatedPanel({
  target, labels, categories, maxTtl, acceptsTextApproval, onResult,
}: {
  target: string
  labels: Record<string, string>
  categories: string[]
  maxTtl: number
  acceptsTextApproval: false | boolean
  onResult: (r: ActionResponse) => void
}) {
  const [category, setCategory] = useState(categories[0] || '')
  const [actionText, setActionText] = useState('')
  const [token, setToken] = useState('')
  const [expiresAt, setExpiresAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const timer = useRef<number | null>(null)

  // 倒计时（到点即失效；**不得延长** —— 后端 60s 硬上限）
  useEffect(() => {
    timer.current = window.setInterval(() => setNow(Date.now()), 250)
    return () => { if (timer.current) window.clearInterval(timer.current) }
  }, [])

  const remaining = useMemo(
    () => (expiresAt ? Math.max(0, (expiresAt - now) / 1000) : 0),
    [expiresAt, now],
  )
  const expired = expiresAt > 0 && remaining <= 0

  async function doIssue() {
    setBusy(true); setMsg('')
    try {
      const text = actionText || NEVER_TEXT_TEMPLATE[category] || category
      const out = await issueConfirmation(category, { target, action_text: text })
      setToken(out.confirmation.token)
      setExpiresAt(out.confirmation.expires_at * 1000)
      setMsg(`已签发单次确认凭据（${out.confirmation.ttl_seconds}s 内有效，单次）；令牌只在此刻返回一次`)
    } catch (e) {
      setMsg(`签发失败：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }

  async function doExecute() {
    setBusy(true); setMsg('')
    try {
      const resp = await runAction(category, {
        target,
        action_text: actionText || NEVER_TEXT_TEMPLATE[category] || category,
        confirmation_token: token,
      })
      onResult(resp)
      setToken(''); setExpiresAt(0)
      setMsg(resp.executed === false
        ? '已提交审批提案（§7：五类不可从 UI 直接执行）'
        : '已受理')
    } catch (e) {
      setMsg(`被拒：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }

  return (
    <div className="mt-2 rounded-lg border border-red-900/60 bg-red-950/20 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-red-300">
        <Ban size={13} />
        <span>
          永不自动化五类（§7 逐字：发布 / 转账 / 删库 / 改权限 / push --force）
          —— 绑定单次 action + {maxTtl}s 时效，**不接受文本形式的「已批准」**
          （accepts_text_approval={String(acceptsTextApproval)}）
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select value={category} onChange={(e) => { setCategory(e.target.value); setToken(''); setExpiresAt(0) }}
          className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
          {categories.map((c) => (
            <option key={c} value={c}>{labels[c] || c}（{c}）</option>
          ))}
        </select>
        <input value={actionText} onChange={(e) => setActionText(e.target.value)}
          placeholder={`可判定文本（缺省示例：${NEVER_TEXT_TEMPLATE[category] || '—'}）`}
          className="w-72 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600" />
        <button type="button" onClick={doIssue} disabled={busy}
          className="rounded border border-slate-600 px-2 py-1 text-[11px] text-slate-200 disabled:opacity-40">
          {busy ? '处理中…' : '1) 显式确认（签发单次凭据）'}
        </button>
        <button type="button" onClick={doExecute} disabled={busy || !token || expired}
          className="rounded bg-red-800 px-2 py-1 text-[11px] text-white disabled:opacity-40">
          2) 提交（转审批提案）
        </button>
        {expiresAt > 0 && (
          <StatusBadge tone={expired ? 'red' : 'yellow'}>
            {expired ? '凭据已失效（需重新确认）' : `剩余 ${remaining.toFixed(1)}s`}
          </StatusBadge>
        )}
      </div>
      {msg && <div className="mt-2 text-[11px] text-cyan-300">{msg}</div>}
    </div>
  )
}
