/**
 * CodeGenerator — 图 → YAML 转换
 *
 * 拓扑排序保证步骤顺序，nodeToStep 按节点类型映射为工作流步骤。
 * 纯函数，无副作用，便于单元测试。
 */
import type { Edge, Node } from '@xyflow/react';
import type { FlowNodeData } from '../types';

export interface WorkflowStep {
  name: string;
  type: string;
  [key: string]: unknown;
}

export interface WorkflowDefinition {
  name: string;
  version: string;
  steps: WorkflowStep[];
}

/**
 * 拓扑排序：按 edges 依赖关系排列节点。
 * 无依赖的节点在前，被指向的节点在后。
 */
export function topologicalSort(nodes: Node<FlowNodeData>[], edges: Edge[]): Node<FlowNodeData>[] {
  const idSet = new Set(nodes.map((n) => n.id));
  const inDegree = new Map<string, number>();
  const adj = new Map<string, string[]>();
  for (const n of nodes) {
    inDegree.set(n.id, 0);
    adj.set(n.id, []);
  }
  for (const e of edges) {
    if (!idSet.has(e.source) || !idSet.has(e.target)) continue;
    adj.get(e.source)!.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
  }
  const queue: string[] = [];
  for (const [id, deg] of inDegree) {
    if (deg === 0) queue.push(id);
  }
  const sorted: Node<FlowNodeData>[] = [];
  while (queue.length > 0) {
    const id = queue.shift()!;
    const node = nodes.find((n) => n.id === id);
    if (node) sorted.push(node);
    for (const next of adj.get(id) || []) {
      inDegree.set(next, (inDegree.get(next) || 0) - 1);
      if (inDegree.get(next) === 0) queue.push(next);
    }
  }
  // 环或孤立节点兜底：追加未排序节点
  for (const n of nodes) {
    if (!sorted.find((s) => s.id === n.id)) sorted.push(n);
  }
  return sorted;
}

function findNextLabel(nodeId: string, edges: Edge[], nodes: Node<FlowNodeData>[]): string | null {
  const outEdge = edges.find((e) => e.source === nodeId);
  if (!outEdge) return null;
  const target = nodes.find((n) => n.id === outEdge.target);
  return target ? target.data.label : null;
}

function findBranchLabel(
  nodeId: string,
  edges: Edge[],
  nodes: Node<FlowNodeData>[],
  handleId: string,
): string | null {
  const branchEdge = edges.find((e) => e.source === nodeId && e.sourceHandle === handleId);
  if (!branchEdge) return null;
  const target = nodes.find((n) => n.id === branchEdge.target);
  return target ? target.data.label : null;
}

export function nodeToStep(node: Node<FlowNodeData>, edges: Edge[], allNodes: Node<FlowNodeData>[]): WorkflowStep {
  const data = node.data;
  switch (data.nodeType) {
    case 'skill':
      return {
        name: data.label,
        type: 'skill',
        skill_id: data.skillId,
        params: data.params || {},
        next: findNextLabel(node.id, edges, allNodes),
        timeout: data.timeout,
        retry: data.retryCount,
      };
    case 'conditional':
      return {
        name: data.label,
        type: 'conditional',
        condition: data.condition,
        true_branch: findBranchLabel(node.id, edges, allNodes, 'true') || data.trueBranch,
        false_branch: findBranchLabel(node.id, edges, allNodes, 'false') || data.falseBranch,
      };
    case 'loop':
      return {
        name: data.label,
        type: 'loop',
        count: data.loopCount,
        variable: data.loopVariable,
        body: findNextLabel(node.id, edges, allNodes),
      };
    case 'agent':
      return {
        name: data.label,
        type: 'agent',
        agent_type: data.agentType,
        max_turns: data.maxTurns,
        next: findNextLabel(node.id, edges, allNodes),
      };
    case 'workflow':
      return {
        name: data.label,
        type: 'workflow',
        workflow_id: data.workflowId,
        next: findNextLabel(node.id, edges, allNodes),
      };
    default:
      return { name: data.label, type: 'unknown' };
  }
}

/**
 * 简易 YAML 序列化（避免引入 js-yaml 依赖）。
 * 缩进 2 空格，支持 string/number/boolean/null/array/object。
 */
export function toYaml(obj: unknown, indent = 0): string {
  const pad = '  '.repeat(indent);
  if (obj === null || obj === undefined) return 'null';
  if (typeof obj === 'string') return obj;
  if (typeof obj === 'number' || typeof obj === 'boolean') return String(obj);
  if (Array.isArray(obj)) {
    if (obj.length === 0) return '[]';
    return obj.map((item) => `${pad}- ${toYaml(item, indent + 1).trimStart()}`).join('\n');
  }
  if (typeof obj === 'object') {
    const entries = Object.entries(obj as Record<string, unknown>);
    if (entries.length === 0) return '{}';
    return entries
      .map(([key, val]) => {
        if (val !== null && typeof val === 'object' && !Array.isArray(val) && Object.keys(val).length > 0) {
          return `${pad}${key}:\n${toYaml(val, indent + 1)}`;
        }
        if (Array.isArray(val) && val.length > 0) {
          return `${pad}${key}:\n${toYaml(val, indent + 1)}`;
        }
        return `${pad}${key}: ${toYaml(val, 0)}`;
      })
      .join('\n');
  }
  return String(obj);
}

export function generateYaml(nodes: Node<FlowNodeData>[], edges: Edge[]): string {
  const sorted = topologicalSort(nodes, edges);
  const steps = sorted.map((n) => nodeToStep(n, edges, sorted));
  const workflow: WorkflowDefinition = {
    name: 'generated_workflow',
    version: '1.0',
    steps,
  };
  return toYaml(workflow);
}
