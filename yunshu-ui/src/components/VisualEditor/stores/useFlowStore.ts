/**
 * useFlowStore — VisualEditor Zustand 状态管理
 *
 * 职责：nodes/edges CRUD + 选中态 + YAML 预览 + 撤销/重做。
 * 不可变更新：仅替换变更节点，保证 React.memo 比较函数生效。
 *
 * 性能埋点：VE_DEBUG=1 时在撤销/重做及关键变更路径输出结构化日志，
 * 包含操作类型、节点/边规模、栈深度、耗时。关闭时零开销。
 */
import { create } from 'zustand';
import {
  applyNodeChanges,
  applyEdgeChanges,
  addEdge,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type Connection,
} from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../types';
import { generateYaml } from '../generator/CodeGenerator';

// ─── 性能埋点开关与工具 ─────────────────────────────────
// 开启方式：localStorage.setItem('ve_debug', '1') 后刷新页面
const VE_DEBUG =
  typeof localStorage !== 'undefined' &&
  typeof localStorage.getItem === 'function' &&
  localStorage.getItem('ve_debug') === '1';

interface PerfCtx {
  nodes: number;
  edges: number;
  undo: number;
  redo: number;
}
function logPerf(op: string, before: PerfCtx, durationMs: number, extra?: Record<string, unknown>) {
  if (!VE_DEBUG) return;
  const after = {
    nodes: useFlowStore.getState().nodes.length,
    edges: useFlowStore.getState().edges.length,
    undo: undoStack.length,
    redo: redoStack.length,
  };
  // eslint-disable-next-line no-console
  console.debug(
    `[VE:store] op=${op} ` +
      `before{nodes=${before.nodes},edges=${before.edges},undo=${before.undo},redo=${before.redo}} ` +
      `after{nodes=${after.nodes},edges=${after.edges},undo=${after.undo},redo=${after.redo}} ` +
      `duration=${durationMs.toFixed(3)}ms` +
      (extra ? ` ${JSON.stringify(extra)}` : ''),
  );
}
function now(): number {
  return VE_DEBUG ? performance.now() : 0;
}
function perfCtx(state: FlowState): PerfCtx {
  return { nodes: state.nodes.length, edges: state.edges.length, undo: undoStack.length, redo: redoStack.length };
}

interface FlowState {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
  selectedNodeId: string | null;
  yamlPreview: string;
  dirty: boolean;

  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: (connection: Connection) => void;

  addNode: (nodeType: NodeType, position: { x: number; y: number }, label?: string) => string;
  updateNodeData: (id: string, data: Partial<FlowNodeData>) => void;
  removeNode: (id: string) => void;
  selectNode: (id: string | null) => void;
  duplicateNode: (id: string) => void;

  regenerateYaml: () => void;
  clearCanvas: () => void;

  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;
}

// ─── 撤销/重做历史栈 ───────────────────────────────────
interface HistorySnapshot {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
}
const undoStack: HistorySnapshot[] = [];
const redoStack: HistorySnapshot[] = [];
const MAX_HISTORY = 50;

function snapshot(state: FlowState): HistorySnapshot {
  return { nodes: state.nodes, edges: state.edges };
}
function pushUndo(state: FlowState) {
  undoStack.push(snapshot(state));
  if (undoStack.length > MAX_HISTORY) undoStack.shift();
  redoStack.length = 0;
  if (VE_DEBUG) {
    // eslint-disable-next-line no-console
    console.debug(
      `[VE:store] pushUndo stack++ undo=${undoStack.length}/${MAX_HISTORY} redo=0 ` +
        `snapshot{nodes=${state.nodes.length},edges=${state.edges.length}}`,
    );
  }
}

// ─── 节点默认数据工厂 ──────────────────────────────────
function createDefaultData(nodeType: NodeType, label?: string): FlowNodeData {
  const base: FlowNodeData = { label: label || defaultLabel(nodeType), nodeType };
  switch (nodeType) {
    case 'skill':
      return { ...base, skillId: '', skillName: label, params: {}, timeout: 30, retryCount: 0 };
    case 'conditional':
      return { ...base, condition: 'true', trueBranch: '', falseBranch: '' };
    case 'loop':
      return { ...base, loopCount: 1, loopVariable: 'item' };
    case 'agent':
      return { ...base, agentType: 'general', maxTurns: 5 };
    case 'workflow':
      return { ...base, workflowId: '' };
    default:
      return base;
  }
}
function defaultLabel(t: NodeType): string {
  const map: Record<NodeType, string> = {
    skill: '新技能',
    conditional: '条件分支',
    loop: '循环',
    agent: 'Agent',
    workflow: '子流程',
  };
  return map[t] || '节点';
}

let nodeSeq = 0;
function nextId(nodeType: NodeType): string {
  nodeSeq += 1;
  return `${nodeType}-${Date.now()}-${nodeSeq}`;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  yamlPreview: '',
  dirty: false,

  onNodesChange: (changes) =>
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes) as Node<FlowNodeData>[], dirty: true })),

  onEdgesChange: (changes) => set((s) => ({ edges: applyEdgeChanges(changes, s.edges), dirty: true })),

  onConnect: (connection) =>
    set((s) => {
      const t0 = now();
      const before = perfCtx(s);
      pushUndo(s);
      const edges = addEdge({ ...connection, animated: true }, s.edges);
      const t1 = now();
      logPerf('onConnect', before, t1 - t0, { source: connection.source, target: connection.target });
      return { edges, dirty: true };
    }),

  addNode: (nodeType, position, label) => {
    const t0 = now();
    const id = nextId(nodeType);
    const data = createDefaultData(nodeType, label);
    const node: Node<FlowNodeData> = { id, type: nodeType, position, data };
    set((s) => {
      const before = perfCtx(s);
      pushUndo(s);
      const t1 = now();
      logPerf('addNode', before, t1 - t0, { type: nodeType, id });
      return { nodes: [...s.nodes, node], selectedNodeId: id, dirty: true };
    });
    return id;
  },

  updateNodeData: (id, data) =>
    set((s) => {
      const t0 = now();
      const before = perfCtx(s);
      pushUndo(s);
      const t1 = now();
      logPerf('updateNodeData', before, t1 - t0, { id, fields: Object.keys(data).join(',') });
      return {
        nodes: s.nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...data } } : n)),
        dirty: true,
      };
    }),

  removeNode: (id) =>
    set((s) => {
      const t0 = now();
      const before = perfCtx(s);
      const removedEdges = s.edges.filter((e) => e.source === id || e.target === id).length;
      pushUndo(s);
      const t1 = now();
      logPerf('removeNode', before, t1 - t0, { id, cascadeEdges: removedEdges });
      return {
        nodes: s.nodes.filter((n) => n.id !== id),
        edges: s.edges.filter((e) => e.source !== id && e.target !== id),
        selectedNodeId: s.selectedNodeId === id ? null : s.selectedNodeId,
        dirty: true,
      };
    }),

  selectNode: (id) => set({ selectedNodeId: id }),

  duplicateNode: (id) =>
    set((s) => {
      const t0 = now();
      const src = s.nodes.find((n) => n.id === id);
      if (!src) return s;
      const before = perfCtx(s);
      pushUndo(s);
      const newId = nextId(src.data.nodeType);
      const dup: Node<FlowNodeData> = {
        id: newId,
        type: src.type,
        position: { x: src.position.x + 40, y: src.position.y + 40 },
        data: { ...src.data, label: `${src.data.label} 副本` },
      };
      const t1 = now();
      logPerf('duplicateNode', before, t1 - t0, { srcId: id, newId });
      return { nodes: [...s.nodes, dup], selectedNodeId: newId, dirty: true };
    }),

  regenerateYaml: () => {
    const t0 = now();
    const s = get();
    const before = perfCtx(s);
    const yaml = generateYaml(s.nodes, s.edges);
    const t1 = now();
    logPerf('regenerateYaml', before, t1 - t0, { yamlLen: yaml.length });
    set({ yamlPreview: yaml });
  },

  clearCanvas: () =>
    set((s) => {
      const t0 = now();
      const before = perfCtx(s);
      pushUndo(s);
      const t1 = now();
      logPerf('clearCanvas', before, t1 - t0);
      return { nodes: [], edges: [], selectedNodeId: null, yamlPreview: '', dirty: false };
    }),

  undo: () => {
    if (undoStack.length === 0) return;
    const t0 = now();
    const before = perfCtx(get());
    const prev = undoStack.pop()!;
    const current = snapshot(get());
    redoStack.push(current);
    set({ nodes: prev.nodes, edges: prev.edges, dirty: true });
    const t1 = now();
    logPerf('undo', before, t1 - t0, {
      restored: { nodes: prev.nodes.length, edges: prev.edges.length },
      saved: { nodes: current.nodes.length, edges: current.edges.length },
    });
  },

  redo: () => {
    if (redoStack.length === 0) return;
    const t0 = now();
    const before = perfCtx(get());
    const next = redoStack.pop()!;
    const current = snapshot(get());
    undoStack.push(current);
    set({ nodes: next.nodes, edges: next.edges, dirty: true });
    const t1 = now();
    logPerf('redo', before, t1 - t0, {
      restored: { nodes: next.nodes.length, edges: next.edges.length },
      saved: { nodes: current.nodes.length, edges: current.edges.length },
    });
  },

  canUndo: () => undoStack.length > 0,
  canRedo: () => redoStack.length > 0,
}));
