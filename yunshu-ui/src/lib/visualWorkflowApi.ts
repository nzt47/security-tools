/**
 * VisualEditor 工作流草稿 API —— 对接后端 /api/visual-workflows/*
 *
 * 后端能力（见 agent/server_routes/routes_visual_workflows.py）：
 * - GET    /api/visual-workflows       → 草稿列表（摘要）
 * - GET    /api/visual-workflows/<id>  → 单个草稿（含 nodes/edges/yaml）
 * - POST   /api/visual-workflows       → 保存/更新（upsert，无 id 自动生成）
 * - DELETE /api/visual-workflows/<id>  → 删除
 *
 * 说明：草稿与 workflow-learning 的"学习工作流"（learned_workflows.json，
 * 匹配/执行用）完全隔离 —— 可视化编排图仅做 JSON 原样存取。
 * 请求经 lib/apiClient.request() 发出：配置了 API 令牌（FLASK_API_TOKEN，
 * 在界面 API Token 输入框中填写）时自动携带 Authorization 头。
 *
 * 序列化契约：画布上的 xyflow Node/Edge 对象含大量运行时字段
 * （measured/internals 等），落盘前必须收敛为平面 JSON；加载时重建为
 * xyflow v12 所需的最小结构 {id, type, position, data}。
 */
import { request } from './apiClient';
import type { Edge, Node } from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../components/VisualEditor/types';

/** 节点平面 JSON（落盘/上送用） */
export interface PlainVisualNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: FlowNodeData;
}

/** 连线平面 JSON（落盘/上送用） */
export interface PlainVisualEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

/** 后端摘要（列表项） */
export interface VisualWorkflowSummary {
  id: string;
  name: string;
  description?: string;
  node_count?: number;
  edge_count?: number;
  created_at?: string;
  updated_at?: string;
}

/** 后端详情（含完整图数据） */
export interface VisualWorkflowDetail extends VisualWorkflowSummary {
  nodes: PlainVisualNode[];
  edges: PlainVisualEdge[];
  yaml?: string;
}

export interface SaveVisualWorkflowInput {
  /** 已有草稿更新时传入其 id；新建时省略 */
  id?: string;
  name: string;
  description?: string;
  nodes: PlainVisualNode[];
  edges: PlainVisualEdge[];
  /** VisualEditor 生成的 YAML 文本（审计/导出用） */
  yaml?: string;
}

const VALID_NODE_TYPES: NodeType[] = ['skill', 'workflow', 'agent', 'conditional', 'loop'];

function isNodeType(v: unknown): v is NodeType {
  return typeof v === 'string' && (VALID_NODE_TYPES as string[]).includes(v);
}

// ─── 画布 → 平面 JSON ──────────────────────────────────────────────

export function toPlainNode(node: Node<FlowNodeData>): PlainVisualNode {
  const data = node.data ?? ({} as FlowNodeData);
  const pos = node.position ?? { x: 0, y: 0 };
  return {
    id: String(node.id),
    // type 与 data.nodeType 保持一致（xyflow 自定义节点注册键）
    type: isNodeType(node.type) ? node.type : (isNodeType(data.nodeType) ? data.nodeType : 'skill'),
    position: { x: Number(pos.x) || 0, y: Number(pos.y) || 0 },
    data,
  };
}

export function toPlainEdge(edge: Edge): PlainVisualEdge {
  const out: PlainVisualEdge = {
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
  };
  if (edge.sourceHandle != null) out.sourceHandle = String(edge.sourceHandle);
  if (edge.targetHandle != null) out.targetHandle = String(edge.targetHandle);
  return out;
}

/** 收敛画布为可落盘 JSON（丢弃 xyflow 运行时附加字段） */
export function serializeGraph(
  nodes: Node<FlowNodeData>[],
  edges: Edge[],
): { nodes: PlainVisualNode[]; edges: PlainVisualEdge[] } {
  return { nodes: nodes.map(toPlainNode), edges: edges.map(toPlainEdge) };
}

// ─── 平面 JSON → 画布 ──────────────────────────────────────────────

/** 由落盘 JSON 重建 xyflow 节点（最小结构，xyflow 会自动补齐运行时字段） */
export function deserializeNodes(raw: PlainVisualNode[] | undefined): Node<FlowNodeData>[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((n) => n && typeof n.id === 'string' && n.data && typeof n.data === 'object')
    .map((n) => {
      const data = n.data as FlowNodeData;
      const nodeType = isNodeType(data.nodeType) ? data.nodeType : isNodeType(n.type) ? n.type : 'skill';
      return {
        id: String(n.id),
        type: nodeType,
        position: {
          x: Number(n.position?.x) || 0,
          y: Number(n.position?.y) || 0,
        },
        data: { ...data, nodeType, label: String(data.label || '节点') },
      };
    });
}

/** 由落盘 JSON 重建 xyflow 连线 */
export function deserializeEdges(raw: PlainVisualEdge[] | undefined): Edge[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((e) => e && typeof e.id === 'string' && typeof e.source === 'string' && typeof e.target === 'string')
    .map((e) => {
      const edge: Edge = { id: String(e.id), source: String(e.source), target: String(e.target) };
      if (e.sourceHandle != null) edge.sourceHandle = String(e.sourceHandle);
      if (e.targetHandle != null) edge.targetHandle = String(e.targetHandle);
      return edge;
    });
}

// ─── HTTP 客户端 ───────────────────────────────────────────────────

interface ListResp {
  ok?: boolean;
  items?: VisualWorkflowSummary[];
  total?: number;
}

interface DetailResp {
  ok?: boolean;
  workflow?: VisualWorkflowDetail;
}

interface SaveResp {
  ok?: boolean;
  workflow?: VisualWorkflowSummary;
  action?: 'created' | 'updated';
}

/** 列出已保存的可视化工作流草稿（按更新时间倒序） */
export async function listVisualWorkflows(): Promise<VisualWorkflowSummary[]> {
  const resp = await request<ListResp>('/api/visual-workflows');
  return resp?.items ?? [];
}

/** 读取单个草稿完整内容（nodes/edges/yaml） */
export async function getVisualWorkflow(id: string): Promise<VisualWorkflowDetail> {
  const resp = await request<DetailResp>(`/api/visual-workflows/${encodeURIComponent(id)}`);
  if (!resp?.workflow) throw new Error('草稿不存在或返回为空');
  return resp.workflow;
}

/** 保存/更新草稿；返回 {id, action} */
export async function saveVisualWorkflow(
  input: SaveVisualWorkflowInput,
): Promise<{ id: string; action: 'created' | 'updated' }> {
  const resp = await request<SaveResp>('/api/visual-workflows', {
    method: 'POST',
    body: input,
  });
  if (!resp?.workflow?.id) throw new Error('后端未返回草稿 id');
  return { id: resp.workflow.id, action: resp.action ?? 'updated' };
}

/** 删除草稿 */
export async function deleteVisualWorkflow(id: string): Promise<void> {
  await request<{ ok?: boolean }>(`/api/visual-workflows/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}
