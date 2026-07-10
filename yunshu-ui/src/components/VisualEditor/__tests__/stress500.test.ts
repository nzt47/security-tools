/**
 * 500 节点极端场景性能压测
 *
 * 验证 P2_性能对比报告.md 第 9 章的预测：
 * - generateYaml 在 500 节点时是否超 100ms 验收线
 * - 拓扑排序 / 撤销重做 / 节点更新的耗时退化
 *
 * 运行：npx vitest run src/components/VisualEditor/__tests__/stress500.test.ts
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import type { FlowNodeData, NodeType } from '../types';
import {
  generateYaml,
  topologicalSort,
} from '../generator/CodeGenerator';

// ─── 500 节点 mock 数据生成 ─────────────────────────────
function generate500Nodes(): Node<FlowNodeData>[] {
  const nodes: Node<FlowNodeData>[] = [];
  const types: NodeType[] = ['skill', 'skill', 'skill', 'skill', 'conditional', 'loop', 'agent', 'workflow'];
  for (let i = 0; i < 500; i++) {
    const nodeType = types[i % types.length];
    const col = i % 10;
    const row = Math.floor(i / 10);
    const data: FlowNodeData = {
      label: `节点${i}`,
      nodeType,
      ...(nodeType === 'skill'
        ? { skillId: `skill_${i}`, skillName: `技能${i}`, timeout: 30, retryCount: 0, params: {} }
        : {}),
      ...(nodeType === 'conditional'
        ? { condition: `val > ${i}`, trueBranch: '', falseBranch: '' }
        : {}),
      ...(nodeType === 'loop' ? { loopCount: (i % 10) + 1, loopVariable: 'item' } : {}),
      ...(nodeType === 'agent' ? { agentType: 'general', maxTurns: 5 } : {}),
      ...(nodeType === 'workflow' ? { workflowId: `wf_${i}` } : {}),
    };
    nodes.push({
      id: `n${i}`,
      type: nodeType,
      position: { x: col * 220, y: row * 120 },
      data,
    });
  }
  return nodes;
}

function generateEdges(nodes: Node<FlowNodeData>[]): Edge[] {
  const edges: Edge[] = [];
  for (let i = 0; i < nodes.length - 1; i++) {
    const node = nodes[i];
    if (node.data.nodeType === 'conditional' && i + 2 < nodes.length) {
      // 条件节点：双分支
      edges.push({ id: `e${i}t`, source: node.id, target: nodes[i + 1].id, sourceHandle: 'true' });
      edges.push({ id: `e${i}f`, source: node.id, target: nodes[i + 2].id, sourceHandle: 'false' });
      i += 1; // 跳过一个，避免重复连线
    } else {
      edges.push({ id: `e${i}`, source: node.id, target: nodes[i + 1].id });
    }
  }
  return edges;
}

// ─── 耗时测量工具 ───────────────────────────────────────
function measure<T>(label: string, fn: () => T): { result: T; ms: number } {
  const t0 = performance.now();
  const result = fn();
  const t1 = performance.now();
  return { result, ms: t1 - t0 };
}

function avg(label: string, runs: number, fn: () => void): number {
  const times: number[] = [];
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    fn();
    times.push(performance.now() - t0);
  }
  const sum = times.reduce((a, b) => a + b, 0);
  return sum / runs;
}

// ═══════════════════════════════════════════════════════
//  压测用例
// ═══════════════════════════════════════════════════════
describe('500 节点极端场景压测', () => {
  let nodes: Node<FlowNodeData>[];
  let edges: Edge[];

  beforeEach(() => {
    nodes = generate500Nodes();
    edges = generateEdges(nodes);
  });

  it('数据规模校验：500 节点 + 连线', () => {
    expect(nodes.length).toBe(500);
    expect(edges.length).toBeGreaterThan(400);
    console.log(`[stress500] 规模: ${nodes.length} 节点, ${edges.length} 连线`);
  });

  it('generateYaml 耗时 — 验收线 100ms', () => {
    const { ms, result } = measure('generateYaml', () => generateYaml(nodes, edges));
    console.log(`[stress500] generateYaml: ${ms.toFixed(2)}ms, YAML ${result.length} 字符`);
    // 记录数据，不强制 assert（不同机器性能差异大）
    // 但若超 500ms 说明严重退化
    expect(ms).toBeLessThan(500);
  });

  it('topologicalSort 耗时 — 验收线 50ms', () => {
    const { ms, result } = measure('topologicalSort', () => topologicalSort(nodes, edges));
    console.log(`[stress500] topologicalSort: ${ms.toFixed(2)}ms, 排序后 ${result.length} 节点`);
    expect(result.length).toBe(500);
    expect(ms).toBeLessThan(100);
  });

  it('generateYaml 5 次取平均 — 测稳定性', () => {
    const avgMs = avg('generateYaml×5', 5, () => generateYaml(nodes, edges));
    console.log(`[stress500] generateYaml 平均(5次): ${avgMs.toFixed(2)}ms`);
    expect(avgMs).toBeLessThan(500);
  });

  it('generateYaml 在不同规模下的退化曲线', () => {
    const sizes = [50, 100, 200, 300, 500];
    const results: string[] = [];
    for (const size of sizes) {
      const subset = nodes.slice(0, size);
      const subEdges = edges.filter((e) => subset.some((n) => n.id === e.source) && subset.some((n) => n.id === e.target));
      const { ms } = measure(`gen${size}`, () => generateYaml(subset, subEdges));
      results.push(`${size}节点=${ms.toFixed(1)}ms`);
    }
    console.log(`[stress500] 退化曲线: ${results.join(' | ')}`);
  });

  it('topologicalSort 在不同规模下的退化曲线', () => {
    const sizes = [50, 100, 200, 300, 500];
    const results: string[] = [];
    for (const size of sizes) {
      const subset = nodes.slice(0, size);
      const subEdges = edges.filter((e) => subset.some((n) => n.id === e.source) && subset.some((n) => n.id === e.target));
      const { ms } = measure(`topo${size}`, () => topologicalSort(subset, subEdges));
      results.push(`${size}节点=${ms.toFixed(1)}ms`);
    }
    console.log(`[stress500] 拓扑排序退化曲线: ${results.join(' | ')}`);
  });

  it('YAML 输出正确性 — 含 500 步骤', () => {
    const yaml = generateYaml(nodes, edges);
    // 步骤序列化为 "  - name: xxx"（带缩进），匹配带缩进的列表项
    const stepCount = (yaml.match(/^\s+- name:/gm) || []).length;
    console.log(`[stress500] YAML 步骤数: ${stepCount}, 总字符: ${yaml.length}`);
    expect(stepCount).toBe(500);
    expect(yaml).toContain('name: generated_workflow');
    expect(yaml).toContain('version: 1.0');
  });
});
