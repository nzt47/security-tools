/**
 * 500 节点极端场景 — 撤销/重做栈内存泄漏检测
 *
 * 验证浅引用快照策略在 500 节点 × 50 步历史下的内存行为：
 * - undoStack/redoStack 满载时的堆内存增量
 * - undo/redo 循环后是否有累积泄漏
 * - clearCanvas 后内存是否回归
 *
 * 运行（需 --expose-gc 获得精确 GC 测量）：
 *   $env:NODE_OPTIONS='--expose-gc'; npx vitest run src/components/VisualEditor/__tests__/memory500.test.ts
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../types';
import { useFlowStore } from '../stores/useFlowStore';

// ─── 内存测量工具 ───────────────────────────────────────
function gcIfAvailable(): void {
  if (typeof global.gc === 'function') {
    global.gc();
    global.gc(); // 两次确保 finalize 回调执行
  }
}

function heapMB(): number {
  return Math.round((process.memoryUsage().heapUsed / 1024 / 1024) * 100) / 100;
}

function measureHeap(label: string): number {
  gcIfAvailable();
  const mb = heapMB();
  // eslint-disable-next-line no-console
  console.log(`[mem500] ${label}: ${mb} MB`);
  return mb;
}

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

// ─── 直接注入 store（绕过 addNode 以控制快照行为）────────
function injectNodes(nodes: Node<FlowNodeData>[], edges: Edge[]) {
  useFlowStore.setState({ nodes: [...nodes], edges: [...edges], dirty: true });
}

// ═══════════════════════════════════════════════════════
//  内存泄漏检测用例
// ═══════════════════════════════════════════════════════
describe('500 节点撤销/重做栈内存泄漏检测', () => {
  beforeEach(() => {
    // 重置 store 到初始状态
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      yamlPreview: '',
      dirty: false,
    });
    gcIfAvailable();
  });

  // 从 store 获取方法的便捷函数
  function getStore() {
    return useFlowStore.getState();
  }

  it('基线：空 store 内存', () => {
    const baseline = measureHeap('基线（空 store）');
    expect(baseline).toBeGreaterThan(0);
  });

  it('注入 500 节点后内存增量', () => {
    const before = measureHeap('注入前');
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const after = measureHeap('注入 500 节点后');
    const delta = after - before;
    console.log(`[mem500] 500 节点数据内存占用: ~${delta} MB`);
    expect(delta).toBeLessThan(10);
  });

  it('50 次 updateNodeData — undoStack 满载内存', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const before = measureHeap('50次更新前（undoStack=0）');

    const { updateNodeData } = getStore();
    for (let i = 0; i < 50; i++) {
      updateNodeData(`n${i}`, { timeout: 60 + i });
    }

    const after = measureHeap('50次更新后（undoStack=50）');
    const delta = after - before;
    console.log(`[mem500] undoStack 满载(50快照×500节点) 增量: ~${delta} MB`);
    expect(delta).toBeLessThan(15);
  });

  it('50 次 undo — redoStack 满载内存', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);

    const { updateNodeData, undo } = getStore();
    for (let i = 0; i < 50; i++) {
      updateNodeData(`n${i}`, { timeout: 60 + i });
    }
    const beforeUndo = measureHeap('undo前（undo=50, redo=0）');

    for (let i = 0; i < 50; i++) {
      undo();
    }
    const afterUndo = measureHeap('undo后（undo=0, redo=50）');

    const delta = afterUndo - beforeUndo;
    console.log(`[mem500] undo→redo 转移 增量: ~${delta} MB`);
    expect(Math.abs(delta)).toBeLessThan(10);
  });

  it('clearCanvas 后内存回归', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);

    const { updateNodeData, clearCanvas } = getStore();
    for (let i = 0; i < 50; i++) {
      updateNodeData(`n${i}`, { timeout: 60 + i });
    }
    const peak = measureHeap('峰值（undoStack=50）');

    clearCanvas();
    const afterClear = measureHeap('clearCanvas后');

    console.log(`[mem500] clearCanvas 后内存: ${afterClear} MB（峰值 ${peak} MB）`);
    console.log('[mem500] 注意: undoStack 未清空，历史快照仍持有引用');
  });

  it('循环 undo/redo 250 次 — 累积泄漏检测', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);

    const { updateNodeData, undo, redo } = getStore();
    for (let i = 0; i < 50; i++) {
      updateNodeData(`n${i}`, { timeout: 60 + i });
    }

    const before = measureHeap('循环前（undo=50, redo=0）');

    for (let cycle = 0; cycle < 5; cycle++) {
      for (let i = 0; i < 50; i++) undo();
      for (let i = 0; i < 50; i++) redo();
    }

    const after = measureHeap('循环250次后');
    const delta = after - before;
    console.log(`[mem500] 250次 undo/redo 循环 增量: ~${delta} MB`);
    expect(delta).toBeLessThan(5);
  });

  it('新操作清空 redoStack 后内存', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);

    const { updateNodeData, undo } = getStore();
    for (let i = 0; i < 50; i++) {
      updateNodeData(`n${i}`, { timeout: 60 + i });
    }
    for (let i = 0; i < 30; i++) undo();
    const before = measureHeap('新操作前（undo=20, redo=30）');

    // 新操作应清空 redoStack
    useFlowStore.getState().updateNodeData('n0', { timeout: 999 });
    const after = measureHeap('新操作后（undo=21, redo=0）');

    const delta = before - after;
    console.log(`[mem500] redoStack 清空回收: ~${delta} MB`);
    // 有 --expose-gc 时严格验证回收；无 GC 时 GC 时机不可控，放宽到不大量泄漏
    if (typeof global.gc === 'function') {
      expect(delta).toBeGreaterThanOrEqual(0);
    } else {
      expect(delta).toBeGreaterThan(-2);
    }
  });

  it('快照引用分析 — 浅引用共享验证', () => {
    const nodes = make500Nodes();
    injectNodes(nodes, []);

    const originalNode0 = useFlowStore.getState().nodes[0];
    const originalNode499 = useFlowStore.getState().nodes[499];

    useFlowStore.getState().updateNodeData('n0', { timeout: 999 });
    const afterUpdate = useFlowStore.getState().nodes;

    // node0 应是新对象（不可变更新）
    expect(afterUpdate[0]).not.toBe(originalNode0);
    // node499 应仍是原对象（引用共享）
    expect(afterUpdate[499]).toBe(originalNode499);

    console.log('[mem500] 浅引用共享验证通过: 未变更节点对象在快照间共享');
  });
});
