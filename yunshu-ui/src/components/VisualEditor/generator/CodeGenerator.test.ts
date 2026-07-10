/**
 * CodeGenerator 单元测试 — 图 → YAML 转换
 *
 * 覆盖：拓扑排序、5 种节点步骤映射、YAML 序列化、完整流程。
 */
import { describe, it, expect } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import {
  topologicalSort,
  nodeToStep,
  toYaml,
  generateYaml,
} from './CodeGenerator';

function makeNode(id: string, data: FlowNodeData, position = { x: 0, y: 0 }): Node<FlowNodeData> {
  return { id, type: data.nodeType, position, data };
}
function makeEdge(source: string, target: string, sourceHandle?: string): Edge {
  return { id: `e-${source}-${target}`, source, target, sourceHandle };
}

// ═══════════════════════════════════════════════════════
//  1. topologicalSort
// ═══════════════════════════════════════════════════════
describe('topologicalSort', () => {
  it('线性链 A→B→C 按依赖顺序排列', () => {
    const nodes = [
      makeNode('c', { label: 'C', nodeType: 'skill' }),
      makeNode('a', { label: 'A', nodeType: 'skill' }),
      makeNode('b', { label: 'B', nodeType: 'skill' }),
    ];
    const edges = [makeEdge('a', 'b'), makeEdge('b', 'c')];
    const sorted = topologicalSort(nodes, edges);
    expect(sorted.map((n) => n.id)).toEqual(['a', 'b', 'c']);
  });

  it('无边的节点保持原顺序', () => {
    const nodes = [
      makeNode('x', { label: 'X', nodeType: 'skill' }),
      makeNode('y', { label: 'Y', nodeType: 'skill' }),
    ];
    const sorted = topologicalSort(nodes, []);
    expect(sorted.map((n) => n.id)).toEqual(['x', 'y']);
  });

  it('分叉拓扑 A→B, A→C 正确处理', () => {
    const nodes = [
      makeNode('a', { label: 'A', nodeType: 'skill' }),
      makeNode('b', { label: 'B', nodeType: 'skill' }),
      makeNode('c', { label: 'C', nodeType: 'skill' }),
    ];
    const edges = [makeEdge('a', 'b'), makeEdge('a', 'c')];
    const sorted = topologicalSort(nodes, edges);
    expect(sorted[0].id).toBe('a');
    expect(sorted.slice(1).map((n) => n.id).sort()).toEqual(['b', 'c']);
  });

  it('环兜底：所有节点仍出现', () => {
    const nodes = [
      makeNode('a', { label: 'A', nodeType: 'skill' }),
      makeNode('b', { label: 'B', nodeType: 'skill' }),
    ];
    const edges = [makeEdge('a', 'b'), makeEdge('b', 'a')];
    const sorted = topologicalSort(nodes, edges);
    expect(sorted.length).toBe(2);
  });

  it('空数组返回空', () => {
    expect(topologicalSort([], [])).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════
//  2. nodeToStep
// ═══════════════════════════════════════════════════════
describe('nodeToStep', () => {
  it('skill 节点映射', () => {
    const node = makeNode('s1', {
      label: 'PDF解析',
      nodeType: 'skill',
      skillId: 'pdf_parser',
      timeout: 30,
      retryCount: 3,
    });
    const step = nodeToStep(node, [], [node]);
    expect(step.type).toBe('skill');
    expect(step.name).toBe('PDF解析');
    expect(step.skill_id).toBe('pdf_parser');
    expect(step.timeout).toBe(30);
    expect(step.retry).toBe(3);
    expect(step.next).toBeNull();
  });

  it('conditional 节点映射 true/false 分支', () => {
    const cond = makeNode('c1', { label: '判断', nodeType: 'conditional', condition: 'x>1', trueBranch: 'T', falseBranch: 'F' });
    const tNode = makeNode('t1', { label: 'T', nodeType: 'skill' });
    const fNode = makeNode('f1', { label: 'F', nodeType: 'skill' });
    const edges = [
      makeEdge('c1', 't1', 'true'),
      makeEdge('c1', 'f1', 'false'),
    ];
    const step = nodeToStep(cond, edges, [cond, tNode, fNode]);
    expect(step.type).toBe('conditional');
    expect(step.condition).toBe('x>1');
    expect(step.true_branch).toBe('T');
    expect(step.false_branch).toBe('F');
  });

  it('loop 节点映射', () => {
    const node = makeNode('l1', { label: '遍历', nodeType: 'loop', loopCount: 5, loopVariable: 'item' });
    const step = nodeToStep(node, [], [node]);
    expect(step.type).toBe('loop');
    expect(step.count).toBe(5);
    expect(step.variable).toBe('item');
  });

  it('agent 节点映射', () => {
    const node = makeNode('a1', { label: '助手', nodeType: 'agent', agentType: 'researcher', maxTurns: 10 });
    const step = nodeToStep(node, [], [node]);
    expect(step.type).toBe('agent');
    expect(step.agent_type).toBe('researcher');
    expect(step.max_turns).toBe(10);
  });

  it('workflow 节点映射', () => {
    const node = makeNode('w1', { label: '子流程', nodeType: 'workflow', workflowId: 'wf_main' });
    const step = nodeToStep(node, [], [node]);
    expect(step.type).toBe('workflow');
    expect(step.workflow_id).toBe('wf_main');
  });

  it('有连线时 next 指向下游节点 label', () => {
    const a = makeNode('a', { label: 'A', nodeType: 'skill' });
    const b = makeNode('b', { label: 'B', nodeType: 'skill' });
    const edges = [makeEdge('a', 'b')];
    const step = nodeToStep(a, edges, [a, b]);
    expect(step.next).toBe('B');
  });
});

// ═══════════════════════════════════════════════════════
//  3. toYaml
// ═══════════════════════════════════════════════════════
describe('toYaml', () => {
  it('序列化基本类型', () => {
    expect(toYaml(null)).toBe('null');
    expect(toYaml('hello')).toBe('hello');
    expect(toYaml(42)).toBe('42');
    expect(toYaml(true)).toBe('true');
  });

  it('序列化空数组/对象', () => {
    expect(toYaml([])).toBe('[]');
    expect(toYaml({})).toBe('{}');
  });

  it('序列化简单对象', () => {
    const result = toYaml({ name: 'test', value: 1 });
    expect(result).toContain('name: test');
    expect(result).toContain('value: 1');
  });

  it('序列化数组', () => {
    const result = toYaml({ items: ['a', 'b'] });
    expect(result).toContain('items:');
    expect(result).toContain('- a');
    expect(result).toContain('- b');
  });

  it('序列化嵌套对象', () => {
    const result = toYaml({ outer: { inner: 'val' } });
    expect(result).toContain('outer:');
    expect(result).toContain('inner: val');
  });
});

// ═══════════════════════════════════════════════════════
//  4. generateYaml 完整流程
// ═══════════════════════════════════════════════════════
describe('generateYaml', () => {
  it('空画布生成空步骤工作流', () => {
    const yaml = generateYaml([], []);
    expect(yaml).toContain('name: generated_workflow');
    expect(yaml).toContain('version: 1.0');
    expect(yaml).toContain('steps: []');
  });

  it('单节点生成有效 YAML', () => {
    const node = makeNode('s1', {
      label: 'PDF解析',
      nodeType: 'skill',
      skillId: 'pdf',
      skillName: 'PDF',
      timeout: 30,
      retryCount: 0,
    });
    const yaml = generateYaml([node], []);
    expect(yaml).toContain('name: PDF解析');
    expect(yaml).toContain('type: skill');
    expect(yaml).toContain('skill_id: pdf');
    expect(yaml).toContain('timeout: 30');
  });

  it('链式拓扑 A→B 生成有序步骤', () => {
    const a = makeNode('a', { label: 'A', nodeType: 'skill', skillId: 'sa', skillName: 'A' });
    const b = makeNode('b', { label: 'B', nodeType: 'skill', skillId: 'sb', skillName: 'B' });
    const edges = [makeEdge('a', 'b')];
    const yaml = generateYaml([b, a], edges); // 故意倒序输入
    expect(yaml.indexOf('name: A')).toBeLessThan(yaml.indexOf('name: B'));
    expect(yaml).toContain('next: B');
  });

  it('条件分支生成 true_branch/false_branch', () => {
    const cond = makeNode('c', { label: '判断', nodeType: 'conditional', condition: 'ok', trueBranch: '', falseBranch: '' });
    const t = makeNode('t', { label: '是', nodeType: 'skill', skillId: 'st', skillName: '是' });
    const f = makeNode('f', { label: '否', nodeType: 'skill', skillId: 'sf', skillName: '否' });
    const edges = [makeEdge('c', 't', 'true'), makeEdge('c', 'f', 'false')];
    const yaml = generateYaml([cond, t, f], edges);
    expect(yaml).toContain('true_branch: 是');
    expect(yaml).toContain('false_branch: 否');
  });
});
