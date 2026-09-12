/**
 * 治理可观测面板（v7.2 §7 六面板）— 类型契约
 * ------------------------------------------------------------------
 * 与后端 `agent/server_routes/routes_ui_panels.py` 的 JSON 一一对应。
 *
 * 【口径纪律（§0.3，前端强约束）】
 *   1. 每个上屏数字都是 `Metric`：带 `source` / `formula` / `unit`，
 *      `value === null` 表示**数据源缺位**（渲染"—"，**绝不渲染 0**）；
 *   2. `insufficient_sample` 为真 ⇒ 只披露不考核（渲染"仅披露"角标，
 *      不得据此下结论、不得把它做成"达标/未达标"的判定）；
 *   3. 不可追溯的百分比禁止上屏 —— 前端不自行计算任何比率，
 *      一律消费后端的 `Metric`。
 */

/** 可追溯指标（后端 `schema.metric()` 的产物） */
export interface Metric {
  /** 指标值；`null` = 数据源缺位（**不是 0**） */
  value: number | null;
  available: boolean;
  /** 数据源（文件/模块::函数） */
  source: string;
  /** 计算口径（可复算的公式） */
  formula: string;
  unit: string;
  /** 数据集/窗口标识 */
  dataset: string;
  note: string;
  sample_size?: number;
  min_sample?: number;
  insufficient_sample?: boolean;
  disclosure_only?: boolean;
  /** 是否满足"可追溯"（无 source 的比率会被后端标 false） */
  traceable?: boolean;
}

/** 缺位占位（后端 `schema.absent()` 的产物） */
export interface Absent {
  value: null;
  available: false;
  reason: string;
}

/** 面板元信息（优先级 + 数据源台账） */
export interface PanelMeta {
  panel: string;
  priority: 'P0' | 'P1' | 'P2';
  datasources: string[];
  min_sample: number;
  min_sample_source: string;
  disclosure_note: string;
}

export interface PanelsIndex {
  ok: true;
  prefix: string;
  panels: PanelMeta[];
  priority_order: Record<string, string>;
  endpoints: Record<string, string>;
}

/** 面板总包（`GET /api/cp/panels`）的枚举名 */
export type PanelId =
  | 'digestion_pipeline'
  | 'capability_map'
  | 'approval_inbox'
  | 'roi'
  | 'incident'
  | 'memory_skills'

// ═══════════════════════════════════════════════════════════
//  1. 消化流水线
// ═══════════════════════════════════════════════════════════

export interface DigestEventCard {
  event_id: string;
  ts: string;
  actor: string;
  capability_id: string;
  from_stage: string | null;
  to_stage: string | null;
  applied: boolean;
  verdict: string;
  scope: string;
  passport_id: string;
  pass_rate: number | null;
  rank_score: number | null;
  manual_required: boolean | null;
  reasons: string[];
  digest_run_id: string;
  trace_id: string;
}

export interface PipelineLane {
  lane: string;
  title: string;
  event_count: Metric;
  items: DigestEventCard[];
  truncated: boolean;
}

export interface ShadowCard {
  capability_id: string;
  generated_at: number | null;
  allowed: boolean;
  budget: number | null;
  sampled: number;
  passed: number | null;
  negative: number | null;
  judge_kind: string;
  degradation: string;
  shadow_version: string;
  p99_wall_candidate_ms: Metric;
  p99_wall_upstream_ms: Metric;
  pass_rate: Metric;
}

export interface ConditionRow {
  name: string;
  dimension: 'veto' | 'rank' | string;
  passed: boolean;
  actual: number | null;
  threshold: number | null;
  comparator: string;
  score: number | null;
  evidence_source: string;
  reasons: string[];
}

export interface DecisionCard {
  capability_id: string;
  pr_id: string;
  pr_dir: string;
  verdict: string;
  blocker: string;
  stage: string;
  passport_id: string;
  rank_score: number | null;
  promotable: boolean;
  manual_required: boolean;
  manual_label: string;
  veto_failed: string[];
  rank_failed: string[];
  generated_at: number | null;
  engine_version: string;
  audit_seq: number | null;
  audit_hash: string;
  conditions: ConditionRow[];
  roi: {
    monthly_saving_cents: Metric;
    one_time_investment_cents: Metric;
    amortized_monthly_cents: Metric;
    net_monthly_cents: Metric;
    positive: boolean | null;
    monthly_samples: number | null;
    caveats: string[];
    assumptions: string[];
  };
}

export interface ManualReviewSummary {
  sampled?: number;
  pending?: number;
  decided?: number;
  by_verdict?: Record<string, number>;
  human_reviewed?: number;
  agent_assisted_reviewed?: number;
  closed?: boolean;
  pending_case_ids?: string[];
  path?: string;
  note?: string;
  available?: boolean;
  value?: null;
  reason?: string;
}

export interface PipelineView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  lanes: PipelineLane[];
  shadow: ShadowCard[];
  internalize: DecisionCard[];
  manual_review: ManualReviewSummary;
  clock: { wall: string; note: string };
  summary: {
    digest_stage_events: Metric;
    capabilities_touched: Metric;
    applied_migrations: Metric;
    shadow_runs: Metric;
    internalize_decisions: Metric;
    internalize_rate: Metric;
    stage_distribution: Record<string, number>;
  };
}

// ═══════════════════════════════════════════════════════════
//  2. 能力地图
// ═══════════════════════════════════════════════════════════

export interface CapabilityCard {
  capability_id: string;
  name: string;
  description: string;
  source_type: string;
  source_id: string;
  provenance: string;
  risk_level: string | null;
  data_class: string | null;
  requires_approval: boolean;
  stage: string | null;
  audit_level: string;
  has_undo_hint: boolean;
  has_compensating_action: boolean;
  idempotent: boolean | null;
  timeout_ms: number | null;
  external_endpoint: boolean | null;
  variant_count: number | null;
  aliases: string[];
  platform: string;
  updated_at: string | null;
  success_rate: Metric;
  sample_count: number | null;
  sample_discipline: {
    sample_size: number;
    min_sample: number;
    insufficient_sample: boolean;
    disclosure_only: boolean;
    note: string;
  } | null;
  p99_latency_ms: Metric;
  /** true 才允许出现审批气泡（缺 undo_hint 且无补偿动作 ⇒ false，§7） */
  approval_bubble_eligible: boolean;
}

export interface CapabilityMapView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  source: string;
  load_error: string;
  total: Metric;
  matched: Metric;
  pagination: { total: number; offset: number; limit: number; has_more: boolean };
  distribution: {
    by_provenance: Record<string, number>;
    by_risk: Record<string, number>;
    by_data_class: Record<string, number>;
    by_stage: Record<string, number>;
    by_platform: Record<string, number>;
  };
  items: CapabilityCard[];
}

// ═══════════════════════════════════════════════════════════
//  3. 审批收件箱
// ═══════════════════════════════════════════════════════════

export interface ApprovalGovernance {
  undo_hint?: string;
  compensating_action?: string;
  undo_hint_status?: string;
}

export interface ApprovalItem {
  record_id: string;
  object_type: string;
  object_id: string;
  level: string;
  action: string;
  description: string;
  actor: string;
  actor_type: string;
  manual_required: boolean;
  created_at: string;
  risk: string | null;
  /** 同策略同风险分组键（`object_type|level|risk`）——一键批的唯一依据 */
  batch_key: string;
  governance: ApprovalGovernance;
  taint: { taint?: boolean; taint_reason?: string };
  /** 审批气泡可见性由**后端**判定，前端不得放宽 */
  bubble: {
    visible: boolean;
    rule: string;
    undo_hint: string;
    compensating_action: string;
  };
}

export interface BatchGroup {
  batch_key: string;
  object_type: string;
  level: string;
  risk: string;
  count: number;
  record_ids: string[];
  bubble_visible_count: number;
}

export interface ApprovalInboxView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  count: number;
  pending_total: Metric;
  pending_by_type: Record<string, number>;
  bubble_hidden: Metric;
  items: ApprovalItem[];
  batch_groups: BatchGroup[];
  decision_contract: Record<string, string>;
  error?: string;
}

export interface BatchDecisionResult {
  ok: boolean;
  decision: 'approve' | 'reject';
  batch_id: string;
  requested: number;
  succeeded: number;
  failed: number;
  batch_key: string;
  results: Array<Record<string, unknown> & { record_id: string; ok: boolean }>;
  note: string;
}

// ═══════════════════════════════════════════════════════════
//  4. ROI / 成本
// ═══════════════════════════════════════════════════════════

export interface SloMetricRow {
  key: string;
  name: string;
  value: number | null;
  unit: string;
  numerator: number | null;
  denominator: number | null;
  samples: number;
  status: string;
  definition: string;
  formula: string;
  source: string;
  target: string;
  computable: boolean;
  min_samples: number;
  target_met: boolean | null;
  disclosure?: string;
  incomplete?: string[];
  contract?: Record<string, unknown>;
}

export interface RoiView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  daily: Record<string, unknown> | Absent;
  utc_weekly: Record<string, unknown> | Absent;
  utc_snapshot: Record<string, unknown> | Absent;
  approval_decay: {
    decay_rate?: Metric;
    target?: Metric;
    meets_target?: boolean | null;
    auto_disposed?: number;
    human_disposed?: number;
    total_disposed?: number;
    insufficient_sample?: boolean;
    min_sample?: number;
    fatigue_buckets?: Record<string, number>;
    latency_median_ms?: Metric;
    disclosure_only?: boolean;
    note?: string;
    available?: boolean;
    value?: null;
    reason?: string;
  };
  slo_metrics?: {
    schema: string;
    window: Record<string, unknown>;
    traceability: Record<string, unknown>;
    metrics: Record<string, SloMetricRow>;
  } | Absent;
  acr_daily: Record<string, unknown> | Absent;
  acr_weekly: Record<string, unknown> | Absent;
  /** ★ U8：策略延迟**两个口径并列**，不得混用 */
  policy_latency: {
    decision_body: Metric;
    full_instrumentation: {
      hit_ms: number;
      miss_ms: number;
      source: string;
      note: string;
    };
    comparability: 'two_scopes_not_interchangeable';
  };
}

// ═══════════════════════════════════════════════════════════
//  5. 自愈事故
// ═══════════════════════════════════════════════════════════

export interface IncidentCardData {
  id: string;
  severity: string;
  root_cause: string;
  fatal_change: string;
  evasion_rule: string;
  in_strategy_memory: { yes?: boolean; id?: string };
  regression_case_added: { yes?: boolean; case_id?: string };
  trace_ids: string[];
  mttd_ms: number | null;
  mttr_ms: number | null;
  status: 'open' | 'resolved' | string;
  tenant_id: string;
  created_at: string;
  resolved_at: string;
  /** 六要素缺失项（空数组 = 齐备，才可 resolved） */
  missing_elements: string[];
  detail: Record<string, unknown>;
}

export interface HealingLatency {
  events?: number;
  by_level?: Record<string, number>;
  mttd_ms?: Metric;
  mttr_ms?: Metric;
  mttd_samples?: number;
  mttr_samples?: number;
  note?: string;
  available?: boolean;
  value?: null;
  reason?: string;
}

export interface IncidentsView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  load_error: string;
  /** 有 open 事故 ⇒ 自动展开（§7） */
  auto_expand: boolean;
  auto_expand_rule: string;
  open_count: Metric;
  total_count: Metric;
  incidents: IncidentCardData[];
  mttd_ms: HealingLatency;
  backup_health: Record<string, unknown> | Absent;
}

// ═══════════════════════════════════════════════════════════
//  6. 记忆 / 技能库
// ═══════════════════════════════════════════════════════════

export interface MemoryCardData {
  memory_id: string;
  layer: string;
  tenant_id: string;
  subject_id: string;
  scope: string;
  scope_kind: string;
  content_redacted: string;
  content_hash: string;
  confidence: number | null;
  created_at: number | null;
  ttl_expires_at: number | null;
  ttl_seconds: number | null;
  forget_candidate: boolean;
  degraded: boolean;
  degradation_reason: string;
  org_level: boolean;
  source_capability_id: string;
  schema_version: number | null;
}

export interface MemorySkillsView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  /** ★ U5：召回优先级契约（策略 > 事实 > 偏好），单一来源为后端 taxonomy */
  recall_priority:
    | {
        source: string;
        order: string[];
        formula: string;
        key_fields: string;
        contract: string;
        callable: boolean;
      }
    | Absent;
  layers:
    | {
        by_layer: Record<string, number>;
        entries: MemoryCardData[];
        total: Metric;
      }
    | Absent;
}

// ═══════════════════════════════════════════════════════════
//  7. 观测流 / 越权告警 / 安全渲染 / 审计导出
// ═══════════════════════════════════════════════════════════

export interface ObservabilityStreamView {
  ok: boolean;
  generated_at: string;
  acr: Record<string, unknown> | Absent;
  utc: Record<string, unknown> | Absent;
  model_degrade: {
    total?: Metric;
    edges?: Record<string, number>;
    reasons?: Record<string, number>;
    fallback_attempted?: number;
    fallback_succeeded?: number;
    chain?: { models: string[]; source: string; enabled: boolean };
    error_code?: string;
    available?: boolean;
    value?: null;
    reason?: string;
  };
  escape: {
    total?: Metric;
    by_path?: Record<string, number>;
    by_reason?: Record<string, number>;
    ledger?: string;
    watched?: string[];
    available?: boolean;
    value?: null;
    reason?: string;
  };
}

export interface AuthzAlertsView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  realtime: {
    stats: Record<string, unknown> | Absent;
    /** 进程内计数：重启归零（口径必须上屏） */
    volatile: boolean;
    note: string;
  };
  durable: {
    window_days?: number;
    total?: Metric;
    by_source_key?: Record<string, number>;
    by_operation?: Record<string, number>;
    recent?: Array<{
      ts: string;
      actor: string;
      actor_type: string | null;
      operation: string;
      object_type: string | null;
      object_id: string | null;
      record_id: string | null;
      denied_by_matrix: boolean;
      reason: string;
      source_key: string;
    }>;
    pii_note?: string;
    available?: boolean;
    value?: null;
    reason?: string;
  };
  thresholds: Record<string, string>;
}

/** ★ U1：前端常量**唯一来源**（不得自定义） */
export interface SecurityRenderState {
  ok: boolean;
  generated_at: string;
  safe_render: {
    allowed_tags: string[];
    csp: Record<string, string>;
    iframe_sandbox: string;
    image_proxy_prefix: string;
    system_slots: string[];
    taint_badge: {
      class: string;
      base_class: string;
      attr: string;
      always_has_background: boolean;
    };
    approval_zone: {
      class: string;
      style: Record<string, string>;
      shadow_root: boolean;
    };
  };
  boundary_words: {
    enabled: boolean;
    /** §7 永不自动化五类（逐字来自后端常量） */
    never_automated: string[];
    labels: Record<string, string>;
    ttl_seconds: number;
    max_ttl_seconds: number;
    single_action_bound: boolean;
    accepts_text_approval: false;
    store: Record<string, unknown>;
  };
  injection_defense: Record<string, unknown>;
  frontend_contract: { must_not_customize: string[]; rule: string };
}

export interface AuditEntryRow {
  seq: number;
  ts: string;
  actor: string;
  action: string;
  subject: string;
  source: string;
  status: string;
  payload_hash: string;
  prev_hash: string;
  self_hash: string;
  trace_id?: string;
  task_id?: string;
  workspace_id?: string;
  subject_id?: string;
}

export interface AuditExportView {
  ok: boolean;
  panel: PanelMeta;
  generated_at: string;
  load_error: string;
  filters: Record<string, unknown>;
  count: number;
  exported: Metric;
  verify: {
    ok: boolean;
    checked: number;
    first_bad_seq: number | null;
    bad_seqs: Array<{ seq: number; reason: string; detail: string }>;
    reason: string;
    detail: string;
    anchor_seq?: number | null;
    head_seq?: number;
    head_self_hash?: string;
  };
  verify_scope: 'exported' | 'full' | 'head';
  elapsed_ms: Metric;
  chain_head: {
    db_path?: string;
    count?: number;
    first_seq?: number;
    last_seq?: number;
    head_self_hash?: string;
    last_ts?: string;
    degraded?: boolean;
  };
  entries: AuditEntryRow[];
  disclosure: string;
}

/** 凭据签发结果（§5.7 机制 5：单次 + 60s） */
export interface ConfirmationIssue {
  ok: boolean;
  action: string;
  confirmation: {
    category: string;
    action_digest: string;
    issued_at: number;
    expires_at: number;
    remaining_seconds: number;
    ttl_seconds: number;
    used: boolean;
    expired: boolean;
    issued_by: string;
    approval_record_id: string;
    /** token **只在此刻返回一次** */
    token: string;
  };
  action_digest: string;
  hits: Array<Record<string, unknown>>;
  max_ttl_seconds: number;
  single_action_bound: boolean;
  accepts_text_approval: false;
  single_use: boolean;
  note: string;
}

export interface ActionResponse {
  ok: boolean;
  action: string;
  target?: string;
  authorized?: boolean;
  decision?: {
    allowed: boolean;
    reason: string;
    operation: string;
    actor_type: string;
    matrix_hit: boolean;
    denied_by_matrix: boolean;
    requires_second_factor?: boolean;
    requires_reason?: boolean;
  };
  boundary?: Record<string, unknown> | null;
  intent?: Record<string, unknown>;
  audit?: { seq?: number; self_hash?: string };
  executor?: string;
  level?: string;
  requires_approval?: boolean;
  dry_run?: boolean;
  plan?: Record<string, unknown>;
  read_only?: boolean;
  capability_id?: string;
  current_stage?: string | null;
  governance?: Record<string, unknown>;
  stage_events?: Array<Record<string, unknown>>;
  executed?: boolean;
  submitted_for_approval?: boolean;
  note?: string;
  code?: string;
  message?: string;
}

/** 七动作（UI 侧动作名 → 后端 `<action>` 路径段） */
export type ActionName =
  | 'circuit_break'
  | 'rollback'
  | 'degrade'
  | 'remove_source'
  | 'approve'
  | 'reject'
  | 'trace_diff'
  | 'switch_forge'
  // 永不自动化五类（URL 用连字符；拿确认也**不执行**，只转审批提案）
  | 'publish'
  | 'transfer'
  | 'drop-database'
  | 'permission-change'
  | 'force-push'

// ═══════════════════════════════════════════════════════════════
//  开关中心（TASK-S7-01）—— `GET /api/cp/settings` 契约镜像
// ═══════════════════════════════════════════════════════════════

/**
 * 生效来源（**契约逐字**：`env | ui_override | config | default`）
 *
 * 这是"这个值现在到底谁说了算"的唯一答案；`shadowed_by` 是**存在但未生效**的
 * 低优先级来源（`source=env` + `shadowed_by=["ui_override","config"]` ⇒
 * UI 覆盖项存在，但 env 赢）。
 */
export type SettingsSource = 'env' | 'ui_override' | 'config' | 'default';

/** 风险分级（A 可直接切 / B 需第二位人工确认 / C 只读脱敏） */
export type SettingsRisk = 'A' | 'B' | 'C';

export interface SettingsItem {
  key: string;
  category: string;
  category_label: string;
  /** 值类型（bool / int / float / str / list / …由后端登记表给出） */
  type: string;
  risk: SettingsRisk | string;
  risk_label: string;
  /** `secret=true` 时后端送 `null`（**永不返回明文**） */
  value: unknown;
  display_value: string;
  default: unknown;
  source: SettingsSource | string;
  /** 来源的人类可读名（如 `config.yaml` / `环境变量`） */
  source_label: string;
  /** 存在但**未生效**的低优先级来源 */
  shadowed_by: string[];
  locked: boolean;
  /** `locked=true` 时必为非空中文句子，**必须显示在禁用控件旁** */
  locked_reason: string;
  /** `locked=true ⇒ editable=false` */
  editable: boolean;
  effect: string;
  effect_label: string;
  needs_restart: boolean;
  env_name: string;
  env_present: boolean;
  config_path: string;
  /** 仅支持环境变量（无 config.yaml 项） */
  env_only: boolean;
  secret: boolean;
  masked: boolean;
  /** 机密项"是否已配置"（密文串由后端给，前端**不得自行拼装**） */
  configured: boolean;
  description: string;
  owner_module: string;
  validator: { kind: string } & Record<string, unknown>;
  requires_second_factor: boolean;
  requires_dual_approval: boolean;
  impact: string;
  rollback: string;
}

export interface SettingsCategory {
  id: string;
  label: string;
  count: number;
}

export interface SettingsCounts {
  total: number;
  by_category: Record<string, number>;
  by_risk: Record<string, number>;
  editable: number;
  locked: number;
  overridden: number;
}

export interface SettingsView {
  ok: boolean;
  prefix: string;
  generated_at: string;
  /** 优先级从高到低（后端给；前端**不得自定义**） */
  source_priority: string[];
  counts: SettingsCounts;
  categories: SettingsCategory[];
  items: SettingsItem[];
  read_only_notice: string;
}

/**
 * 变更 / 重置响应（200 已生效 或 202 待第二人确认）
 *
 * 202：`{ ok:true, applied:false, pending:true, pending_id, requires_dual_approval:true, message }`
 * 200：`{ ok:true, applied:true, key, old, new, source:"ui_override", effect, effect_label, audit, receipt, item }`
 *
 * ★ 实测补充（`agent/settings/service.py::ChangeOutcome.to_dict`）：生效分支**没有**顶层
 *   `value` / `display_value`，新值与展示值在 `item` 里（`item.display_value`）；
 *   重置成功的标记在 `receipt.reset === true`（另有一版契约为顶层 `reset`）。
 *   两个形状都接受 ⇒ 前端对两侧都不脆。
 */
export interface SettingsChangeResponse {
  ok: boolean;
  applied?: boolean;
  pending?: boolean;
  pending_id?: string;
  requires_dual_approval?: boolean;
  /** 后端原文（如"B 级开关需第二位人工确认后方可生效"）；前端**不得改写** */
  message?: string;
  key?: string;
  old?: unknown;
  new?: unknown;
  source?: SettingsSource | string;
  effect?: string;
  effect_label?: string;
  audit?: { seq?: number; self_hash?: string };
  receipt?: Record<string, unknown>;
  /** 生效/待确认后的条目快照（含 `display_value` / `source` / `shadowed_by`） */
  item?: SettingsItem;
  value?: unknown;
  display_value?: string;
  reset?: boolean;
  code?: string;
}

/** 变更请求体（`POST /api/cp/settings/<key>`；数组体一律被后端拒绝——**无批量端点**） */
export interface SettingsChangeBody {
  value: unknown;
  second_factor?: string;
  reason?: string;
  /** 仅在与既有待确认项关联时携带（正常链路由 202 响应给出） */
  pending_id?: string;
}

/** 二次确认请求体（`POST /api/cp/settings/<key>/confirm`；**必须是另一位人工**） */
export interface SettingsConfirmBody {
  pending_id: string;
  second_factor?: string;
  reason?: string;
}
