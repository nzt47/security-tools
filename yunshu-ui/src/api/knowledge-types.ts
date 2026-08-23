/**
 * 知识库 API 类型定义（任务6 · 前端对接）
 *
 * 依据 docs/zh/知识库重构计划/知识库API接口文档_openapi.yaml 生成。
 * 对接模式：与 lib/apiClient.ts 的 request<T>() 保持一致（同域 /api → vite proxy → 5678）。
 *
 * 契约要点：
 * - 所有错误均返回 JSON：404 不存在 / 422 校验失败 / 409 冲突 / 503 未初始化
 * - Card.status ∈ draft | current | archive | unknown
 * - Card.type ∈ concepts | entities | insights
 * - 双链：正文中的 [[slug]] 或 [[slug|别名]] 自动解析进 links 字段
 * - 删除有入链卡片返回 409，响应含 incoming_links 列表
 */

// ═══════════════════════════════════════════════════════════════
//  枚举与基础类型
// ═══════════════════════════════════════════════════════════════

/** 卡片生命周期状态 */
export type CardStatus = 'draft' | 'current' | 'archive' | 'unknown';

/** 卡片类型（wiki 三层目录） */
export type CardType = 'concepts' | 'entities' | 'insights';

/** 矛盾标记状态 */
export type ContradictionStatus = 'reviewed' | 'conflict' | 'resolved';

/** 矛盾标记（AGENTS.md §6.2 只标记不裁决） */
export interface Contradiction {
  target_slug: string;
  status: ContradictionStatus;
  /** 矛盾说明（可选） */
  note?: string;
}

// ═══════════════════════════════════════════════════════════════
//  Card（完整字段，与 OpenAPI Card schema 对齐）
// ═══════════════════════════════════════════════════════════════

export interface Card {
  /** 卡片标题 */
  title: string;
  /** 唯一标识（文件名），默认等于 slugify(title) */
  slug: string;
  status: CardStatus;
  type: CardType;
  /** 来源（文章/播客/资产等） */
  source: string;
  /** 创建日期（YYYY-MM-DD） */
  date: string;
  tags: string[];
  /** 双链目标 slug 列表（正文自动解析，显式传入优先） */
  links: string[];
  contradictions: Contradiction[];
  /** 一句话核心洞见（必填） */
  insight: string;
  /** 适用范围 */
  scope: string;
  /** 卡片正文 Markdown（双链语法 [[slug]] / [[slug|别名]]） */
  content: string;
  /** 扩展元数据（可选） */
  metadata: Record<string, unknown>;
}

/** 创建卡片请求体：insight 必填；links 可省略（自动从正文解析） */
export type CardInput = Pick<
  Card,
  'title' | 'slug' | 'status' | 'type' | 'source' | 'date' | 'insight'
> &
  Partial<Omit<Card, 'title' | 'slug' | 'status' | 'type' | 'source' | 'date' | 'insight'>>;

/** 更新卡片请求体：任意可更新字段（slug 不可变），或 transition 状态迁移 */
export interface CardUpdate {
  /** 目标状态（合法迁移：draft→current|unknown、current→archive|draft、unknown→draft|current；archive 为终态） */
  transition?: CardStatus;
  title?: string;
  status?: CardStatus;
  type?: CardType;
  source?: string;
  date?: string;
  tags?: string[];
  links?: string[];
  contradictions?: Contradiction[];
  insight?: string;
  scope?: string;
  content?: string;
  metadata?: Record<string, unknown>;
}

/** 卡片详情（含入链；仅 GET 详情响应中出现） */
export interface CardDetail extends Card {
  /** 指向该卡的引用方 slug 列表 */
  incoming_links: string[];
}

// ═══════════════════════════════════════════════════════════════
//  检索 / 治理 / 关系图
// ═══════════════════════════════════════════════════════════════

/** 检索命中项 */
export interface KnowledgeHit {
  slug: string;
  title: string;
  status: CardStatus;
  type: CardType;
  /** 融合得分（RRF） */
  score: number;
  /** 精排得分（reranker 不可用时为 null） */
  rerank_score: number | null;
  /** 来源引用（格式 [来源: slug|status]） */
  source_ref: string;
  /** 命中片段 */
  snippet: string;
}

/** 关系图节点 */
export interface GraphNode {
  id: string; // 卡片 slug
  label: string; // 卡片标题
  type: CardType;
  status: CardStatus;
}

/** 关系图边（仅 wiki 内节点） */
export interface GraphEdge {
  source: string; // 引用方 slug
  target: string; // 被引用方 slug
}

/** 健康巡检报告（lint_all 输出） */
export interface HealthReport {
  checked_at: string;
  total_cards: number;
  /** 孤儿卡片（无入链） */
  orphans: string[];
  /** 死链（目标不存在） */
  broken_links: Array<{ from_slug: string; to_slug: string }>;
  /** index.md 与实际不同步的卡片 */
  index_drift: string[];
  /** current 态超期未访问的卡片 */
  stale_cards: Array<{ slug: string; days_unaccessed: number }>;
  /** 未裁决矛盾 */
  unresolved_conflicts: Array<{ slug: string; target_slug: string }>;
  /** 健康分（0-100，100 满分） */
  health_score: number;
  suggestions: string[];
}

// ═══════════════════════════════════════════════════════════════
//  API 响应封装
// ═══════════════════════════════════════════════════════════════

/** 列表过滤查询参数 */
export interface ListCardsParams {
  status?: CardStatus;
  type?: CardType;
}

/** 检索请求体 */
export interface QueryBody {
  question: string;
  /** 返回命中数（1-20，默认 5） */
  top_k?: number;
}

/** 通用错误响应（404/409/422/503 均此结构） */
export interface ApiErrorBody {
  ok: false;
  error: string;
  /** 仅 422：schema 校验违规项列表 */
  violations?: string[];
  /** 仅删除 409：引用方 slug 列表 */
  incoming_links?: string[];
}

export interface ListCardsResponse {
  ok: true;
  cards: Card[];
  count: number;
}

export interface CardDetailResponse {
  ok: true;
  card: CardDetail;
}

export interface CreateCardResponse {
  ok: true;
  card: Card;
}

export interface UpdateCardResponse {
  ok: true;
  card: Card;
}

export interface DeleteCardResponse {
  ok: true;
  deleted: string;
}

export interface KnowledgeQueryResponse {
  ok: true;
  hits: KnowledgeHit[];
  /** 引用块文本（兼容旧调用方） */
  result: string;
}

export interface KnowledgeIndexResponse {
  ok: true;
  content: string; // index.md 全文
}

export interface KnowledgeLintResponse {
  ok: true;
  report: HealthReport;
}

export interface KnowledgeGraphResponse {
  ok: true;
  nodes: GraphNode[];
  edges: GraphEdge[];
}
