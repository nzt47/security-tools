/**
 * 治理面板集成测试（TASK-S6-01）
 * ------------------------------------------------------------------
 * 覆盖验收项：
 *   - 六面板逐面板：数据源实证（**非 mock 结构**：字段名与后端契约逐字一致）
 *   - 审批收件箱：同策略同风险一键批 + ★ 缺 undo_hint 不出气泡
 *   - 能力地图：缺 undo_hint/补偿动作 ⇒ 不允许出现审批气泡
 *   - ROI：★ U8 两个延迟口径并列展示且标注不可混用
 *   - 自愈事故：有 open 事故自动展开；MTTD/MTTR 缺位渲染 "—"
 *   - 审计导出：验签摘要（ok / first_bad_seq）
 *   - 记忆面板：★ U5 召回优先级契约来自后端
 *   - 七动作 + 五类确认：类别来自后端常量；60s 倒计时；**不声明 actor_type**
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { GovernancePanels } from './index'
import { resetSecurityStateCache } from './usePanel'

const api = vi.hoisted(() => ({
  fetchPipeline: vi.fn(),
  fetchCapabilityMap: vi.fn(),
  fetchApprovalInbox: vi.fn(),
  fetchRoi: vi.fn(),
  fetchIncidents: vi.fn(),
  fetchMemorySkills: vi.fn(),
  fetchAuditExport: vi.fn(),
  fetchObservabilityStream: vi.fn(),
  fetchAuthzAlerts: vi.fn(),
  fetchSecurityRenderState: vi.fn(),
  issueConfirmation: vi.fn(),
  runAction: vi.fn(),
  batchLink: vi.fn(),
  batchDecide: vi.fn(),
  downloadAuditCsv: vi.fn(),
}))

vi.mock('@/lib/cpPanelsApi', () => api)

const SECURITY = {
  ok: true,
  generated_at: 't',
  safe_render: {
    allowed_tags: [],
    csp: {},
    iframe_sandbox: 'allow-same-origin',
    image_proxy_prefix: '/api/cp/image-proxy?url=',
    system_slots: [],
    taint_badge: {
      class: 'cp-taint-badge',
      base_class: 'cp-taint-badge--has-bg',
      attr: 'data-cp-taint-source',
      always_has_background: true,
    },
    approval_zone: {
      class: 'cp-approval-zone',
      style: { position: 'fixed', 'z-index': '2147483000', isolation: 'isolate', 'pointer-events': 'auto' },
      shadow_root: true,
    },
  },
  boundary_words: {
    enabled: true,
    never_automated: ['transfer', 'publish', 'drop_database', 'permission_change', 'force_push'],
    labels: { transfer: '转账', publish: '发布', drop_database: '删库', permission_change: '改权限', force_push: 'push --force' },
    ttl_seconds: 60,
    max_ttl_seconds: 60,
    single_action_bound: true,
    accepts_text_approval: false,
    store: {},
  },
  injection_defense: {},
  frontend_contract: { must_not_customize: [], rule: '' },
}

const M = (value: number | null, unit = 'count', extra: Record<string, unknown> = {}) => ({
  value, available: value !== null, source: 'agent/x.py::f', formula: 'a/b',
  unit, dataset: '', note: '', sample_size: value ?? 0, min_sample: 20,
  insufficient_sample: (value ?? 0) < 20, disclosure_only: (value ?? 0) < 20,
  traceable: true, ...extra,
})

const PANEL = { panel: 'x', priority: 'P0', datasources: ['d1'], min_sample: 20, min_sample_source: 's', disclosure_note: '' }

beforeEach(() => {
  resetSecurityStateCache()
  Object.values(api).forEach((fn) => fn.mockReset())
  api.fetchSecurityRenderState.mockResolvedValue(SECURITY)
})
afterEach(cleanup)

// ════════════════════════════════════════════════════════════

describe('1. 消化流水线面板（P0 展开）', () => {
  const pipeline = {
    ok: true, panel: { ...PANEL, panel: 'digestion_pipeline' }, generated_at: 't',
    lanes: [
      { lane: 'trace_collect', title: '轨迹采集', event_count: M(29), truncated: false, items: [
        { event_id: 'e1', ts: '2026-09-12T08:00:00', actor: 'auto', capability_id: 'cp.fs.write',
          from_stage: null, to_stage: 'borrowed', applied: true, verdict: 'applied', scope: 'digestion',
          passport_id: '', pass_rate: null, rank_score: null, manual_required: null,
          reasons: ['首次入轨'], digest_run_id: 'd1', trace_id: 't1' } ] },
      { lane: 'pattern_mining', title: '模式挖掘', event_count: M(0), truncated: false, items: [] },
      { lane: 'skill_generation', title: 'Skill 生成', event_count: M(0), truncated: false, items: [] },
      { lane: 'acceptance', title: '验收', event_count: M(0), truncated: false, items: [] },
      { lane: 'gray', title: '灰度', event_count: M(2), truncated: false, items: [] },
    ],
    shadow: [{ capability_id: 'cp.fs.write', generated_at: 1, allowed: true, budget: 50,
      sampled: 57, passed: 55, negative: 2, judge_kind: 'deterministic_local',
      degradation: 'stable', shadow_version: 's3-03.1',
      p99_wall_candidate_ms: M(420, 'ms'), p99_wall_upstream_ms: M(380, 'ms'),
      pass_rate: M(0.9649, 'ratio') }],
    internalize: [{ capability_id: 'cp.fs.write', pr_id: 'pr_1', pr_dir: '/tmp', verdict: 'promote',
      blocker: '', stage: 'shadow', passport_id: 'psp', rank_score: 3.5, promotable: true,
      manual_required: false, manual_label: '', veto_failed: [], rank_failed: [],
      generated_at: 1, engine_version: 's3-03.1', audit_seq: 9, audit_hash: 'h',
      conditions: [{ name: 'roi_positive', dimension: 'rank', passed: true, actual: 340,
        threshold: 0, comparator: '>', score: 1, evidence_source: 's2-03_utc', reasons: [] }],
      roi: { monthly_saving_cents: M(340, 'cents'), one_time_investment_cents: M(2200, 'cents'),
        amortized_monthly_cents: M(183.33, 'cents'), net_monthly_cents: M(156.67, 'cents'),
        positive: true, monthly_samples: 57, caveats: [], assumptions: [] } }],
    manual_review: { sampled: 6, pending: 6, decided: 0, closed: false, by_verdict: {},
      note: '人工复核未完成前不得视为已验收（M5 口径）' },
    clock: { wall: 'CLOCK_WALL', note: '灰度 p99 一律标注墙钟' },
    summary: { digest_stage_events: M(29), capabilities_touched: M(1), applied_migrations: M(29),
      shadow_runs: M(1), internalize_decisions: M(1), internalize_rate: M(1, 'ratio'),
      stage_distribution: { borrowed: 32 } },
  }

  it('泳道五列按 §7 顺序渲染，列内事件来自真实 digest.stage', async () => {
    api.fetchPipeline.mockResolvedValue(pipeline)
    render(<GovernancePanels panel="pipeline" />)
    for (const title of ['轨迹采集', '模式挖掘', 'Skill 生成', '验收', '灰度']) {
      expect(await screen.findByText(title)).toBeInTheDocument()
    }
    expect((await screen.findAllByText('cp.fs.write')).length).toBeGreaterThan(0)
  })

  it('内化决策弹出原因链（六条件 + 证据来源），非原始 JSON', async () => {
    api.fetchPipeline.mockResolvedValue(pipeline)
    render(<GovernancePanels panel="pipeline" />)
    fireEvent.click(await screen.findByText(/六条件逐项/))
    expect(await screen.findByText(/roi_positive/)).toBeInTheDocument()
    expect(await screen.findByText(/s2-03_utc/)).toBeInTheDocument()
  })

  it('人工抽检未闭合时显示 M5 口径说明', async () => {
    api.fetchPipeline.mockResolvedValue(pipeline)
    render(<GovernancePanels panel="pipeline" />)
    expect(await screen.findByText(/M5 口径/)).toBeInTheDocument()
  })

  it('缺位数字渲染 "—"（内化率分母为 0 时）', async () => {
    api.fetchPipeline.mockResolvedValue({
      ...pipeline,
      summary: { ...pipeline.summary, internalize_rate: M(null, 'ratio') },
    })
    render(<GovernancePanels panel="pipeline" />)
    const cells = await screen.findAllByText('—')
    expect(cells.length).toBeGreaterThan(0)
  })
})

describe('2. 能力地图面板', () => {
  const capMap = {
    ok: true, panel: { ...PANEL, panel: 'capability_map', priority: 'P1' }, generated_at: 't',
    source: 'agent/descriptors/registry.py::list_with_trust()', load_error: '',
    total: M(32), matched: M(2),
    pagination: { total: 2, offset: 0, limit: 500, has_more: false },
    distribution: { by_provenance: { native: 1 }, by_risk: { low: 1, destructive: 1 },
      by_data_class: { public: 1 }, by_stage: { shadow: 1, borrowed: 1 }, by_platform: {} },
    items: [
      { capability_id: 'cp.a', name: 'a', description: '', source_type: 'builtin', source_id: 'builtin',
        provenance: 'native', risk_level: 'low', data_class: 'public', requires_approval: false,
        stage: 'shadow', audit_level: 'full', has_undo_hint: true, has_compensating_action: false,
        idempotent: true, timeout_ms: 1000, external_endpoint: false, variant_count: 0, aliases: [],
        platform: 'builtin', updated_at: 't', success_rate: M(0.97, 'rate'),
        sample_count: 57, sample_discipline: { sample_size: 57, min_sample: 20, insufficient_sample: false, disclosure_only: false, note: '' },
        p99_latency_ms: M(420, 'ms'), approval_bubble_eligible: true },
      { capability_id: 'cp.b', name: 'b', description: '', source_type: 'mcp', source_id: 'mcp:x',
        provenance: 'borrowed', risk_level: 'destructive', data_class: 'secret', requires_approval: true,
        stage: 'borrowed', audit_level: 'full', has_undo_hint: false, has_compensating_action: false,
        idempotent: false, timeout_ms: null, external_endpoint: true, variant_count: 1, aliases: ['b1'],
        platform: 'mcp', updated_at: 't', success_rate: M(0.5, 'rate', { sample_size: 4, insufficient_sample: true, disclosure_only: true }),
        sample_count: 4, sample_discipline: { sample_size: 4, min_sample: 20, insufficient_sample: true, disclosure_only: true, note: 'n' },
        p99_latency_ms: M(null, 'ms'), approval_bubble_eligible: false },
    ],
  }

  it('来自真实台账：显示来源与总量', async () => {
    api.fetchCapabilityMap.mockResolvedValue(capMap)
    render(<GovernancePanels panel="capabilities" />)
    expect(await screen.findByText(/capability × provenance/)).toBeInTheDocument()
    // 面板头提供"数据源(n)"可点击入口（验收：每个数字可溯源）
    expect(screen.getByText(/数据源（\d+）/)).toBeInTheDocument()
  })

  it('★ 缺 undo_hint 且无补偿动作 ⇒ 标注"无退路"（不出现审批气泡）', async () => {
    api.fetchCapabilityMap.mockResolvedValue(capMap)
    render(<GovernancePanels panel="capabilities" />)
    expect(await screen.findByText('无退路')).toBeInTheDocument()
    expect(await screen.findByText('可退')).toBeInTheDocument()
  })

  it('样本不足的行带"仅披露"', async () => {
    api.fetchCapabilityMap.mockResolvedValue(capMap)
    render(<GovernancePanels panel="capabilities" />)
    expect((await screen.findAllByText('仅披露')).length).toBeGreaterThan(0)
  })
})

describe('3. 审批收件箱面板（P0）', () => {
  const inbox = {
    ok: true, panel: { ...PANEL, panel: 'approval_inbox' }, generated_at: 't',
    count: 2, pending_total: M(2), pending_by_type: { 'stage.promote': 2 },
    bubble_hidden: M(2, 'count', { note: '缺 undo_hint ⇒ 不出气泡' }),
    items: [
      { record_id: 'r1', object_type: 'stage.promote', object_id: 'cp.a', level: 'L1', action: 'promote',
        description: 'd1', actor: 'system', actor_type: 'auto', manual_required: false,
        created_at: '2026-09-12T09:00:00', risk: 'low', batch_key: 'stage.promote|L1|low',
        governance: {}, taint: {},
        bubble: { visible: false, rule: '缺 undo_hint 不出现审批气泡（§7）', undo_hint: '', compensating_action: '' } },
      { record_id: 'r2', object_type: 'stage.promote', object_id: 'cp.b', level: 'L1', action: 'promote',
        description: 'd2', actor: 'system', actor_type: 'auto', manual_required: false,
        created_at: '2026-09-12T09:01:00', risk: 'low', batch_key: 'stage.promote|L1|low',
        governance: { undo_hint: 'revert stage' }, taint: { taint: true, taint_reason: 'mcp' },
        bubble: { visible: true, rule: '缺 undo_hint 不出现审批气泡（§7）', undo_hint: 'revert stage', compensating_action: '' } },
    ],
    batch_groups: [{ batch_key: 'stage.promote|L1|low', object_type: 'stage.promote', level: 'L1',
      risk: 'low', count: 2, record_ids: ['r1', 'r2'], bubble_visible_count: 1 }],
    decision_contract: { operation_human_only: '§7.0', note: '无旁路' },
  }

  it('按后端 batch_key 分组显示"一键批"入口', async () => {
    api.fetchApprovalInbox.mockResolvedValue(inbox)
    render(<GovernancePanels panel="approvals" />)
    expect(await screen.findByText(/stage\.promote · L1 · 风险 low（2）/)).toBeInTheDocument()
  })

  it('★ 缺 undo_hint 的待审记录标注"不出气泡"（规则来自后端）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(inbox)
    render(<GovernancePanels panel="approvals" />)
    expect((await screen.findAllByText('不出气泡')).length).toBeGreaterThan(0)
  })

  it('外来内容显示 TaintBadge', async () => {
    api.fetchApprovalInbox.mockResolvedValue(inbox)
    render(<GovernancePanels panel="approvals" />)
    expect(await screen.findByText('外来内容')).toBeInTheDocument()
  })

  it('批量批准：先签发批量链接再提交（两段式，逐条绑定）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(inbox)
    api.batchLink.mockResolvedValue({ ok: true, batch_id: 'batch-1', record_ids: ['r1', 'r2'],
      batch_key: 'stage.promote|L1|low', ttl_seconds: 900, one_time_per_record: true, note: '' })
    api.batchDecide.mockResolvedValue({ ok: true, decision: 'approve', batch_id: 'batch-1',
      requested: 2, succeeded: 2, failed: 0, batch_key: 'stage.promote|L1|low', results: [], note: '' })
    render(<GovernancePanels panel="approvals" />)
    fireEvent.click(await screen.findByText(/stage\.promote · L1 · 风险 low（2）/))
    fireEvent.click(screen.getByText(/批准所选（2）/))
    await waitFor(() => expect(api.batchLink).toHaveBeenCalledWith(['r1', 'r2']))
    expect(api.batchDecide).toHaveBeenCalledWith(expect.objectContaining({
      batch_id: 'batch-1', decision: 'approve',
    }))
    expect(await screen.findByText(/成功 2 \/ 2/)).toBeInTheDocument()
  })

  it('驳回必须填理由（前端先行拦截）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(inbox)
    render(<GovernancePanels panel="approvals" />)
    fireEvent.click(await screen.findByText(/stage\.promote · L1 · 风险 low（2）/))
    fireEvent.click(screen.getByText(/驳回所选（2）/))
    expect(await screen.findByText(/驳回必须填写理由/)).toBeInTheDocument()
    expect(api.batchDecide).not.toHaveBeenCalled()
  })
})

describe('4. ROI 面板（★ U8 双口径）', () => {
  const roi = {
    ok: true, panel: { ...PANEL, panel: 'roi', priority: 'P1' }, generated_at: 't',
    daily: { date: '2026-09-12', cost_normalized_cents: M(120.5, 'cents'),
      cost_effective_cents: M(130, 'cents'), daily_budget_cents: M(500, 'cents'),
      over_budget: false, ratio: M(0.9, 'ratio'), cost_schema_version: 'utc.v1',
      calibration_version: 'price_anchor.v1', source_of_truth: 'events' },
    utc_weekly: {}, utc_snapshot: {},
    approval_decay: { decay_rate: M(0.42, 'rate', { sample_size: 4, insufficient_sample: true, disclosure_only: true }),
      target: M(0.7, 'rate'), meets_target: false, auto_disposed: 4, human_disposed: 6,
      total_disposed: 10, insufficient_sample: true, min_sample: 20, fatigue_buckets: { quick: 3 },
      latency_median_ms: M(1200, 'ms'), disclosure_only: true, note: '披露不考核' },
    slo_metrics: { schema: 'eval.slo_weekly.v1', window: {}, traceability: {},
      metrics: {
        approval_decay_rate: { key: 'approval_decay_rate', name: '审批衰减率', value: 0.42, unit: '比率',
          numerator: 4, denominator: 10, samples: 10, status: 'insufficient_samples',
          definition: '', formula: 'a/b', source: 'cost_brake', target: '≥70%', computable: true,
          min_samples: 20, target_met: false },
        delegation_recovery: { key: 'delegation_recovery', name: '委派回收率', value: 0.5, unit: '比率',
          numerator: 1, denominator: 2, samples: 2, status: 'insufficient_samples',
          definition: '', formula: 'a/b', source: 'S4-04 行契约', target: '100%', computable: false,
          min_samples: 20, target_met: false },
      } },
    acr_daily: {}, acr_weekly: {},
    policy_latency: {
      decision_body: M(0.0474, 'ms', { note: '**不得**与全量埋点口径混用/相减/平均（U8）' }),
      full_instrumentation: { hit_ms: 3.764, miss_ms: 7.97, source: 'TASK-S4-02_验收报告',
        note: '含链式审计+埋点的端到端耗时' },
      comparability: 'two_scopes_not_interchangeable',
    },
  }

  it('两个延迟口径并列渲染且标注不可混用', async () => {
    api.fetchRoi.mockResolvedValue(roi)
    render(<GovernancePanels panel="roi" />)
    expect(await screen.findByText(/two_scopes_not_interchangeable|不可互换/)).toBeInTheDocument()
    expect(screen.getByText('0.047 ms')).toBeInTheDocument()
    expect(screen.getByText(/3.764 ms \/ 7.97 ms/)).toBeInTheDocument()
  })

  it('§6.7 指标字典含 U10 委派回收率行', async () => {
    api.fetchRoi.mockResolvedValue(roi)
    render(<GovernancePanels panel="roi" />)
    expect(await screen.findByText('委派回收率')).toBeInTheDocument()
    expect(screen.getAllByText('insufficient_samples').length).toBeGreaterThan(0)
  })

  it('审批衰减率标"披露不考核"', async () => {
    api.fetchRoi.mockResolvedValue(roi)
    render(<GovernancePanels panel="roi" />)
    expect((await screen.findAllByText(/披露不考核/)).length).toBeGreaterThan(0)
  })

  it('日成本视图缺位时给出 absent 说明而非 0', async () => {
    api.fetchRoi.mockResolvedValue({ ...roi, daily: { value: null, available: false, reason: 'cost_brake 不可用' } })
    render(<GovernancePanels panel="roi" />)
    expect(await screen.findByText(/cost_brake 不可用/)).toBeInTheDocument()
  })
})

describe('5. 自愈事故面板', () => {
  const incidents = {
    ok: true, panel: { ...PANEL, panel: 'incident', priority: 'P1' }, generated_at: 't',
    load_error: '', auto_expand: true, auto_expand_rule: '存在 status=open 的事故卡即自动展开（§7）',
    open_count: M(1), total_count: M(1),
    incidents: [{ id: 'inc-1', severity: 'L4', root_cause: 'rc', fatal_change: 'deadbeef',
      evasion_rule: 'policy:1', in_strategy_memory: { yes: true, id: 'm1' },
      regression_case_added: { yes: true, case_id: 'c1' }, trace_ids: ['t1'], mttd_ms: 120,
      mttr_ms: 300, status: 'open', tenant_id: 'default', created_at: '2026-09-12T09:00:00',
      resolved_at: '', missing_elements: [], detail: {} }],
    mttd_ms: { events: 1, by_level: { L4: 1 }, mttd_ms: M(120, 'ms'), mttr_ms: M(300, 'ms'),
      mttd_samples: 1, mttr_samples: 1, note: '缺该字段的事件不计入，不补 0' },
    backup_health: { source: 'agent/disaster_recovery.py', config: { enabled: true, backup_dir: '/vault' },
      backup_count: M(3), latest_backup: { backup_id: 'b1', timestamp: 't', size: 100 },
      recovery_status: { status: 'idle' }, scheduler_running: true,
      event_note: 'backup.health 无发射方' },
  }

  it('有 open 事故 ⇒ 自动展开并显示横幅', async () => {
    api.fetchIncidents.mockResolvedValue(incidents)
    render(<GovernancePanels panel="incidents" />)
    expect(await screen.findByText(/面板自动展开/)).toBeInTheDocument()
    expect(await screen.findByText('inc-1')).toBeInTheDocument()
  })

  it('事故卡六要素齐备标注（§3）', async () => {
    api.fetchIncidents.mockResolvedValue(incidents)
    render(<GovernancePanels panel="incidents" />)
    expect(await screen.findByText('六要素齐备')).toBeInTheDocument()
  })

  it('无事故时不自动展开', async () => {
    api.fetchIncidents.mockResolvedValue({
      ...incidents, auto_expand: false, open_count: M(0), total_count: M(0), incidents: [],
    })
    render(<GovernancePanels panel="incidents" />)
    await screen.findByText(/自愈事故/)
    expect(screen.queryByText(/面板自动展开/)).toBeNull()
  })

  it('MTTD/MTTR 缺位渲染 "—"（不补 0）', async () => {
    api.fetchIncidents.mockResolvedValue({
      ...incidents,
      mttd_ms: { ...incidents.mttd_ms, mttd_ms: M(null, 'ms'), mttr_ms: M(null, 'ms'),
        mttd_samples: 0, mttr_samples: 0, events: 0 },
    })
    render(<GovernancePanels panel="incidents" />)
    await screen.findByText(/自愈事故/)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})

describe('6. 记忆技能库面板（★ U5 优先级契约）', () => {
  const memory = {
    ok: true, panel: { ...PANEL, panel: 'memory_skills', priority: 'P2' }, generated_at: 't',
    recall_priority: { source: 'agent/memory/taxonomy.py::recall_priority_key()',
      order: ['strategy', 'fact', 'preference', 'working'],
      formula: '策略 > 事实 > 偏好（P7.2-10）', key_fields: 'recall_priority_key(entry)',
      contract: '组装注入（P7.2-10）按本顺序；本面板只呈现，不改变召回结果', callable: true },
    layers: { by_layer: { fact: 2 }, total: M(2), entries: [
      { memory_id: 'm1', layer: 'fact', tenant_id: 'default', subject_id: 's', scope: 'project:x',
        scope_kind: 'project', content_redacted: '脱敏后的内容', content_hash: 'h', confidence: 0.8,
        created_at: 1, ttl_expires_at: null, ttl_seconds: null, forget_candidate: false,
        degraded: false, degradation_reason: '', org_level: false, source_capability_id: '', schema_version: 1 },
    ] },
  }

  it('优先级契约来自后端并上屏（策略 > 事实 > 偏好）', async () => {
    api.fetchMemorySkills.mockResolvedValue(memory)
    render(<GovernancePanels panel="memory" />)
    expect(await screen.findByText(/recall_priority_key\(\)/)).toBeInTheDocument()
    expect(screen.getByText('strategy')).toBeInTheDocument()
    expect(screen.getByText(/组装注入（P7.2-10）/)).toBeInTheDocument()
  })

  it('条目显示脱敏文本（不显示原文）', async () => {
    api.fetchMemorySkills.mockResolvedValue(memory)
    render(<GovernancePanels panel="memory" />)
    expect(await screen.findByText('脱敏后的内容')).toBeInTheDocument()
  })
})

describe('7. 审计导出面板', () => {
  const audit = {
    ok: true, panel: { ...PANEL, panel: 'audit_export' }, generated_at: 't', load_error: '',
    filters: {}, count: 2, exported: M(2),
    verify: { ok: false, checked: 16, first_bad_seq: 17,
      bad_seqs: [{ seq: 17, reason: 'prev_hash_mismatch', detail: '链断裂' }],
      reason: 'prev_hash_mismatch', detail: 'prev_hash ≠ 上一条重算 self_hash' },
    verify_scope: 'exported', elapsed_ms: M(266.5, 'ms'),
    chain_head: { db_path: '/db', count: 19615, first_seq: 1, last_seq: 19619, head_self_hash: 'f57', degraded: false },
    entries: [
      { seq: 1, ts: '2026-09-12T09:00:00', actor: 'ui:1', action: 'a', subject: 's',
        source: 'ui', status: 'success', payload_hash: 'p', prev_hash: 'g', self_hash: 'abcdef1234' },
    ],
    disclosure: '导出含验签摘要',
  }

  it('验签摘要上屏（ok / first_bad_seq / checked）', async () => {
    api.fetchAuditExport.mockResolvedValue(audit)
    render(<GovernancePanels panel="audit" />)
    expect(await screen.findByText('验签失败')).toBeInTheDocument()
    expect(screen.getByText(/首个异常 seq=/)).toBeInTheDocument()
    expect(screen.getByText(/checked=16/)).toBeInTheDocument()
  })

  it('验签通过时显示绿色摘要', async () => {
    api.fetchAuditExport.mockResolvedValue({
      ...audit, verify: { ...audit.verify, ok: true, first_bad_seq: null, bad_seqs: [], reason: 'ok' },
    })
    render(<GovernancePanels panel="audit" />)
    expect(await screen.findByText('验签通过')).toBeInTheDocument()
  })
})

/**
 * 在 Shadow DOM 自治单元内按可见文本查按钮
 *
 * ★ 为什么必须这样查：审批/动作按钮区渲染在 **Shadow DOM** 里（U2），
 *   `screen.*` 只遍历 light DOM，因此查不到——这本身就是 DOM 隔离生效的证据。
 */
function buttonInShadow(text: string): HTMLButtonElement {
  const hosts = Array.from(document.querySelectorAll('[data-cp-approval-zone="true"]'))
  for (const host of hosts) {
    const found = Array.from((host.shadowRoot?.querySelectorAll('button') || []))
      .find((b) => (b.textContent || '').includes(text))
    if (found) return found as HTMLButtonElement
  }
  throw new Error(`影子树内未找到按钮：${text}`)
}

describe('八. 七动作 + 永不自动化五类确认 UI', () => {
  const emptyInbox = {
    ok: true, panel: { ...PANEL, panel: 'approval_inbox' }, generated_at: 't', count: 0,
    pending_total: M(0), pending_by_type: {}, bubble_hidden: M(0), items: [], batch_groups: [],
    decision_contract: {},
  }

  it('动作按钮渲染在 Shadow DOM 内（DOM 隔离，U2）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(emptyInbox)
    render(<GovernancePanels panel="approvals" />)
    fireEvent.click(await screen.findByText(/七动作（对所选记录/))
    await waitFor(() => {
      const hosts = document.querySelectorAll('[data-cp-approval-zone="true"]')
      expect(hosts.length).toBeGreaterThan(0)
      expect((hosts[0] as HTMLElement).shadowRoot).toBeTruthy()
    })
    // light DOM 里查不到"熔断"，影子树里查得到
    expect(screen.queryByText('熔断')).toBeNull()
    expect(buttonInShadow('熔断')).toBeTruthy()
  })

  it('类别词表取自后端常量（未在前端硬编码五类）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(emptyInbox)
    render(<GovernancePanels panel="approvals" />)
    fireEvent.click(await screen.findByText(/七动作（对所选记录/))
    await waitFor(() => expect(buttonInShadow('永不自动化五类')).toBeTruthy())
    fireEvent.click(buttonInShadow('永不自动化五类'))
    // labels 来自后端（转账/发布/删库/改权限/push --force）
    expect(await screen.findByText(/转账（transfer）/)).toBeInTheDocument()
    expect(screen.getByText(/accepts_text_approval=false/)).toBeInTheDocument()
    expect(screen.getByText(/60s 时效/)).toBeInTheDocument()
  })

  it('签发凭据后显示 60s 倒计时（单次 + 时效）', async () => {
    api.fetchApprovalInbox.mockResolvedValue(emptyInbox)
    api.issueConfirmation.mockResolvedValue({
      ok: true, action: 'transfer',
      confirmation: { category: 'transfer', action_digest: 'd', issued_at: Date.now() / 1000,
        expires_at: Date.now() / 1000 + 60, remaining_seconds: 60, ttl_seconds: 60, used: false,
        expired: false, issued_by: 'ui', approval_record_id: '', token: 'bwc-abc' },
      action_digest: 'd', hits: [], max_ttl_seconds: 60, single_action_bound: true,
      accepts_text_approval: false, single_use: true, note: '',
    })
    render(<GovernancePanels panel="approvals" />)
    fireEvent.click(await screen.findByText(/七动作（对所选记录/))
    await waitFor(() => expect(buttonInShadow('永不自动化五类')).toBeTruthy())
    fireEvent.click(buttonInShadow('永不自动化五类'))
    fireEvent.click(await screen.findByText(/1\) 显式确认/))
    await waitFor(() => expect(api.issueConfirmation).toHaveBeenCalled())
    expect(await screen.findByText(/剩余 \d+\.\ds/)).toBeInTheDocument()
    // token 只用于回传，**不声明 actor_type**（与 Flask 侧审批台同纪律）
    const body = api.issueConfirmation.mock.calls[0][1]
    expect(body).not.toHaveProperty('actor_type')
    expect(body).not.toHaveProperty('actor')
  })
})
