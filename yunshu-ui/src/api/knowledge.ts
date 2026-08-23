/**
 * 知识库 API 封装（任务6）
 *
 * 类型来自 ./knowledge-types.ts（依据 OpenAPI 文档生成）。
 * 统一使用 @/lib/apiClient 的 request<T>()：同域 /api → vite proxy → 5678。
 *
 * 错误处理约定（后端均返回 JSON）：
 *   404 不存在 / 422 schema 校验 / 409 冲突（重复 slug、非法迁移、有入链删除） / 503 未初始化
 * request<T>() 在 HTTP !ok 时抛 ApiError（code/status/details）。
 */
import { request, buildQuery } from '@/lib/apiClient';
import type {
  Card,
  CardDetail,
  CardInput,
  CardUpdate,
  KnowledgeHit,
  HealthReport,
  GraphNode,
  GraphEdge,
  ListCardsParams,
  ListCardsResponse,
  CardDetailResponse,
  CreateCardResponse,
  UpdateCardResponse,
  DeleteCardResponse,
  KnowledgeQueryResponse,
  KnowledgeIndexResponse,
  KnowledgeLintResponse,
  KnowledgeGraphResponse,
  QueryBody,
} from './knowledge-types';

/** 卡片列表（支持 ?status=&type= 过滤） */
export function listCards(params: ListCardsParams = {}): Promise<ListCardsResponse> {
  const q = buildQuery({ ...params });
  return request<ListCardsResponse>(`/api/knowledge/cards${q ? `?${q}` : ''}`);
}

/** 卡片详情（含 incoming_links） */
export function getCard(slug: string): Promise<CardDetailResponse> {
  return request<CardDetailResponse>(`/api/knowledge/cards/${encodeURIComponent(slug)}`);
}

/** 创建卡片（正文双链自动解析进 links） */
export function createCard(data: CardInput): Promise<CreateCardResponse> {
  return request<CreateCardResponse>('/api/knowledge/cards', {
    method: 'POST',
    body: data,
  });
}

/** 更新卡片（字段更新或 transition 状态迁移） */
export function updateCard(slug: string, data: CardUpdate): Promise<UpdateCardResponse> {
  return request<UpdateCardResponse>(`/api/knowledge/cards/${encodeURIComponent(slug)}`, {
    method: 'PATCH',
    body: data,
  });
}

/** 删除卡片（有入链时抛 ApiError 409，details 含 incoming_links） */
export function deleteCard(slug: string): Promise<DeleteCardResponse> {
  return request<DeleteCardResponse>(`/api/knowledge/cards/${encodeURIComponent(slug)}`, {
    method: 'DELETE',
  });
}

/** 知识库融合检索 */
export function searchKnowledge(question: string, topK = 5): Promise<KnowledgeQueryResponse> {
  const body: QueryBody = { question, top_k: topK };
  return request<KnowledgeQueryResponse>('/api/knowledge/query', {
    method: 'POST',
    body,
  });
}

/** index.md 内容 */
export function getIndex(): Promise<KnowledgeIndexResponse> {
  return request<KnowledgeIndexResponse>('/api/knowledge/index');
}

/** 健康巡检报告（lint） */
export function getLint(): Promise<KnowledgeLintResponse> {
  return request<KnowledgeLintResponse>('/api/knowledge/lint');
}

/** 关系图节点-边数据 */
export function getGraph(): Promise<KnowledgeGraphResponse> {
  return request<KnowledgeGraphResponse>('/api/knowledge/graph');
}

export type {
  Card,
  CardDetail,
  CardInput,
  CardUpdate,
  KnowledgeHit,
  HealthReport,
  GraphNode,
  GraphEdge,
};
