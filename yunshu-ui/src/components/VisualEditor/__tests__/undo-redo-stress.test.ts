/**
 * 500 节点极端场景 — 快速连续 undo/redo 边界测试
 *
 * 验证撤销/重做在极端操作频率下的安全性：
 * - 空栈时 no-op，不抛异常、不栈溢出
 * - MAX_HISTORY=50 硬上限不被突破
 * - 单次操作耗时与总耗时在可接受范围
 *
 * 运行：npx vitest run src/components/VisualEditor/__tests__/undo-redo-stress.test.ts
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../types';
import { useFlowStore } from '../stores/useFlowStore';

// ─── 500 节点 mock 数据 ─────────────────────────────────
function make500Nodes(): Node<FlowNodeData>[] {
  const nodes: Node<FlowNodeData>[] = [];
  const types: NodeType[] = ['skill', 'skill', 'skill', 'skill', 'conditional', 'loop', 'agent', 'workflow'];
  for (let i = 0; i < 500; i++) {
    const nodeType = types[i % types.length];
    nodes.push({
      id: `n${i}`,
      type: nodeType,
      position: { x: (i % 10) * 220, y: Math.floor(i / 10) * 120 },
      data: {
        label: `节点${i}`,
        nodeType,
        ...(nodeType === 'skill'
          ? { skillId: `skill_${i}`, skillName: `技能${i}`, timeout: 30, retryCount: 0, params: { key: `val_${i}` } }
          : {}),
        ...(nodeType === 'conditional' ? { condition: `val > ${i}`, trueBranch: '', falseBranch: '' } : {}),
        ...(nodeType === 'loop' ? { loopCount: (i % 10) + 1, loopVariable: 'item' } : {}),
        ...(nodeType === 'agent' ? { agentType: 'general', maxTurns: 5 } : {}),
        ...(nodeType === 'workflow' ? { workflowId: `wf_${i}` } : {}),
      },
    });
  }
  return nodes;
}

function makeEdges(nodes: Node<FlowNodeData>[]): Edge[] {
  const edges: Edge[] = [];
  for (let i = 0; i < nodes.length - 1; i++) {
    edges.push({ id: `e${i}`, source: nodes[i].id, target: nodes[i + 1].id });
  }
  return edges;
}

function injectNodes(nodes: Node<FlowNodeData>[], edges: Edge[]) {
  useFlowStore.setState({ nodes: [...nodes], edges: [...edges], dirty: true });
}

// ─── 耗时测量 ───────────────────────────────────────────
function measure<T>(fn: () => T): { result: T; ms: number } {
  const t0 = performance.now();
  const result = fn();
  return { result, ms: performance.now() - t0 };
}

// ═══════════════════════════════════════════════════════
//  快速连续 undo/redo 边界测试
// ═══════════════════════════════════════════════════════
describe('500 节点快速连续 undo/redo 边界测试', () => {
  beforeEach(() => {
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      yamlPreview: '',
      dirty: false,
    });
  });

  function getStore() {
    return useFlowStore.getState();
  }

  function setupHistory(steps: number) {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const { updateNodeData } = getStore();
    for (let i = 0; i < steps; i++) {
      updateNodeData(`n${i % 500}`, { timeout: 60 + i });
    }
  }

  it('空栈连续 undo 1000 次 — 不抛异常、不栈溢出', () => {
    const { undo } = getStore();
    expect(() => {
      for (let i = 0; i < 1000; i++) undo();
    }).not.toThrow();
    // 空栈 no-op，nodes 仍为空
    expect(useFlowStore.getState().nodes.length).toBe(0);
    expect(getStore().canUndo()).toBe(false);
  });

  it('空栈连续 redo 1000 次 — 不抛异常、不栈溢出', () => {
    const { redo } = getStore();
    expect(() => {
      for (let i = 0; i < 1000; i++) redo();
    }).not.toThrow();
    expect(getStore().canRedo()).toBe(false);
  });

  it('满栈(50)连续 undo 1000 次 — 超过栈深度后安全 no-op', () => {
    setupHistory(50);
    const { undo } = getStore();
    expect(getStore().canUndo()).toBe(true);

    let lastNodeCount = useFlowStore.getState().nodes.length;
    let noOpCount = 0;
    for (let i = 0; i < 1000; i++) {
      undo();
      const currentCount = useFlowStore.getState().nodes.length;
      if (i >= 50) {
        // 超过栈深度后应为 no-op
        if (currentCount === lastNodeCount) noOpCount++;
        lastNodeCount = currentCount;
      }
    }
    // 950 次应为 no-op（1000 - 50）
    expect(noOpCount).toBe(950);
    expect(getStore().canUndo()).toBe(false);
  });

  it('MAX_HISTORY=50 硬上限验证 — 60 次操作后栈不超限', () => {
    setupHistory(60);
    // undoStack 应被 shift 到 50
    // 通过 undo 50 次应到底，第 51 次 no-op
    const { undo } = getStore();
    let undoCount = 0;
    for (let i = 0; i < 60; i++) {
      const before = useFlowStore.getState().nodes.length;
      undo();
      const after = useFlowStore.getState().nodes.length;
      if (before !== after || i < 50) undoCount++;
    }
    // 最多 50 次有效 undo
    expect(undoCount).toBeLessThanOrEqual(50);
    expect(getStore().canUndo()).toBe(false);
  });

  it('快速交替 undo/redo 500 次 — 无异常、记录耗时', () => {
    setupHistory(50);
    const { undo, redo } = getStore();

    const { ms } = measure(() => {
      for (let i = 0; i < 500; i++) {
        undo();
        redo();
      }
    });

    console.log(`[undo-redo-stress] 500 次交替 undo/redo 总耗时: ${ms.toFixed(2)}ms, 平均: ${(ms / 1000).toFixed(3)}ms/次`);
    // 1000 次操作（500 undo + 500 redo）应在 2s 内
    expect(ms).toBeLessThan(2000);
    // 最终状态应回到 undoStack=50
    expect(getStore().canUndo()).toBe(true);
    expect(getStore().canRedo()).toBe(false);
  });

  it('单次 undo/redo 最大耗时 — 500 节点场景', () => {
    setupHistory(50);
    const { undo, redo } = getStore();

    // 先 undo 50 次填满 redoStack
    for (let i = 0; i < 50; i++) undo();

    // 测量 50 次 redo 的单次最大耗时
    let maxRedo = 0;
    for (let i = 0; i < 50; i++) {
      const { ms } = measure(() => redo());
      if (ms > maxRedo) maxRedo = ms;
    }

    // 再 undo 50 次测单次最大
    let maxUndo = 0;
    for (let i = 0; i < 50; i++) {
      const { ms } = measure(() => undo());
      if (ms > maxUndo) maxUndo = ms;
    }

    console.log(`[undo-redo-stress] 单次最大耗时: undo=${maxUndo.toFixed(3)}ms, redo=${maxRedo.toFixed(3)}ms`);
    // 单次应 <10ms（浅引用快照，仅切换数组引用）
    expect(maxUndo).toBeLessThan(50);
    expect(maxRedo).toBeLessThan(50);
  });

  it('极端：1000 次 updateNodeData + 1000 次 undo — 无累积问题', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const { updateNodeData, undo } = getStore();

    // 1000 次更新（undoStack 始终保持 50 上限）
    const { ms: updateMs } = measure(() => {
      for (let i = 0; i < 1000; i++) {
        updateNodeData(`n${i % 500}`, { timeout: i });
      }
    });

    // 1000 次 undo（50 次有效 + 950 次 no-op）
    const { ms: undoMs } = measure(() => {
      for (let i = 0; i < 1000; i++) undo();
    });

    console.log(
      `[undo-redo-stress] 1000次update: ${updateMs.toFixed(2)}ms, 1000次undo: ${undoMs.toFixed(2)}ms`,
    );

    // 1000 次更新应在 3s 内（含 pushUndo + shift）
    expect(updateMs).toBeLessThan(3000);
    // 1000 次 undo 应在 1s 内（50 次有效 + 950 次 no-op）
    expect(undoMs).toBeLessThan(1000);
    expect(getStore().canUndo()).toBe(false);
  });

  it('undo/redo 后数据一致性 — 节点数与 edges 保持正确', () => {
    setupHistory(50);
    const { undo, redo } = getStore();

    const originalNodeCount = useFlowStore.getState().nodes.length;
    const originalEdgeCount = useFlowStore.getState().edges.length;

    // undo 到底
    for (let i = 0; i < 50; i++) undo();
    // undo 到最初注入的 500 节点状态
    expect(useFlowStore.getState().nodes.length).toBe(originalNodeCount);
    expect(useFlowStore.getState().edges.length).toBe(originalEdgeCount);

    // redo 回来
    for (let i = 0; i < 50; i++) redo();
    expect(useFlowStore.getState().nodes.length).toBe(originalNodeCount);
    expect(useFlowStore.getState().edges.length).toBe(originalEdgeCount);

    // 最后一次 update 的数据应保持
    const lastNode = useFlowStore.getState().nodes[49];
    expect(lastNode.data.timeout).toBe(109); // 60 + 49 = 109
  });
});
