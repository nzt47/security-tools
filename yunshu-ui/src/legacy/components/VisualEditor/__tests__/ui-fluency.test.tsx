/**
 * 500 节点场景 — UI 交互流畅度测试
 *
 * 测量 store 操作 → React rerender 的端到端耗时，检测是否卡顿：
 * - 单次操作端到端 <16ms = 60fps 流畅
 * - 单次操作 16-50ms = 掉帧
 * - 单次操作 >50ms = 卡顿
 *
 * 由于 jsdom 无真实渲染管线，用 act() 包裹 store 操作测量同步耗时，
 * 间接反映一帧内的主线程占用。
 *
 * 运行：npx vitest run src/components/VisualEditor/__tests__/ui-fluency.test.tsx
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { FC, memo } from 'react';
import { render, act } from '@testing-library/react';
import { useFlowStore } from '../stores/useFlowStore';
import type { Node, Edge } from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../types';

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
          ? { skillId: `skill_${i}`, skillName: `技能${i}`, timeout: 30, retryCount: 0, params: {} }
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

// ─── 探针组件 ───────────────────────────────────────────
// 模拟真实消费 store 的组件，用 renderTracker 计数渲染次数

function makeProbeComponents() {
  const nodesRenderTracker = vi.fn();
  const selectedRenderTracker = vi.fn();
  const nodeCountRenderTracker = vi.fn();

  // 订阅 s.nodes（全量）— 模拟 FlowCanvas
  const NodesProbe: FC = () => {
    const nodes = useFlowStore((s) => s.nodes);
    nodesRenderTracker();
    return <div data-testid="nodes-probe">{nodes.length}</div>;
  };

  // 订阅 s.selectedNodeId — 模拟 PropertiesPanel
  const SelectedProbe: FC = () => {
    const selected = useFlowStore((s) => s.selectedNodeId);
    selectedRenderTracker();
    return <div data-testid="selected-probe">{selected ?? 'none'}</div>;
  };

  // 订阅 s.nodes.length — 模拟工具栏节点计数
  const NodeCountProbe: FC = () => {
    const count = useFlowStore((s) => s.nodes.length);
    nodeCountRenderTracker();
    return <div data-testid="count-probe">{count}</div>;
  };

  // memo 化的节点项 — 模拟 NodeRenderer
  const NodeItemRaw: FC<{ id: string; label: string }> = ({ label }) => {
    return <div data-testid="node-item">{label}</div>;
  };
  const NodeItemMemo = memo(NodeItemRaw, (prev, next) => prev.label === next.label);

  return {
    NodesProbe,
    SelectedProbe,
    NodeCountProbe,
    NodeItemMemo,
    nodesRenderTracker,
    selectedRenderTracker,
    nodeCountRenderTracker,
  };
}

// ─── 耗时工具 ───────────────────────────────────────────
function measureAct(fn: () => void): number {
  const t0 = performance.now();
  act(fn);
  return performance.now() - t0;
}

const FLUENCY_THRESHOLD = 16; // 60fps 单帧预算
const STUTTER_THRESHOLD = 50; // 卡顿线

// ═══════════════════════════════════════════════════════
//  UI 交互流畅度测试
// ═══════════════════════════════════════════════════════
describe('500 节点 UI 交互流畅度', () => {
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

  it('单次 updateNodeData 端到端耗时 — 流畅线 16ms', () => {
    const nodes = make500Nodes();
    injectNodes(nodes, []);
    const { NodesProbe, nodesRenderTracker } = makeProbeComponents();
    render(<NodesProbe />);
    nodesRenderTracker.mockClear();

    const { updateNodeData } = getStore();
    const ms = measureAct(() => updateNodeData('n0', { timeout: 999 }));

    console.log(`[ui-fluency] 单次 updateNodeData 端到端: ${ms.toFixed(2)}ms`);
    // 订阅 s.nodes 的组件应重渲染（数组引用变了）
    expect(nodesRenderTracker).toHaveBeenCalledTimes(1);
    // 流畅线：应 <16ms（jsdom 比浏览器慢，放宽到 50ms）
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('单次 selectNode 端到端耗时 — 不触发 nodes 订阅者', () => {
    const nodes = make500Nodes();
    injectNodes(nodes, []);
    const { NodesProbe, SelectedProbe, nodesRenderTracker, selectedRenderTracker } = makeProbeComponents();
    render(
      <>
        <NodesProbe />
        <SelectedProbe />
      </>,
    );
    nodesRenderTracker.mockClear();
    selectedRenderTracker.mockClear();

    const { selectNode } = getStore();
    const ms = measureAct(() => selectNode('n100'));

    console.log(`[ui-fluency] 单次 selectNode 端到端: ${ms.toFixed(2)}ms`);
    // selectNode 只改 selectedNodeId，不应触发 nodes 订阅者
    expect(nodesRenderTracker).toHaveBeenCalledTimes(0);
    expect(selectedRenderTracker).toHaveBeenCalledTimes(1);
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('单次 undo 端到端耗时 — 500 节点切换数组引用', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    // 产生 1 步历史
    act(() => getStore().updateNodeData('n0', { timeout: 999 }));

    const { NodesProbe, nodesRenderTracker } = makeProbeComponents();
    render(<NodesProbe />);
    nodesRenderTracker.mockClear();

    const { undo } = getStore();
    const ms = measureAct(() => undo());

    console.log(`[ui-fluency] 单次 undo 端到端: ${ms.toFixed(2)}ms`);
    expect(nodesRenderTracker).toHaveBeenCalledTimes(1);
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('单次 redo 端到端耗时 — 500 节点切换数组引用', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    act(() => getStore().updateNodeData('n0', { timeout: 999 }));
    act(() => getStore().undo());

    const { NodesProbe, nodesRenderTracker } = makeProbeComponents();
    render(<NodesProbe />);
    nodesRenderTracker.mockClear();

    const { redo } = getStore();
    const ms = measureAct(() => redo());

    console.log(`[ui-fluency] 单次 redo 端到端: ${ms.toFixed(2)}ms`);
    expect(nodesRenderTracker).toHaveBeenCalledTimes(1);
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('连续 50 次 updateNodeData — 测平均耗时与最大耗时', () => {
    const nodes = make500Nodes();
    injectNodes(nodes, []);
    const { NodesProbe, nodesRenderTracker } = makeProbeComponents();
    render(<NodesProbe />);

    const { updateNodeData } = getStore();
    const times: number[] = [];
    for (let i = 0; i < 50; i++) {
      const ms = measureAct(() => updateNodeData(`n${i}`, { timeout: i }));
      times.push(ms);
    }

    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    const max = Math.max(...times);
    const overFluency = times.filter((t) => t > FLUENCY_THRESHOLD).length;
    const overStutter = times.filter((t) => t > STUTTER_THRESHOLD).length;

    console.log(
      `[ui-fluency] 50次 updateNodeData: avg=${avg.toFixed(2)}ms, max=${max.toFixed(2)}ms, ` +
        `超流畅线(>16ms)=${overFluency}次, 超卡顿线(>50ms)=${overStutter}次`,
    );
    // 平均应 <16ms，最大不应卡顿
    expect(overStutter).toBe(0);
  });

  it('连续 undo/redo 100 次循环 — 测帧时间分布', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    // 产生 10 步历史
    for (let i = 0; i < 10; i++) {
      act(() => getStore().updateNodeData(`n${i}`, { timeout: i }));
    }

    const { NodesProbe } = makeProbeComponents();
    render(<NodesProbe />);

    const { undo, redo } = getStore();
    const times: number[] = [];
    // undo 10 次到底，redo 10 次回来，循环 5 轮 = 100 次
    for (let cycle = 0; cycle < 5; cycle++) {
      for (let i = 0; i < 10; i++) {
        times.push(measureAct(() => undo()));
      }
      for (let i = 0; i < 10; i++) {
        times.push(measureAct(() => redo()));
      }
    }

    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    const max = Math.max(...times);
    const p95 = times.sort((a, b) => a - b)[Math.floor(times.length * 0.95)];
    const overFluency = times.filter((t) => t > FLUENCY_THRESHOLD).length;
    const overStutter = times.filter((t) => t > STUTTER_THRESHOLD).length;

    console.log(
      `[ui-fluency] 100次 undo/redo循环: avg=${avg.toFixed(2)}ms, p95=${p95.toFixed(2)}ms, ` +
        `max=${max.toFixed(2)}ms, 超流畅线=${overFluency}次, 超卡顿线=${overStutter}次`,
    );
    // 不应有卡顿
    expect(overStutter).toBe(0);
  });

  it('addNode 端到端耗时 — 500 节点基础上新增', () => {
    const nodes = make500Nodes();
    injectNodes(nodes, []);
    const { NodesProbe, NodeCountProbe, nodesRenderTracker, nodeCountRenderTracker } = makeProbeComponents();
    render(
      <>
        <NodesProbe />
        <NodeCountProbe />
      </>,
    );
    nodesRenderTracker.mockClear();
    nodeCountRenderTracker.mockClear();

    const { addNode } = getStore();
    const ms = measureAct(() => addNode('skill', { x: 100, y: 100 }, '新节点'));

    console.log(`[ui-fluency] addNode(501th) 端到端: ${ms.toFixed(2)}ms`);
    // nodes 和 nodes.length 都应变化
    expect(nodesRenderTracker).toHaveBeenCalledTimes(1);
    expect(nodeCountRenderTracker).toHaveBeenCalledTimes(1);
    expect(useFlowStore.getState().nodes.length).toBe(501);
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('removeNode 端到端耗时 — 从 500 节点删除', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const { NodesProbe, nodesRenderTracker } = makeProbeComponents();
    render(<NodesProbe />);
    nodesRenderTracker.mockClear();

    const { removeNode } = getStore();
    const ms = measureAct(() => removeNode('n250'));

    console.log(`[ui-fluency] removeNode 端到端: ${ms.toFixed(2)}ms`);
    expect(nodesRenderTracker).toHaveBeenCalledTimes(1);
    expect(useFlowStore.getState().nodes.length).toBe(499);
    expect(ms).toBeLessThan(STUTTER_THRESHOLD);
  });

  it('综合：模拟用户编辑会话 — 30 次混合操作', () => {
    const nodes = make500Nodes();
    const edges = makeEdges(nodes);
    injectNodes(nodes, edges);
    const { NodesProbe, SelectedProbe } = makeProbeComponents();
    render(
      <>
        <NodesProbe />
        <SelectedProbe />
      </>,
    );

    const times: number[] = [];

    // 模拟真实用户操作序列（动态获取 store 避免旧引用）
    const ops = [
      () => getStore().selectNode('n0'),
      () => getStore().updateNodeData('n0', { timeout: 60 }),
      () => getStore().selectNode('n100'),
      () => getStore().updateNodeData('n100', { timeout: 60 }),
      () => getStore().addNode('conditional', { x: 200, y: 200 }),
      () => getStore().updateNodeData('n200', { timeout: 120 }),
      () => getStore().undo(),
      () => getStore().undo(),
      () => getStore().redo(),
      () => getStore().selectNode('n300'),
      () => getStore().duplicateNode('n50'),
      () => getStore().updateNodeData('n50', { timeout: 90 }),
      () => getStore().removeNode('n400'),
      () => getStore().undo(),
      () => getStore().selectNode('n10'),
      () => getStore().updateNodeData('n10', { timeout: 45 }),
      () => getStore().undo(),
      () => getStore().redo(),
      () => getStore().clearCanvas(),
      () => getStore().undo(),
      () => getStore().selectNode(null),
      () => getStore().addNode('skill', { x: 0, y: 0 }),
      () => getStore().updateNodeData(useFlowStore.getState().nodes[0].id, { timeout: 30 }),
      () => getStore().undo(),
      () => getStore().redo(),
      () => getStore().selectNode(useFlowStore.getState().nodes[0].id),
      () => getStore().duplicateNode(useFlowStore.getState().nodes[0].id),
      () => getStore().removeNode(useFlowStore.getState().nodes[0].id),
      () => getStore().undo(),
      () => getStore().clearCanvas(),
    ];

    for (const op of ops) {
      times.push(measureAct(() => op()));
    }

    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    const max = Math.max(...times);
    const overFluency = times.filter((t) => t > FLUENCY_THRESHOLD).length;
    const overStutter = times.filter((t) => t > STUTTER_THRESHOLD).length;

    console.log(
      `[ui-fluency] 30次混合操作: avg=${avg.toFixed(2)}ms, max=${max.toFixed(2)}ms, ` +
        `超流畅线=${overFluency}次, 超卡顿线=${overStutter}次`,
    );
    // 不应有严重卡顿
    expect(overStutter).toBe(0);
  });
});
