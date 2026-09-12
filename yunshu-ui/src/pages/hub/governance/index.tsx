/**
 * 治理可观测六面板（v7.2 §7）—— 云枢工作台内的面板容器
 * ------------------------------------------------------------------
 * ★ UI 五坑④：**不建独立 Web App**。本页就是工作台的一个导航栏目
 *   （`工作台 → 治理面板`），由 `hubNav.tsx` 懒加载，`ContentPanel` 渲染。
 *
 * 面板优先级（§7 逐字）：
 *   P0 展开：消化流水线 / 审批收件箱
 *   P1 折叠：能力地图 / 成本 ROI / 自愈事故（**有事故自动展开**）
 *   P2 折叠：记忆技能库
 *
 * 口径纪律（§0.3）：所有数字走 `MetricValue`（带来源/公式/样本量入口）；
 *   缺位渲染"—"不渲染 0；样本 <20 带"仅披露"角标。
 */

import { useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, ChevronDown, ChevronRight, FileDown,
  GitBranch, Layers, Loader2, RefreshCw, ScrollText, ShieldAlert,
  Siren, TrendingUp, Users,
} from 'lucide-react'
import {
  batchDecide,
  batchLink,
  downloadAuditCsv,
  fetchApprovalInbox,
  fetchAuditExport,
  fetchCapabilityMap,
  fetchIncidents,
  fetchMemorySkills,
  fetchPipeline,
  fetchRoi,
} from '@/lib/cpPanelsApi'
import type {
  ApprovalInboxView,
  ApprovalItem,
  AuditExportView,
  AuthzAlertsView,
  CapabilityMapView,
  DecisionCard,
  IncidentsView,
  MemorySkillsView,
  ObservabilityStreamView,
  PipelineView,
  RoiView,
  SecurityRenderState,
  ShadowCard,
  SloMetricRow,
} from '@/lib/cpPanelsTypes'
import {
  AbsentBox,
  ApprovalZone,
  IncidentBanner,
  MetricValue,
  PanelHeader,
  ReasonChain,
  StatusBadge,
  TaintBadge,
  toneForRisk,
  toneForSeverity,
  toneForStage,
  VirtualList,
} from './components'
import {
  formatMs,
  formatNumber,
  formatPercent,
  formatTime,
  usePanel,
  useSecurityState,
} from './usePanel'
import { AbsoluteActionBar } from './actions'
import { ImplicitEntryLog, recordImplicitEntry } from './implicitEntry'

// ═══════════════════════════════════════════════════════════
//  容器：按 panel 渲染（P0 展开 / P1·P2 折叠 / 自愈自动展开）
// ═══════════════════════════════════════════════════════════

export type GovernancePanelId =
  | 'pipeline' | 'capabilities' | 'approvals'
  | 'roi' | 'incidents' | 'memory' | 'audit'

/** 折叠区（P1/P2 默认收起；P0 默认展开） */
function Collapsible({
  title, subtitle, defaultOpen = false, icon, children,
}: {
  title: string
  subtitle?: string
  defaultOpen?: boolean
  icon?: React.ReactNode
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {icon}
        <span className="text-sm font-medium text-slate-200">{title}</span>
        {subtitle && <span className="text-xs text-slate-500">{subtitle}</span>}
      </button>
      {open && <div className="border-t border-slate-800 p-4">{children}</div>}
    </div>
  )
}

function PanelShell({
  loading, error, reload, children, onRefresh,
}: {
  loading: boolean
  error: string
  reload: () => void
  children: React.ReactNode
  onRefresh?: () => void
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="mb-2 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onRefresh || reload}
          className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-400 hover:text-cyan-300"
        >
          <RefreshCw size={11} /> 刷新
        </button>
      </div>
      {loading && (
        <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin" /> 加载中…
        </div>
      )}
      {!loading && error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          <AlertTriangle size={15} className="mt-0.5" />
          <span className="break-all">{error}</span>
        </div>
      )}
      {!loading && !error && children}
    </div>
  )
}

export function GovernancePanels({ panel = 'pipeline' }: { panel?: GovernancePanelId }) {
  switch (panel) {
    case 'approvals':
      return <ApprovalInboxPanel />
    case 'capabilities':
      return <CapabilityMapPanel />
    case 'roi':
      return <RoiPanel />
    case 'incidents':
      return <IncidentsPanel />
    case 'memory':
      return <MemorySkillsPanel />
    case 'audit':
      return <AuditExportPanel />
    case 'pipeline':
    default:
      return <DigestionPipelinePanel />
  }
}


// ═══════════════════════════════════════════════════════════
//  1. 消化流水线（P0 展开）— 泳道五列
// ═══════════════════════════════════════════════════════════

export function DigestionPipelinePanel() {
  const { data, loading, error, reload } = usePanel<PipelineView>(
    () => fetchPipeline({ days: 7, limit: 100 }),
    [],
  )
  if (data && !data.ok) {
    return <PanelShell loading={false} error="流水线数据不可用" reload={reload}><span /></PanelShell>
  }
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="消化流水线"
            description="轨迹采集 → 模式挖掘 → Skill 生成 → 验收 → 灰度（P0 默认展开）"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
          />
          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
            <KpiTile label="stage 事件" metric={data.summary.digest_stage_events} icon={<GitBranch size={13} />} />
            <KpiTile label="涉及能力" metric={data.summary.capabilities_touched} icon={<Layers size={13} />} />
            <KpiTile label="已落库迁移" metric={data.summary.applied_migrations} />
            <KpiTile label="灰度运行" metric={data.summary.shadow_runs} />
            <KpiTile label="内化决策" metric={data.summary.internalize_decisions} />
            <KpiTile label="内化率" metric={data.summary.internalize_rate} format="percent" />
          </div>

          {/* ── 泳道五列 ── */}
          <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-5">
            {data.lanes.map((lane) => (
              <div key={lane.lane} className="flex min-h-[220px] flex-col rounded-lg border border-slate-800 bg-slate-900/60">
                <div className="border-b border-slate-800 px-3 py-2">
                  <div className="text-xs font-medium text-slate-200">{lane.title}</div>
                  <div className="mt-0.5 text-[11px] text-slate-500">
                    <MetricValue metric={lane.event_count} />
                    {lane.truncated && <span className="ml-1 text-amber-500">（已截断）</span>}
                  </div>
                </div>
                <VirtualList
                  className="max-h-[360px] flex-1 p-1.5"
                  items={lane.items}
                  itemHeight={62}
                  height={360}
                  keyOf={(e) => e.event_id}
                  renderItem={(e) => (
                    <div className="mb-1 rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5">
                      <div className="truncate font-mono text-[10px] text-cyan-300">{e.capability_id || '(无能力)'}</div>
                      <div className="mt-0.5 flex items-center gap-1">
                        <StatusBadge tone={toneForStage(e.to_stage)}>{e.to_stage || '—'}</StatusBadge>
                        {e.applied && <StatusBadge tone="green">已落库</StatusBadge>}
                      </div>
                      <div className="mt-0.5 truncate text-[10px] text-slate-500" title={e.reasons?.join('；')}>
                        {e.verdict || '—'} · {formatTime(e.ts)}
                      </div>
                    </div>
                  )}
                />
              </div>
            ))}
          </div>

          {/* ── 灰度 + 内化决策 + 原因链 ── */}
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Collapsible title="灰度运行（shadow）" subtitle={data.clock?.note} defaultOpen>
              {data.shadow.length === 0 ? (
                <AbsentBox label="灰度台账无记录" reason="data/digestion/shadow/shadow_ledger.jsonl 为空" />
              ) : (
                data.shadow.map((s) => <ShadowCardView key={`${s.capability_id}-${s.generated_at}`} card={s} />)
              )}
            </Collapsible>
            <Collapsible title="内化决策（六条件）" subtitle="判决 ≠ 合入：promote PR 由人工合入" defaultOpen>
              {data.internalize.length === 0 ? (
                <AbsentBox label="无内化决策产物" reason="promote_pr 目录为空（S3-03 无 decisions store，读 decision.json）" />
              ) : (
                data.internalize.map((d) => <DecisionCardView key={`${d.capability_id}-${d.pr_id}`} decision={d} />)
              )}
            </Collapsible>
          </div>

          <div className="mt-3">
            <Collapsible title="人工抽检队列（M5 口径）" defaultOpen={false}>
              <ManualReviewView summary={data.manual_review} />
            </Collapsible>
          </div>

          {/* 七动作接线（写动作走后端 §7.0 矩阵 + 既有审批；前端无额外权限） */}
          <div className="mt-3">
            <Collapsible title="七动作（熔断/回滚/降级/摘除/溯源 Diff/熔炉开关）" defaultOpen={false}>
              <AbsoluteActionBar
                target={data.internalize[0]?.capability_id || ''}
                targetLabel="内化决策能力"
              />
            </Collapsible>
          </div>

          {/* ★ UI 五坑③：隐式入口（右键"沉淀为 Skill"等）必留可点击记录 */}
          <div className="mt-3">
            <ImplicitEntryLog />
          </div>
        </>
      )}
    </PanelShell>
  )
}

/** 供其它页面复用：手动登记一次隐式入口触发（右键菜单/快捷键/状态灯） */
export const recordImplicitEntryForPanels = recordImplicitEntry

/** 隐式入口示例接线：右键"沉淀为 Skill"（一级 + 快捷键） */
export function DistillAsSkillEntry({ capabilityId }: { capabilityId: string }) {
  return (
    <button
      type="button"
      className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:border-cyan-700"
      onClick={() => {
        recordImplicitEntry({
          entry: 'context-menu:distill-as-skill',
          entryLabel: '右键 → 沉淀为 Skill',
          target: capabilityId,
          outcome: 'ok',
          detail: '已登记（隐式入口可点击记录，§7 五坑③）',
        })
      }}
    >
      沉淀为 Skill（隐式入口）
    </button>
  )
}

function KpiTile({
  label, metric, format = 'number', icon,
}: {
  label: string
  metric?: import('@/lib/cpPanelsTypes').Metric
  format?: 'number' | 'percent' | 'cents' | 'ms'
  icon?: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
      <div className="flex items-center gap-1 text-[11px] text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg leading-none">
        <MetricValue metric={metric} format={format} />
      </div>
    </div>
  )
}

function ShadowCardView({ card }: { card: ShadowCard }) {
  return (
    <div className="mb-2 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-cyan-300">{card.capability_id}</span>
        <StatusBadge tone={card.allowed ? 'green' : 'red'}>
          {card.allowed ? '允许运行' : '被阻断'}
        </StatusBadge>
        <StatusBadge tone={card.degradation === 'degraded' ? 'red' : card.degradation === 'stable' ? 'green' : 'gray'}>
          {card.degradation || '—'}
        </StatusBadge>
        <span className="text-[10px] text-slate-500">judge={card.judge_kind}</span>
        <span className="ml-auto text-[10px] text-slate-500">{formatTime(card.generated_at)}</span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] md:grid-cols-4">
        <div>样本 <span className="text-slate-200">{card.sampled}</span></div>
        <div>通过 <span className="text-slate-200">{formatNumber(card.passed)}</span></div>
        <div>
          p99(墙钟·候选) <MetricValue metric={card.p99_wall_candidate_ms} format="ms" />
        </div>
        <div>
          p99(墙钟·上游) <MetricValue metric={card.p99_wall_upstream_ms} format="ms" />
        </div>
      </div>
      <div className="mt-1.5 text-[11px]">
        通过率 <MetricValue metric={card.pass_rate} format="percent" />
      </div>
    </div>
  )
}

function DecisionCardView({ decision }: { decision: DecisionCard }) {
  const verdictTone =
    decision.verdict === 'promote' ? 'green'
      : decision.verdict === 'veto_blocked' ? 'red' : 'yellow'
  return (
    <div className="mb-3 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-cyan-300">{decision.capability_id}</span>
        <StatusBadge tone={verdictTone}>{decision.verdict}</StatusBadge>
        <StatusBadge tone={toneForStage(decision.stage)}>{decision.stage || '—'}</StatusBadge>
        {decision.manual_required && <StatusBadge tone="yellow">{decision.manual_label || '人工通道'}</StatusBadge>}
        {decision.promotable && <StatusBadge tone="green">可 promote</StatusBadge>}
      </div>
      {decision.blocker && (
        <div className="mt-1.5 text-[11px] text-red-300">阻断项：{decision.blocker}</div>
      )}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] md:grid-cols-4">
        <div>月省 <MetricValue metric={decision.roi.monthly_saving_cents} format="cents" /></div>
        <div>一次性投入 <MetricValue metric={decision.roi.one_time_investment_cents} format="cents" /></div>
        <div>月摊销 <MetricValue metric={decision.roi.amortized_monthly_cents} format="cents" /></div>
        <div>净月收益 <MetricValue metric={decision.roi.net_monthly_cents} format="cents" /></div>
      </div>
      {/* ★ P7.2-24 可解释性：非原始 JSON；含"来源/公式/样本量"入口 */}
      <div className="mt-2">
        <ReasonChain
          title="六条件逐项（①-④ 排序 / ⑤⑥ 一票否决）"
          formula={decision.roi.monthly_saving_cents?.formula}
          nodes={decision.conditions.map((c) => ({
            label: `${c.name}［${c.dimension === 'veto' ? '否决' : '排序'}］`,
            passed: c.passed,
            value: (
              <span className="font-mono">
                实测 {String(c.actual ?? '—')} {c.comparator || ''} 阈值 {String(c.threshold ?? '—')}
              </span>
            ),
            detail: c.reasons?.[0],
            source: c.evidence_source,
          }))}
        />
      </div>
      <div className="mt-1.5 font-mono text-[10px] text-slate-600">
        engine={decision.engine_version} · audit_seq={decision.audit_seq ?? '—'} ·
        pr={decision.pr_id || '—'}
      </div>
    </div>
  )
}

function ManualReviewView({ summary }: { summary: import('@/lib/cpPanelsTypes').ManualReviewSummary }) {
  if (summary?.available === false) {
    return <AbsentBox label="人工抽检队列不可用" reason={summary.reason} />
  }
  return (
    <div className="text-xs text-slate-300">
      <div className="flex flex-wrap items-center gap-3">
        <span>抽检 <strong>{summary.sampled ?? 0}</strong> 条</span>
        <span>待裁定 <strong className={summary.pending ? 'text-amber-300' : ''}>{summary.pending ?? 0}</strong></span>
        <span>已裁定 <strong>{summary.decided ?? 0}</strong></span>
        <StatusBadge tone={summary.closed ? 'green' : 'yellow'}>
          {summary.closed ? '抽检闭合' : '未闭合'}
        </StatusBadge>
      </div>
      {summary.note && <div className="mt-1.5 text-[11px] text-amber-400/80">{summary.note}</div>}
      {summary.path && <div className="mt-1 font-mono text-[10px] text-slate-600">{summary.path}</div>}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  2. 能力地图（P1 折叠）
// ═══════════════════════════════════════════════════════════

export function CapabilityMapPanel() {
  const [stage, setStage] = useState('')
  const [risk, setRisk] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const { data, loading, error, reload } = usePanel<CapabilityMapView>(
    () => fetchCapabilityMap({ stage, risk, q, limit: 500, offset }),
    [stage, risk, q, offset],
  )
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="能力地图"
            description="capability × provenance / risk / data_class / evolution.stage（真实台账）"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
          />
          {data.load_error && (
            <div className="mb-2 rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
              台账读取失败：{data.load_error}
            </div>
          )}
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">能力总数 <MetricValue metric={data.total} /></span>
            <span className="text-slate-500">命中 <MetricValue metric={data.matched} /></span>
            <input
              value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }}
              placeholder="搜索能力/描述"
              className="ml-2 w-44 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200 placeholder:text-slate-600"
            />
            <select value={stage} onChange={(e) => { setStage(e.target.value); setOffset(0) }}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
              <option value="">全部 stage</option>
              {Object.keys(data.distribution.by_stage).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <select value={risk} onChange={(e) => { setRisk(e.target.value); setOffset(0) }}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
              <option value="">全部风险</option>
              {Object.keys(data.distribution.by_risk).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>

          <div className="mb-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-4">
            <DistBox title="按来源(provenance)" dist={data.distribution.by_provenance} />
            <DistBox title="按风险" dist={data.distribution.by_risk} />
            <DistBox title="按数据分级" dist={data.distribution.by_data_class} />
            <DistBox title="按 stage" dist={data.distribution.by_stage} />
          </div>

          <VirtualList
            items={data.items}
            itemHeight={74}
            height={520}
            className="rounded-lg border border-slate-800"
            keyOf={(c) => c.capability_id}
            renderItem={(c) => (
              <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 text-xs">
                <span className="w-56 shrink-0 truncate font-mono text-cyan-300" title={c.capability_id}>
                  {c.capability_id}
                </span>
                <StatusBadge tone={toneForStage(c.stage)}>{c.stage || '未入轨'}</StatusBadge>
                <StatusBadge tone={toneForRisk(c.risk_level)}>{c.risk_level || '未标注'}</StatusBadge>
                <span className="text-slate-500">{c.provenance || '—'}</span>
                <span className="text-slate-500">{c.data_class || '—'}</span>
                <span className="ml-auto flex items-center gap-3">
                  <MetricValue metric={c.success_rate} format="percent" />
                  <span className="text-slate-600">n={c.sample_count ?? '—'}</span>
                  {/* 缺 undo_hint ⇒ 不出现审批气泡（§7）；这里如实标注 */}
                  {c.approval_bubble_eligible
                    ? <StatusBadge tone="blue" title="含 undo_hint/补偿动作：可出审批气泡">可退</StatusBadge>
                    : <StatusBadge tone="gray" title="缺 undo_hint 且无补偿动作：不出现审批气泡（§7）">无退路</StatusBadge>}
                </span>
              </div>
            )}
          />
          <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
            <button type="button" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 500))}
              className="rounded border border-slate-700 px-2 py-0.5 disabled:opacity-40">上一页</button>
            <span>{offset + 1}–{Math.min(offset + 500, data.pagination.total)} / {data.pagination.total}</span>
            <button type="button" disabled={!data.pagination.has_more}
              onClick={() => setOffset(offset + 500)}
              className="rounded border border-slate-700 px-2 py-0.5 disabled:opacity-40">下一页</button>
          </div>
        </>
      )}
    </PanelShell>
  )
}

function DistBox({ title, dist }: { title: string; dist: Record<string, number> }) {
  const entries = Object.entries(dist || {})
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2">
      <div className="text-slate-500">{title}</div>
      <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
        {entries.length === 0 && <span className="text-slate-600">—</span>}
        {entries.map(([k, v]) => (
          <span key={k} className="text-slate-300">{k}:{v}</span>
        ))}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  3. 审批收件箱（P0）— 批量裁决 + 气泡规则
// ═══════════════════════════════════════════════════════════

export function ApprovalInboxPanel() {
  const { data, loading, error, reload } = usePanel<ApprovalInboxView>(
    () => fetchApprovalInbox({ limit: 100 }),
    [],
  )
  const { state: sec } = useSecurityState()
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [batchId, setBatchId] = useState('')

  const items = useMemo(() => data?.items || [], [data])
  const selectedIds = useMemo(
    () => items.filter((i) => selected[i.record_id]).map((i) => i.record_id),
    [items, selected],
  )
  const groups = data?.batch_groups || []
  const selectedGroup = groups.find((g) => g.record_ids.some((id) => selected[id]))

  async function approveBatch() {
    if (!sec) { setMsg('安全常量未就绪（U1：未拿到常量不得执行审批）'); return }
    setBusy('approve'); setMsg('')
    try {
      // 两段式：先为所选记录签发逐条绑定的一次性链接（后端逐条单表校验）
      const link = batchId ? { batch_id: batchId } : await batchLink(selectedIds)
      setBatchId(link.batch_id)
      const out = await batchDecide({
        batch_id: link.batch_id, decision: 'approve', reason: reason || '一键批（同策略同风险）',
      })
      setMsg(`批量批准：成功 ${out.succeeded} / ${out.requested}（失败 ${out.failed}）`)
      setSelected({}); setBatchId(''); reload()
    } catch (e) {
      setMsg(`批量裁决被拒：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }

  async function rejectBatch() {
    if (!sec) { setMsg('安全常量未就绪（U1：未拿到常量不得执行审批）'); return }
    if (!reason.trim()) { setMsg('驳回必须填写理由（审计要求）'); return }
    setBusy('reject'); setMsg('')
    try {
      const link = batchId ? { batch_id: batchId } : await batchLink(selectedIds)
      setBatchId(link.batch_id)
      const out = await batchDecide({ batch_id: link.batch_id, decision: 'reject', reason })
      setMsg(`批量驳回：成功 ${out.succeeded} / ${out.requested}`)
      setSelected({}); setBatchId(''); reload()
    } catch (e) {
      setMsg(`批量裁决被拒：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy('') }
  }

  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="审批收件箱"
            description="批量裁决=逐条走同一审批链（会话+CSRF+链接+二次认证+矩阵），无旁路"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
            actions={
              <span className="flex items-center gap-2 text-xs">
                <MetricValue metric={data.pending_total} label="待审" />
                <MetricValue metric={data.bubble_hidden} label="不出气泡" />
              </span>
            }
          />
          {data.error && <div className="mb-2 text-xs text-red-300">{data.error}</div>}

          {/* ★ 批量裁决：同策略同风险一键批（分组键来自后端） */}
          <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
              <Users size={13} /> 同策略同风险可一键批（§7）——分组键由后端给出，前端不推断
            </div>
            <div className="flex flex-wrap gap-2">
              {groups.map((g) => (
                <button
                  key={g.batch_key}
                  type="button"
                  onClick={() => {
                    const next: Record<string, boolean> = {}
                    g.record_ids.forEach((id) => { next[id] = true })
                    setSelected(next); setBatchId('')
                  }}
                  className={`rounded border px-2 py-1 text-[11px] ${
                    selectedGroup?.batch_key === g.batch_key
                      ? 'border-cyan-600 bg-cyan-500/15 text-cyan-200'
                      : 'border-slate-700 text-slate-300 hover:border-cyan-700'
                  }`}
                >
                  {g.object_type} · {g.level} · 风险 {g.risk}（{g.count}）
                  {g.bubble_visible_count === 0 && <span className="ml-1 text-slate-500">无气泡</span>}
                </button>
              ))}
              {groups.length === 0 && <span className="text-xs text-slate-500">无待审记录</span>}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input
                value={reason} onChange={(e) => setReason(e.target.value)}
                placeholder="批准备注 / 驳回理由（驳回必填）"
                className="w-64 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600"
              />
              <button type="button" disabled={!selectedIds.length || busy !== ''}
                onClick={approveBatch}
                className="rounded bg-cyan-700 px-3 py-1 text-xs text-white disabled:opacity-40">
                {busy === 'approve' ? '提交中…' : `批准所选（${selectedIds.length}）`}
              </button>
              <button type="button" disabled={!selectedIds.length || busy !== ''}
                onClick={rejectBatch}
                className="rounded border border-red-800 px-3 py-1 text-xs text-red-300 disabled:opacity-40">
                {busy === 'reject' ? '提交中…' : `驳回所选（${selectedIds.length}）`}
              </button>
              {selectedIds.length === 0 && (
                <span className="text-[11px] text-slate-500">先点上方分组（或逐条勾选）</span>
              )}
            </div>
            {msg && <div className="mt-2 text-[11px] text-cyan-300">{msg}</div>}
          </div>

          <VirtualList
            items={items}
            itemHeight={92}
            height={560}
            className="rounded-lg border border-slate-800"
            keyOf={(i) => i.record_id}
            renderItem={(i) => (
              <ApprovalRow
                item={i}
                checked={!!selected[i.record_id]}
                onToggle={() => { setSelected((s) => ({ ...s, [i.record_id]: !s[i.record_id] })); setBatchId('') }}
                onRefresh={reload}
                sec={sec}
              />
            )}
          />

          {/* 七动作（审批/熔断/回滚…）：写动作走后端审批，前端不得旁路（U4） */}
          <div className="mt-3">
            <Collapsible title="七动作（对所选记录 / 目标）" defaultOpen={false}>
              <AbsoluteActionBar
                target={selectedIds[0] || items[0]?.record_id || ''}
                targetLabel="审批记录"
                onDone={reload}
              />
            </Collapsible>
          </div>

          <div className="mt-2 font-mono text-[10px] text-slate-600">
            {JSON.stringify(data.decision_contract)}
          </div>
        </>
      )}
    </PanelShell>
  )
}

function ApprovalRow({
  item, checked, onToggle, onRefresh, sec,
}: {
  item: ApprovalItem
  checked: boolean
  onToggle: () => void
  onRefresh: () => void
  sec: SecurityRenderState | null
}) {
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  async function single(decision: 'approve' | 'reject') {
    if (!sec) { setMsg('安全常量未就绪'); return }
    setBusy(true); setMsg('')
    try {
      const link = await (await import('@/lib/cpPanelsApi')).batchLink([item.record_id])
      const out = await batchDecide({
        batch_id: link.batch_id, decision,
        reason: decision === 'reject' ? (window.prompt('驳回理由（必填）') || '') : '单条确认',
      })
      const row = out.results?.[0]
      setMsg(row && row.ok ? `${decision === 'approve' ? '已批准' : '已驳回'}` : `被拒：${JSON.stringify(row)}`)
      onRefresh()
    } catch (e) {
      setMsg(`被拒：${e instanceof Error ? e.message : String(e)}`)
    } finally { setBusy(false) }
  }

  return (
    <div className="flex items-start gap-3 border-b border-slate-800/60 px-3 py-2 text-xs">
      <input type="checkbox" checked={checked} onChange={onToggle} className="mt-1" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11px] text-cyan-300">{item.object_type}</span>
          <span className="truncate text-slate-300">{item.object_id}</span>
          <StatusBadge tone="blue">{item.level}</StatusBadge>
          <StatusBadge tone={toneForRisk(item.risk)}>风险 {item.risk || '未标注'}</StatusBadge>
          {/* §5.7⑦：外来内容恒带 TaintBadge（class 名来自后端常量） */}
          {item.taint?.taint && <TaintBadge source={item.taint.taint_reason} />}
          {/* ★ 缺 undo_hint 不出现审批气泡（后端判定，前端不放宽） */}
          {!item.bubble.visible && (
            <StatusBadge tone="gray" title={item.bubble.rule}>不出气泡</StatusBadge>
          )}
        </div>
        <div className="mt-1 line-clamp-2 text-slate-400">{item.description || '(无说明)'}</div>
        <div className="mt-0.5 text-[10px] text-slate-600">
          {item.actor} / {item.actor_type || '—'} · {formatTime(item.created_at)} ·
          {item.bubble.undo_hint ? ` undo_hint: ${item.bubble.undo_hint}` : ' 无 undo_hint'}
        </div>
        {msg && <div className="mt-1 text-[10px] text-cyan-300">{msg}</div>}
      </div>
      {/* ★ U2：审批按钮区挂在工作台内的 Shadow DOM 自治单元里 */}
      <ApprovalZone inline>
        <div className="flex items-center gap-2">
          <button type="button" disabled={busy || !sec} data-primary="true" onClick={() => single('approve')}>
            批准
          </button>
          <button type="button" disabled={busy || !sec} onClick={() => single('reject')}>驳回</button>
        </div>
      </ApprovalZone>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  4. 成本 ROI（P1 折叠）
// ═══════════════════════════════════════════════════════════

export function RoiPanel() {
  const { data, loading, error, reload } = usePanel<RoiView>(
    () => fetchRoi({ days: 30 }),
    [],
  )
  const daily = (data?.daily || {}) as Record<string, unknown>
  const decay = data?.approval_decay
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="成本 ROI"
            description="月省 / 投入 / 阈值 + ACR/UTC + 审批衰减率（口径逐项标注）"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
          />
          {(data.daily as { available?: boolean })?.available === false ? (
            <AbsentBox label="日成本视图不可用" reason={(data.daily as { reason?: string }).reason} />
          ) : (
            <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
              <KpiTile label="日归一成本" metric={daily.cost_normalized_cents as never} format="cents" icon={<TrendingUp size={13} />} />
              <KpiTile label="日有效成本" metric={daily.cost_effective_cents as never} format="cents" />
              <KpiTile label="日预算阈值" metric={daily.daily_budget_cents as never} format="cents" />
              <KpiTile label="日/基线比" metric={daily.ratio as never} format="number" />
            </div>
          )}
          <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
            <StatusBadge tone={daily.over_budget ? 'red' : 'green'}>
              {daily.over_budget ? '已超日预算（日级硬熔断判据）' : '未超日预算'}
            </StatusBadge>
            <span className="text-slate-500">
              口径版本 {String(daily.cost_schema_version ?? '—')} / {String(daily.calibration_version ?? '—')}
            </span>
            <span className="text-slate-500">数据源 {String(daily.source_of_truth ?? '—')}</span>
          </div>

          {/* ★ U8：策略延迟两个口径并列（不得混用） */}
          <Collapsible title="策略延迟口径（U8：本体 vs 全量埋点，不得混用）" defaultOpen>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              <div className="rounded border border-slate-800 bg-slate-950/50 p-3">
                <div className="text-xs text-slate-400">决策本体 p99（不含埋点）</div>
                <div className="mt-1 text-lg"><MetricValue metric={data.policy_latency.decision_body} format="ms" /></div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-950/50 p-3">
                <div className="text-xs text-slate-400">全量埋点端到端（命中 / 未命中）</div>
                <div className="mt-1 font-mono text-sm text-slate-200">
                  {data.policy_latency.full_instrumentation.hit_ms} ms / {data.policy_latency.full_instrumentation.miss_ms} ms
                </div>
                <div className="mt-1 text-[10px] text-amber-400/80">
                  {data.policy_latency.full_instrumentation.note}
                </div>
              </div>
            </div>
            <div className="mt-2 text-[11px] text-red-300">
              可比性：{data.policy_latency.comparability}（两个口径**不可互换/相减/平均**）
            </div>
          </Collapsible>

          <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
            {/* 审批衰减率 */}
            <Collapsible title="审批衰减率（披露不考核）" defaultOpen>
              {decay?.available === false ? (
                <AbsentBox label="衰减率不可用" reason={decay.reason} />
              ) : (
                <div className="space-y-2 text-xs">
                  <div className="flex items-center gap-3">
                    <span className="text-slate-400">自动消化占比</span>
                    <span className="text-lg"><MetricValue metric={decay?.decay_rate} format="percent" /></span>
                    <StatusBadge tone={decay?.meets_target ? 'green' : 'yellow'}>
                      目标 {formatPercent((decay?.target?.value as number) ?? null)}
                    </StatusBadge>
                    {decay?.insufficient_sample && <StatusBadge tone="yellow">样本不足，仅披露</StatusBadge>}
                  </div>
                  <div className="text-slate-500">
                    自动 {decay?.auto_disposed ?? '—'} / 人工 {decay?.human_disposed ?? '—'} /
                    合计 {decay?.total_disposed ?? '—'}
                  </div>
                  <div className="flex items-center gap-2 text-slate-500">
                    审批迟滞中位数 <MetricValue metric={decay?.latency_median_ms} format="ms" />
                  </div>
                  {decay?.fatigue_buckets && (
                    <div className="flex flex-wrap gap-2 text-[11px] text-slate-500">
                      疲劳分桶：
                      {Object.entries(decay.fatigue_buckets).map(([k, v]) => (
                        <span key={k}>{k}:{v}</span>
                      ))}
                    </div>
                  )}
                  {decay?.note && <div className="text-[10px] text-amber-400/80">{decay.note}</div>}
                </div>
              )}
            </Collapsible>

            {/* §6.7 指标字典 */}
            <Collapsible title="§6.7 周报指标（S5-02 字典）" defaultOpen>
              <SloMetricsTable metrics={data.slo_metrics} />
            </Collapsible>
          </div>
        </>
      )}
    </PanelShell>
  )
}

function SloMetricsTable({
  metrics,
}: {
  metrics?: RoiView['slo_metrics']
}) {
  if (!metrics || (metrics as { available?: boolean }).available === false) {
    return <AbsentBox label="指标字典不可用" reason={(metrics as { reason?: string })?.reason} />
  }
  const rows: SloMetricRow[] = Object.values(
    (metrics as { metrics: Record<string, SloMetricRow> }).metrics || {},
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[11px]">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 pr-2">指标</th>
            <th className="py-1 pr-2">值</th>
            <th className="py-1 pr-2">样本</th>
            <th className="py-1 pr-2">状态</th>
            <th className="py-1">来源/公式</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-slate-800/60">
              <td className="py-1 pr-2 text-slate-300" title={r.definition}>{r.name}</td>
              <td className="py-1 pr-2 font-mono text-slate-200">
                {r.value === null ? '—' : formatNumber(r.value, { digits: 3 })}
                <span className="ml-1 text-slate-500">{r.unit}</span>
              </td>
              <td className="py-1 pr-2 text-slate-500">{r.samples} / {r.min_samples}</td>
              <td className="py-1 pr-2">
                <StatusBadge
                  tone={r.status === 'ok' ? 'green'
                    : r.status === 'insufficient_samples' ? 'yellow' : 'gray'}
                >
                  {r.status}
                </StatusBadge>
              </td>
              <td className="py-1 font-mono text-[10px] text-slate-500" title={`${r.source}\n${r.formula}`}>
                {r.source}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={5} className="py-2 text-slate-500">无指标行</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  5. 自愈事故（P1；有事故自动展开）
// ═══════════════════════════════════════════════════════════

export function IncidentsPanel() {
  const { data, loading, error, reload } = usePanel<IncidentsView>(
    () => fetchIncidents({ limit: 200, days: 30 }),
    [],
  )
  const [expanded, setExpanded] = useState<boolean | null>(null)
  const autoOpen = expanded ?? !!data?.auto_expand
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="自愈事故"
            description="IncidentCard 六要素 + MTTD/MTTR + 备份健康（§8.6）"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
            actions={
              <button type="button" onClick={() => setExpanded(!autoOpen)}
                className="rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-400">
                {autoOpen ? '折叠' : '展开'}
              </button>
            }
          />
          {data.auto_expand && <div className="mb-3"><IncidentBanner count={data.open_count.value ?? 0} note={data.auto_expand_rule} /></div>}
          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <KpiTile label="未闭合事故" metric={data.open_count} icon={<Siren size={13} />} />
            <KpiTile label="事故卡总数" metric={data.total_count} />
            <KpiTile label="MTTD 中位数" metric={data.mttd_ms?.mttd_ms} format="ms" />
            <KpiTile label="MTTR 中位数" metric={data.mttd_ms?.mttr_ms} format="ms" />
          </div>
          {data.load_error && (
            <div className="mb-2 text-[11px] text-red-300">事故目录读取失败：{data.load_error}</div>
          )}

          {autoOpen && (
            <div className="space-y-2">
              {data.incidents.length === 0 && (
                <AbsentBox label="无事故卡" reason="data/healing_incidents 为空（无事故即无需自动展开）" />
              )}
              {data.incidents.map((c) => (
                <IncidentCardView key={c.id} card={c} />
              ))}
            </div>
          )}

          <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Collapsible title="备份健康（沿用既有 disaster_recovery）" defaultOpen>
              {(data.backup_health as { available?: boolean })?.available === false ? (
                <AbsentBox reason={(data.backup_health as { reason?: string }).reason} />
              ) : (
                <BackupHealthCard health={data.backup_health as Record<string, unknown>} />
              )}
            </Collapsible>
            <Collapsible title="MTTD / MTTR 口径" defaultOpen>
              <div className="space-y-1 text-xs text-slate-400">
                <div className="flex items-center gap-2">
                  MTTD <MetricValue metric={data.mttd_ms?.mttd_ms} format="ms" />
                  <span className="text-[10px] text-slate-600">样本 {data.mttd_ms?.mttd_samples ?? 0}</span>
                </div>
                <div className="flex items-center gap-2">
                  MTTR <MetricValue metric={data.mttd_ms?.mttr_ms} format="ms" />
                  <span className="text-[10px] text-slate-600">样本 {data.mttd_ms?.mttr_samples ?? 0}</span>
                </div>
                <div className="text-[10px] text-amber-400/80">{data.mttd_ms?.note}</div>
                {data.mttd_ms?.by_level && (
                  <div className="flex flex-wrap gap-2 text-[11px]">
                    按级别：{Object.entries(data.mttd_ms.by_level).map(([k, v]) => <span key={k}>{k}:{v}</span>)}
                  </div>
                )}
              </div>
            </Collapsible>
          </div>
        </>
      )}
    </PanelShell>
  )
}

function IncidentCardView({ card }: { card: IncidentsView['incidents'][number] }) {
  const ready = card.missing_elements.length === 0
  return (
    <div className={`rounded-lg border p-3 ${card.status === 'open' ? 'border-red-900/60 bg-red-950/20' : 'border-slate-800 bg-slate-900/40'}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <StatusBadge tone={toneForSeverity(card.severity)}>{card.severity}</StatusBadge>
        <span className="font-mono text-[11px] text-slate-400">{card.id}</span>
        <StatusBadge tone={card.status === 'open' ? 'red' : 'green'}>{card.status}</StatusBadge>
        <StatusBadge tone={ready ? 'green' : 'yellow'} title="§3：六要素齐才可 resolved">
          {ready ? '六要素齐备' : `缺要素 ${card.missing_elements.length}`}
        </StatusBadge>
        <span className="ml-auto text-[10px] text-slate-500">{formatTime(card.created_at)}</span>
      </div>
      <div className="mt-2 space-y-1 text-[11px] text-slate-300">
        <div>根因：{card.root_cause || '—'}</div>
        <div>致命变更：<span className="font-mono">{card.fatal_change || '—'}</span></div>
        <div>规避规则：{card.evasion_rule || '—'}</div>
        <div className="flex flex-wrap gap-3 text-slate-500">
          <span>MTTD {formatMs(card.mttd_ms)}</span>
          <span>MTTR {formatMs(card.mttr_ms)}</span>
          <span>trace {card.trace_ids.length} 条</span>
        </div>
      </div>
      <div className="mt-2">
        <ReasonChain
          title="六要素逐项"
          nodes={[
            { label: 'root_cause', passed: !!card.root_cause, value: card.root_cause || '—' },
            { label: 'fatal_change', passed: !!card.fatal_change, value: card.fatal_change || '—' },
            { label: 'evasion_rule', passed: !!card.evasion_rule, value: card.evasion_rule || '—' },
            { label: 'in_strategy_memory', passed: !!card.in_strategy_memory?.yes, value: card.in_strategy_memory?.id || '—' },
            { label: 'regression_case_added', passed: !!card.regression_case_added?.yes, value: card.regression_case_added?.case_id || '—' },
            { label: 'trace_ids', passed: card.trace_ids.length > 0, value: `${card.trace_ids.length} 条` },
          ]}
        />
      </div>
    </div>
  )
}

function BackupHealthCard({ health }: { health: Record<string, unknown> }) {
  const cfg = (health.config || {}) as Record<string, unknown>
  const latest = health.latest_backup as Record<string, unknown> | null
  return (
    <div className="space-y-1.5 text-xs text-slate-300">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge tone={cfg.enabled ? 'green' : 'gray'}>{cfg.enabled ? '备份已启用' : '备份未启用'}</StatusBadge>
        <StatusBadge tone={health.scheduler_running ? 'green' : 'yellow'}>
          {health.scheduler_running ? '调度中' : '未调度'}
        </StatusBadge>
        <MetricValue metric={health.backup_count as never} label="备份数" />
      </div>
      <div className="text-[11px] text-slate-500">
        备份目录 <span className="font-mono">{String(cfg.backup_dir || '—')}</span>
      </div>
      {latest ? (
        <div className="text-[11px]">
          最近备份 <span className="font-mono">{String(latest.backup_id)}</span> ·{' '}
          {String(latest.timestamp)} · {formatNumber((latest.size as number) ?? null)} B
        </div>
      ) : (
        <div className="text-[11px] text-slate-500">最近备份：—（无记录）</div>
      )}
      <div className="text-[10px] text-amber-400/80">{String(health.event_note || '')}</div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  6. 记忆 / 技能库（P2 折叠）
// ═══════════════════════════════════════════════════════════

export function MemorySkillsPanel() {
  const { data, loading, error, reload } = usePanel<MemorySkillsView>(
    () => fetchMemorySkills({ limit: 100 }),
    [],
  )
  const layers = data?.layers as { by_layer?: Record<string, number>; entries?: MemorySkillsView['layers'] extends infer _ ? import('@/lib/cpPanelsTypes').MemoryCardData[] : never; available?: boolean; reason?: string } | undefined
  const rp = data?.recall_priority
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="记忆 / 技能库"
            description="四层记忆（策略/事实/偏好/工作）· 现有技能中心仍在「记忆管理 → 技能中心」"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
          />
          {/* ★ U5：召回优先级契约（单一来源＝后端 taxonomy） */}
          <Collapsible title="召回优先级契约（U5：策略 > 事实 > 偏好）" defaultOpen>
            {rp && (rp as { available?: boolean }).available === false ? (
              <AbsentBox label="优先级口径不可用" reason={(rp as { reason?: string }).reason} />
            ) : (
              <div className="space-y-1 text-xs text-slate-300">
                <div className="flex flex-wrap items-center gap-2">
                  {(rp as { order?: string[] })?.order?.map((o, i) => (
                    <span key={o} className="flex items-center gap-1">
                      <StatusBadge tone={i === 0 ? 'green' : i === 1 ? 'blue' : 'gray'}>{o}</StatusBadge>
                      {i < ((rp as { order?: string[] })?.order?.length ?? 1) - 1 && <span className="text-slate-600">›</span>}
                    </span>
                  ))}
                </div>
                <div className="font-mono text-[10px] text-slate-500">来源：{(rp as { source?: string })?.source}</div>
                <div className="text-[11px] text-slate-500">公式：{(rp as { formula?: string })?.formula}</div>
                <div className="text-[11px] text-amber-400/80">{(rp as { contract?: string })?.contract}</div>
              </div>
            )}
          </Collapsible>

          <div className="mt-3">
            {layers?.available === false ? (
              <AbsentBox label="记忆存储不可用" reason={layers.reason} />
            ) : (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                  <MetricValue metric={(layers as { total?: never })?.total} label="召回条目" />
                  {Object.entries(layers?.by_layer || {}).map(([k, v]) => (
                    <span key={k} className="text-slate-500">{k}:{v}</span>
                  ))}
                </div>
                <VirtualList
                  items={(layers?.entries || []) as import('@/lib/cpPanelsTypes').MemoryCardData[]}
                  itemHeight={56}
                  height={480}
                  className="rounded-lg border border-slate-800"
                  keyOf={(m) => m.memory_id}
                  renderItem={(m) => (
                    <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 text-xs">
                      <StatusBadge tone={m.layer === 'strategy' ? 'green' : m.layer === 'fact' ? 'blue' : 'gray'}>
                        {m.layer}
                      </StatusBadge>
                      <span className="w-24 shrink-0 truncate font-mono text-[10px] text-slate-500" title={m.memory_id}>
                        {m.memory_id}
                      </span>
                      {/* content_redacted 已是脱敏文本（§3.4 脱敏先于哈希） */}
                      <span className="min-w-0 flex-1 truncate text-slate-300">{m.content_redacted}</span>
                      {m.org_level && <StatusBadge tone="blue">org 级</StatusBadge>}
                      {m.degraded && <StatusBadge tone="yellow">降级</StatusBadge>}
                      {m.forget_candidate && <StatusBadge tone="red">遗忘候选</StatusBadge>}
                      <span className="shrink-0 text-[10px] text-slate-600">{m.scope}</span>
                    </div>
                  )}
                />
              </>
            )}
          </div>
        </>
      )}
    </PanelShell>
  )
}

// ═══════════════════════════════════════════════════════════
//  7. 审计导出（含验签摘要）
// ═══════════════════════════════════════════════════════════

export function AuditExportPanel() {
  const [limit, setLimit] = useState(200)
  const [scope, setScope] = useState<'exported' | 'full' | 'head'>('exported')
  const { data, loading, error, reload } = usePanel<AuditExportView>(
    () => fetchAuditExport({ limit, verify: true, verify_scope: scope }),
    [limit, scope],
  )
  const [dl, setDl] = useState('')
  const [obs, setObs] = useState<ObservabilityStreamView | null>(null)
  const [alerts, setAlerts] = useState<AuthzAlertsView | null>(null)

  async function onExport() {
    try {
      const { filename, blob } = await downloadAuditCsv({ limit: 2000 })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename; a.click()
      URL.revokeObjectURL(url)
      setDl(`已导出（含验签摘要；verify.ok=${data?.verify.ok === true}）`)
    } catch (e) {
      setDl(`导出失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="审计导出"
            description="链式台账 + 验签摘要（S2-02）：导出内容与验签结果同源，文件自证完整性"
            priority="P0"
            datasources={data.panel?.datasources}
            actions={
              <button type="button" onClick={onExport}
                className="flex items-center gap-1 rounded bg-cyan-700 px-2 py-1 text-[11px] text-white">
                <FileDown size={12} /> 导出 CSV
              </button>
            }
          />
          {dl && <div className="mb-2 text-[11px] text-cyan-300">{dl}</div>}

          <div className="mb-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <KpiTile label="导出记录" metric={data.exported} icon={<ScrollText size={13} />} />
            <KpiTile label="已验签" metric={{
              value: data.verify.checked, available: true, source: 'AuditChain.verify_chain()',
              formula: '从锚点重算 self_hash 并逐条比对的记录数',
              unit: 'count', dataset: data.verify_scope, note: '', sample_size: data.verify.checked,
            }} />
            <KpiTile label="链头 seq" metric={{
              value: data.chain_head.last_seq ?? null, available: data.chain_head.last_seq !== undefined,
              source: 'AuditChain.chain_head()', formula: '最后一条记录 seq',
              unit: 'seq', dataset: '', note: '',
            }} />
            <KpiTile label="读+验签耗时" metric={data.elapsed_ms} format="ms" />
          </div>

          <div className={`mb-3 rounded-lg border px-3 py-2 text-xs ${
            data.verify.ok ? 'border-emerald-900/60 bg-emerald-950/20 text-emerald-300'
              : 'border-red-900/60 bg-red-950/30 text-red-300'}`}>
            <div className="flex items-center gap-2">
              <StatusBadge tone={data.verify.ok ? 'green' : 'red'}>
                {data.verify.ok ? '验签通过' : '验签失败'}
              </StatusBadge>
              <span>checked={data.verify.checked} · reason={data.verify.reason}</span>
              {data.verify.first_bad_seq !== null && (
                <span>首个异常 seq=<strong>{data.verify.first_bad_seq}</strong>
                  （其后 {data.verify.bad_seqs.length} 条异常）</span>
              )}
            </div>
            {data.verify.detail && <div className="mt-1 font-mono text-[10px] opacity-80">{data.verify.detail}</div>}
            <div className="mt-1 text-[10px] opacity-70">
              验签口径：{data.verify_scope}
              （exported=仅本次导出区间；full=全链重算，耗时随链长线性增长）
            </div>
          </div>

          <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
            <select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1">
              <option value="exported">验签：导出区间</option>
              <option value="head">验签：链尾窗口</option>
              <option value="full">验签：全链（慢）</option>
            </select>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
              className="rounded border border-slate-700 bg-slate-950 px-2 py-1">
              {[50, 200, 500, 1000, 2000].map((n) => <option key={n} value={n}>{n} 条</option>)}
            </select>
          </div>

          <VirtualList
            items={data.entries}
            itemHeight={44}
            height={420}
            className="rounded-lg border border-slate-800"
            keyOf={(e) => String(e.seq)}
            renderItem={(e) => (
              <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 font-mono text-[10px] text-slate-400">
                <span className="w-14 shrink-0 text-slate-500">#{e.seq}</span>
                <span className="w-32 shrink-0 truncate">{e.actor}</span>
                <span className="w-48 shrink-0 truncate text-cyan-300">{e.action}</span>
                <span className="min-w-0 flex-1 truncate">{e.subject}</span>
                <span className="w-20 shrink-0">{e.status}</span>
                <span className="w-32 shrink-0 text-slate-600">{formatTime(e.ts)}</span>
                <span className="shrink-0 text-slate-600" title={e.self_hash}>
                  {String(e.self_hash || '').slice(0, 10)}…
                </span>
              </div>
            )}
          />

          {/* S2-03 #12：ACR/UTC + 降级拓扑 + 逃逸清单（四类面板非 mock） */}
          <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Collapsible title="模型降级拓扑 / 逃逸清单（事件流消费）" defaultOpen={false}>
              <button type="button"
                onClick={async () => {
                  const { fetchObservabilityStream: f } = await import('@/lib/cpPanelsApi')
                  setObs(await f({ days: 30 }))
                }}
                className="mb-2 rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300">
                加载事件流聚合
              </button>
              {obs && <EventStreamView view={obs} />}
            </Collapsible>
            <Collapsible title="越权告警聚合（U3：actor_ip_hash + 窗口阈值）" defaultOpen={false}>
              <button type="button"
                onClick={async () => {
                  const { fetchAuthzAlerts: f } = await import('@/lib/cpPanelsApi')
                  setAlerts(await f({ days: 30, limit: 50 }))
                }}
                className="mb-2 rounded border border-slate-700 px-2 py-1 text-[11px] text-slate-300">
                加载越权告警
              </button>
              {alerts && <AuthzAlertsViewBox view={alerts} />}
            </Collapsible>
          </div>
        </>
      )}
    </PanelShell>
  )
}

function EventStreamView({ view }: { view: ObservabilityStreamView }) {
  const degrade = view.model_degrade
  const escape = view.escape
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex items-center gap-1">
          <Activity size={12} /> 模型降级
          <MetricValue metric={degrade?.total} />
        </span>
        <span className="text-slate-500">
          回退尝试 {degrade?.fallback_attempted ?? '—'} / 成功 {degrade?.fallback_succeeded ?? '—'}
        </span>
      </div>
      {degrade?.edges && Object.keys(degrade.edges).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(degrade.edges).map(([edge, n]) => (
            <span key={edge} className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
              {edge} × {n}
            </span>
          ))}
        </div>
      )}
      {degrade?.chain && (
        <div className="text-[10px] text-slate-500">
          回退链：{degrade.chain.models?.join(' → ') || '—'}（来源 {degrade.chain.source}，
          {degrade.chain.enabled ? '已启用' : '未启用'}）
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-800 pt-2">
        <span className="flex items-center gap-1">
          <ShieldAlert size={12} /> 逃逸清单
          <MetricValue metric={escape?.total} />
        </span>
        {escape?.by_path && Object.entries(escape.by_path).map(([p, n]) => (
          <span key={p} className="font-mono text-[10px] text-red-300">{p} × {n}</span>
        ))}
      </div>
      <div className="font-mono text-[10px] text-slate-600">受治理文件：{(escape?.watched || []).join(', ') || '—'}</div>
    </div>
  )
}

function AuthzAlertsViewBox({ view }: { view: AuthzAlertsView }) {
  const rt = view.realtime.stats as Record<string, unknown>
  const du = view.durable
  return (
    <div className="space-y-2 text-xs">
      <div className="rounded border border-amber-900/50 bg-amber-950/20 px-2 py-1 text-[10px] text-amber-300">
        {view.realtime.note}（volatile={String(view.realtime.volatile)}）
      </div>
      <div className="flex flex-wrap items-center gap-3 text-slate-400">
        <span>阈值 {String(rt.threshold ?? '—')}</span>
        <span>窗口 {String(rt.window_seconds ?? '—')}s</span>
        <span>累计 {String(rt.total ?? '—')}</span>
      </div>
      <div className="border-t border-slate-800 pt-2">
        <div className="mb-1 text-slate-400">
          耐久口径（事件流 policy.denied，窗口 {du.window_days ?? '—'} 天）
          <MetricValue metric={du.total} label="总计" />
        </div>
        {du.by_source_key && (
          <div className="mb-1 flex flex-wrap gap-2 font-mono text-[10px] text-slate-400">
            {Object.entries(du.by_source_key).map(([k, v]) => <span key={k}>{k.slice(0, 14)}… × {v}</span>)}
          </div>
        )}
        <div className="max-h-40 overflow-y-auto">
          {(du.recent || []).slice(0, 20).map((r, i) => (
            <div key={`${r.ts}-${i}`} className="flex items-center gap-2 border-b border-slate-800/50 py-0.5 text-[10px]">
              <span className="w-32 shrink-0 text-slate-500">{formatTime(r.ts)}</span>
              <span className="w-32 shrink-0 truncate text-slate-300">{r.actor}</span>
              <span className="w-40 shrink-0 truncate text-cyan-300">{r.operation}</span>
              <span className="min-w-0 flex-1 truncate text-slate-500">{r.reason}</span>
            </div>
          ))}
          {(du.recent || []).length === 0 && <div className="text-slate-500">窗口内无越权事件</div>}
        </div>
        <div className="mt-1 text-[10px] text-slate-600">{du.pii_note}</div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
//  导出：七动作 + 永不自动化五类确认 UI（U1 常量驱动）
// ═══════════════════════════════════════════════════════════

export { AbsoluteActionBar } from './actions'
export { ImplicitEntryLog } from './implicitEntry'
export { GovernancePanels as default }
