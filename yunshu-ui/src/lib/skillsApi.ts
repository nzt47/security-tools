/**
 * 技能管理系统 & 工作流学习系统 API 客户端
 *
 * 状态同步机制说明（遵循"前后端状态绝对同步"原则）：
 * - AbortController：每次请求携带 AbortSignal，组件卸载或依赖变更时自动取消未完成请求
 * - Request ID：搜索/列表加载使用自增 requestId，仅最新请求的响应会更新 UI，杜绝竞态
 * - 边界显性化：所有失败分支抛出带业务错误码的 Error，而非静默返回 null
 * - 健康检查：暴露 checkHealth() 方法，供 UI 轮询 /health 端点
 */

const API_BASE = '';  // 同域

// ═══════════════════════════════════════════════════════════════
//  类型定义（与后端 models.py 对齐）
// ═══════════════════════════════════════════════════════════════

export type SkillCategory =
  | 'BUILTIN' | 'CUSTOM' | 'CLAUDE' | 'COMMUNITY' | 'MCP' | 'AI_GENERATED';

export type SkillStatus =
  | 'DRAFT' | 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED'
  | 'PUBLISHED' | 'DEPRECATED' | 'ARCHIVED';

export type ReviewStatus = 'PENDING' | 'PASSED' | 'REJECTED' | 'NEEDS_REVIEW';

export type ContentType = 'CODE' | 'MARKDOWN' | 'YAML' | 'JSON' | 'TEXT';

export interface SkillVersion {
  version: string;
  content: string;
  changelog?: string;
  created_at: string;
}

export interface ReviewFinding {
  type: 'DUPLICATE' | 'SECURITY' | 'QUALITY';
  severity: 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  message: string;
  detail?: Record<string, unknown>;
}

export interface ReviewResult {
  status: ReviewStatus;
  duplicate_score: number;
  security_score: number;
  quality_score: number;
  overall_score: number;
  findings: ReviewFinding[];
  reviewed_at: string;
}

export interface SkillMetrics {
  usage_count: number;
  success_count: number;
  failure_count: number;
  avg_latency_ms: number;
  last_used_at?: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  category: SkillCategory;
  status: SkillStatus;
  enabled: boolean;
  content: string;
  content_type: ContentType;
  tags: string[];
  version: string;
  author?: string;
  default_params: Record<string, unknown>;
  dependencies: string[];
  metrics: SkillMetrics;
  review?: ReviewResult;
  created_at: string;
  updated_at: string;
}

export interface SkillSearchParams {
  q?: string;
  categories?: SkillCategory[];
  tags?: string[];
  statuses?: SkillStatus[];
  enabled_only?: boolean;
  min_quality?: number;
  sort_by?: 'updated_at' | 'usage_count' | 'quality_score' | 'name';
  sort_desc?: boolean;
  page?: number;
  page_size?: number;
}

export interface SkillSearchResult {
  items: Skill[];
  total: number;
  page: number;
  page_size: number;
}

// ─── 工作流学习 ───
export type WorkflowStatus = 'DRAFT' | 'ACTIVE' | 'DEPRECATED' | 'ARCHIVED';

export interface WorkflowStep {
  id: string;
  name: string;
  tool_name: string;
  params_template: Record<string, unknown>;
  condition?: string;
  timeout_ms?: number;
}

export interface LearnedWorkflow {
  id: string;
  name: string;
  description: string;
  task_signature: string;
  keywords: string[];
  steps: WorkflowStep[];
  priority: number;
  confidence: number;
  success_count: number;
  failure_count: number;
  total_runs: number;
  enabled: boolean;
  status: WorkflowStatus;
  created_at: string;
  updated_at: string;
}

export interface WorkflowExecutionResult {
  ok: boolean;
  workflow_id: string;
  outputs: unknown[];
  skipped_llm: boolean;
  duration_ms: number;
  error?: string;
}

// ═══════════════════════════════════════════════════════════════
//  错误类（边界显性化）
// ═══════════════════════════════════════════════════════════════

export class SkillsApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'SkillsApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

// ═══════════════════════════════════════════════════════════════
//  内部请求工具（AbortController + 业务错误码）
// ═══════════════════════════════════════════════════════════════

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    // 网络层错误（含超时、断网、AbortError）
    if (e instanceof Error && e.name === 'AbortError') {
      throw new SkillsApiError('SKILL_REQUEST_ABORTED', '请求已取消', 0);
    }
    throw new SkillsApiError(
      'SKILL_NETWORK_ERROR',
      `网络请求失败: ${e instanceof Error ? e.message : String(e)}`,
      0,
    );
  }

  let payload: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!res.ok) {
    const p = (payload || {}) as Record<string, unknown>;
    throw new SkillsApiError(
      (p.code as string) || 'SKILL_HTTP_ERROR',
      (p.error as string) || `HTTP ${res.status}`,
      res.status,
      p.details,
    );
  }

  return payload as T;
}

// ═══════════════════════════════════════════════════════════════
//  debounce 工具（搜索框防抖，防止高频请求打乱状态）
// ═══════════════════════════════════════════════════════════════

export function debounce<T extends (...args: never[]) => void>(
  fn: T,
  wait: number,
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return (...args: Parameters<T>) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// ═══════════════════════════════════════════════════════════════
//  Skills Mgmt API
// ═══════════════════════════════════════════════════════════════

export const skillsApi = {
  /** 健康检查 — 返回依赖状态 */
  checkHealth(signal?: AbortSignal) {
    return request<{ ok: boolean; stats: Record<string, unknown> }>(
      '/api/skills-mgmt/health',
      { signal },
    );
  },

  /** 搜索技能（带分页与筛选） */
  search(params: SkillSearchParams, signal?: AbortSignal) {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      if (Array.isArray(v)) {
        v.forEach((x) => q.append(k, String(x)));
      } else {
        q.set(k, String(v));
      }
    });
    return request<SkillSearchResult>(`/api/skills-mgmt/search?${q.toString()}`, { signal });
  },

  /** 列出全部技能 */
  list(signal?: AbortSignal) {
    return request<{ ok: true; items: Skill[] }>('/api/skills-mgmt', { signal });
  },

  /** 获取单个技能详情 */
  get(skillId: string, signal?: AbortSignal) {
    return request<{ ok: true; skill: Skill }>(`/api/skills-mgmt/${skillId}`, { signal });
  },

  /** AI 辅助生成技能 */
  createAi(payload: { name: string; intent: string; category?: SkillCategory; tags?: string[] }) {
    return request<{ ok: true; skill: Skill }>('/api/skills-mgmt/create/ai', {
      method: 'POST',
      body: payload,
    });
  },

  /** 手动创建技能 */
  createManual(payload: Partial<Skill> & { name: string; content: string }) {
    return request<{ ok: true; skill: Skill }>('/api/skills-mgmt/create/manual', {
      method: 'POST',
      body: payload,
    });
  },

  /** 从外部源安装技能（github:/url:/local:/registry:） */
  install(payload: { source: string; force?: boolean }) {
    return request<{ ok: true; skill: Skill }>('/api/skills-mgmt/install', {
      method: 'POST',
      body: payload,
    });
  },

  /** 触发审核（单个技能） */
  review(skillId: string) {
    return request<{ ok: true; skill: Skill; review: ReviewResult }>(
      `/api/skills-mgmt/${skillId}/review`,
      { method: 'POST' },
    );
  },

  /** 批量审核所有待审技能 */
  reviewBatch() {
    return request<{ ok: true; reviewed: number; results: { id: string; review: ReviewResult }[] }>(
      '/api/skills-mgmt/review/batch',
      { method: 'POST' },
    );
  },

  /** 获取/更新审核阈值 */
  getThresholds() {
    return request<{ ok: true; thresholds: Record<string, number> }>(
      '/api/skills-mgmt/review/thresholds',
    );
  },
  updateThresholds(thresholds: Record<string, number>) {
    return request<{ ok: true; thresholds: Record<string, number> }>(
      '/api/skills-mgmt/review/thresholds',
      { method: 'PUT', body: thresholds },
    );
  },

  /** 更新技能字段（PATCH） */
  update(skillId: string, patch: Partial<Skill>) {
    return request<{ ok: true; skill: Skill }>(`/api/skills-mgmt/${skillId}`, {
      method: 'PATCH',
      body: patch,
    });
  },

  /** 删除技能 */
  remove(skillId: string) {
    return request<{ ok: true }>(`/api/skills-mgmt/${skillId}`, { method: 'DELETE' });
  },

  /** 启用/禁用切换 */
  toggle(skillId: string, enabled: boolean) {
    return request<{ ok: true; skill: Skill }>(`/api/skills-mgmt/${skillId}/toggle`, {
      method: 'POST',
      body: { enabled },
    });
  },

  /** 版本列表 */
  listVersions(skillId: string) {
    return request<{ ok: true; versions: SkillVersion[] }>(
      `/api/skills-mgmt/${skillId}/versions`,
    );
  },

  /** 版本升级（major/minor/patch） */
  bumpVersion(skillId: string, kind: 'major' | 'minor' | 'patch', changelog?: string, content?: string) {
    return request<{ ok: true; skill: Skill }>(`/api/skills-mgmt/${skillId}/versions/bump`, {
      method: 'POST',
      body: { kind, changelog, content },
    });
  },

  /** 版本回滚 */
  rollbackVersion(skillId: string, targetVersion: string) {
    return request<{ ok: true; skill: Skill }>(`/api/skills-mgmt/${skillId}/versions/rollback`, {
      method: 'POST',
      body: { target_version: targetVersion },
    });
  },

  /** 参数优化建议 */
  optimize(skillId: string) {
    return request<{ ok: true; skill: Skill; suggestions: string[] }>(
      `/api/skills-mgmt/${skillId}/optimize`,
      { method: 'POST' },
    );
  },

  /** 记录一次执行（成功/失败 + 耗时） */
  recordExecution(skillId: string, success: boolean, latencyMs: number) {
    return request<{ ok: true }>(`/api/skills-mgmt/${skillId}/execution`, {
      method: 'POST',
      body: { success, latency_ms: latencyMs },
    });
  },

  /** 元信息：可用分类 */
  metaCategories() {
    return request<{ ok: true; categories: SkillCategory[] }>(
      '/api/skills-mgmt/meta/categories',
    );
  },
};

// ═══════════════════════════════════════════════════════════════
//  Workflow Learning API
// ═══════════════════════════════════════════════════════════════

export const workflowApi = {
  checkHealth(signal?: AbortSignal) {
    return request<{ ok: boolean; stats: Record<string, unknown> }>(
      '/api/workflow-learning/health',
      { signal },
    );
  },

  /** 从一次大模型交互中学习工作流 */
  learn(payload: {
    session_id: string;
    user_input: string;
    tool_calls: { tool_name: string; params: Record<string, unknown>; output?: unknown }[];
    final_output?: string;
    success?: boolean;
    duration_ms?: number;
  }) {
    return request<{ ok: true; workflow?: LearnedWorkflow; learned: boolean; reason?: string }>(
      '/api/workflow-learning/learn',
      { method: 'POST', body: payload },
    );
  },

  /** 匹配本地工作流（不执行） */
  match(taskText: string, topK = 5) {
    return request<{
      ok: true;
      matches: { workflow: LearnedWorkflow; similarity: number; score: number }[];
    }>('/api/workflow-learning/match', {
      method: 'POST',
      body: { task_text: taskText, top_k: topK },
    });
  },

  /** 尝试执行匹配到的最佳工作流（优先本地执行，避免冗余 LLM 调用） */
  tryExecute(taskText: string, params?: Record<string, unknown>) {
    return request<{ ok: true; result?: WorkflowExecutionResult; skipped: boolean; reason?: string }>(
      '/api/workflow-learning/try-execute',
      { method: 'POST', body: { task_text: taskText, params } },
    );
  },

  /** 按 ID 执行工作流 */
  executeById(wfId: string, params?: Record<string, unknown>) {
    return request<{ ok: true; result: WorkflowExecutionResult }>(
      `/api/workflow-learning/execute/${wfId}`,
      { method: 'POST', body: { params } },
    );
  },

  /** 列出所有工作流 */
  list(enabledOnly = false) {
    return request<{ ok: true; workflows: LearnedWorkflow[] }>(
      `/api/workflow-learning/workflows?enabled_only=${enabledOnly ? 'true' : 'false'}`,
    );
  },

  /** 获取单个工作流 */
  get(wfId: string) {
    return request<{ ok: true; workflow: LearnedWorkflow }>(
      `/api/workflow-learning/workflows/${wfId}`,
    );
  },

  /** 删除工作流 */
  remove(wfId: string) {
    return request<{ ok: true }>(`/api/workflow-learning/workflows/${wfId}`, {
      method: 'DELETE',
    });
  },

  /** 启用/禁用切换 */
  toggle(wfId: string, enabled: boolean) {
    return request<{ ok: true; workflow: LearnedWorkflow }>(
      `/api/workflow-learning/workflows/${wfId}/toggle`,
      { method: 'POST', body: { enabled } },
    );
  },

  /** 调整优先级 */
  setPriority(wfId: string, priority: number) {
    return request<{ ok: true; workflow: LearnedWorkflow }>(
      `/api/workflow-learning/workflows/${wfId}/priority`,
      { method: 'POST', body: { priority } },
    );
  },
};

// ═══════════════════════════════════════════════════════════════
//  埋点占位符（关键用户交互点）
// ═══════════════════════════════════════════════════════════════

export function trackEvent(event: string, payload?: Record<string, unknown>) {
  // 占位符：实际接入可对接 BusinessMetricsCollector / Sentry / 自建埋点
  if (typeof console !== 'undefined') {
    console.log('[trackEvent]', event, payload || {});
  }
}
