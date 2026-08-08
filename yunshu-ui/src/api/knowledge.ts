/**
 * 知识库 API 封装（任务6 Step 3）
 *
 * fetch 封装，风格参考 hooks/useChatStream.ts（同域 API_BASE=''）。
 * 所有接口统一返回 {ok, ...}，非 2xx 抛 KnowledgeApiError（含 status 与 error 信息）。
 */

const API_BASE = ''; // 同域，空字符串

export interface KnowledgeCard {
  title: string;
  slug: string;
  status: string;
  type: string;
  source: string;
  date: string;
  tags: string[];
  links: string[];
  contradictions: Array<{
    target_slug: string;
    status: string;
    summary?: string;
  }>;
  insight: string;
  scope: string;
  content: string;
  metadata: Record<string, unknown>;
  incoming_links?: string[];
}

export interface KnowledgeHit {
  slug: string;
  title: string;
  status: string;
  type: string;
  score: number;
  rerank_score: number;
  source_ref: string;
  snippet: string;
}

export interface LintReport {
  checked_at: string;
  total_cards: number;
  orphans: string[];
  broken_links: Array<{ from_slug: string; to_slug: string }>;
  index_drift: string[];
  stale_cards: Array<{ slug: string; days_unaccessed: number }>;
  unresolved_conflicts: Array<{ slug: string; target_slug: string; status: string; summary: string }>;
  health_score: number;
  suggestions: string[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  status: string;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export class KnowledgeApiError extends Error {
  status: number;
  body: any;

  constructor(status: number, message: string, body?: any) {
    super(message);
    this.name = 'KnowledgeApiError';
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: options?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    throw new KnowledgeApiError(
      res.status,
      data?.error || `HTTP ${res.status}`,
      data,
    );
  }
  return data as T;
}

/** 卡片列表（支持 ?status= & ?type= 过滤） */
export async function listCards(params?: { status?: string; type?: string }): Promise<KnowledgeCard[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.type) qs.set('type', params.type);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const data = await request<{ ok: boolean; cards: KnowledgeCard[] }>(`/api/knowledge/cards${suffix}`);
  return data.cards || [];
}

/** 卡片详情（含 incoming_links） */
export async function getCard(slug: string): Promise<KnowledgeCard> {
  const data = await request<{ ok: boolean; card: KnowledgeCard }>(
    `/api/knowledge/cards/${encodeURIComponent(slug)}`,
  );
  return data.card;
}

/** 创建卡片（body 为任务0 Card dict） */
export async function createCard(card: Partial<KnowledgeCard>): Promise<KnowledgeCard> {
  const data = await request<{ ok: boolean; card: KnowledgeCard }>('/api/knowledge/cards', {
    method: 'POST',
    body: JSON.stringify(card),
  });
  return data.card;
}

/** 更新卡片（支持 transition 状态迁移） */
export async function updateCard(
  slug: string,
  patch: Partial<KnowledgeCard> & { transition?: string },
): Promise<KnowledgeCard> {
  const data = await request<{ ok: boolean; card: KnowledgeCard }>(
    `/api/knowledge/cards/${encodeURIComponent(slug)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(patch),
    },
  );
  return data.card;
}

/** 删除卡片（有入链时后端返回 409） */
export async function deleteCard(slug: string): Promise<void> {
  await request<{ ok: boolean }>(`/api/knowledge/cards/${encodeURIComponent(slug)}`, {
    method: 'DELETE',
  });
}

/** 获取 index.md 内容 */
export async function getIndex(): Promise<string> {
  const data = await request<{ ok: boolean; content: string }>('/api/knowledge/index');
  return data.content;
}

/** 健康报告（lint_all） */
export async function getLint(): Promise<LintReport> {
  const data = await request<{ ok: boolean; report: LintReport }>('/api/knowledge/lint');
  return data.report;
}

/** 节点-边数据（关系图） */
export async function getGraph(): Promise<{ nodes: GraphNode[]; edges: GraphEdge[] }> {
  const data = await request<{ ok: boolean; nodes: GraphNode[]; edges: GraphEdge[] }>('/api/knowledge/graph');
  return { nodes: data.nodes || [], edges: data.edges || [] };
}

/** 融合检索（任务4 /api/knowledge/query） */
export async function searchKnowledge(question: string, topK = 5): Promise<KnowledgeHit[]> {
  const data = await request<{ ok: boolean; hits: KnowledgeHit[] }>('/api/knowledge/query', {
    method: 'POST',
    body: JSON.stringify({ question, top_k: topK }),
  });
  return data.hits || [];
}
