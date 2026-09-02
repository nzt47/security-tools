/**
 * useFlowStore.loadGraph —— 整图加载（保存后重新打开 / 从后端加载）测试
 *
 * 验证：
 * - loadGraph 整图替换 nodes/edges
 * - 历史栈被清空（不可跨图撤销/重做）
 * - yamlPreview 同步刷新
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import { useFlowStore } from '../stores/useFlowStore';
import { generateYaml } from '../generator/CodeGenerator';

function skillNode(id: string, x = 0, y = 0): Node<FlowNodeData> {
  return {
    id,
    type: 'skill',
    position: { x, y },
    data: { label: `技能${id}`, nodeType: 'skill', skillId: id, skillName: `技能${id}` },
  };
}

describe('useFlowStore.loadGraph', () => {
  beforeEach(() => {
    // 直接重置（不走 clearCanvas，避免污染历史栈）
    useFlowStore.setState({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      yamlPreview: '',
      dirty: false,
    });
    // loadGraph 会清空栈；这里手动先制造一次可撤销操作再验证清栈
  });

  it('loadGraph 替换整图并清空撤销/重做历史', () => {
    const s = useFlowStore.getState();
    s.addNode('skill', { x: 0, y: 0 }, '旧图节点');
    expect(useFlowStore.getState().canUndo()).toBe(true);

    const nodes = [skillNode('a', 10, 20), skillNode('b', 300, 20)];
    const edges: Edge[] = [{ id: 'e1', source: 'a', target: 'b' }];
    useFlowStore.getState().loadGraph(nodes, edges);

    const after = useFlowStore.getState();
    expect(after.nodes.map((n) => n.id)).toEqual(['a', 'b']);
    expect(after.edges).toHaveLength(1);
    expect(after.selectedNodeId).toBeNull();
    expect(after.canUndo()).toBe(false);
    expect(after.canRedo()).toBe(false);
  });

  it('loadGraph 后 yamlPreview 与 generateYaml 一致', () => {
    const nodes = [skillNode('a'), skillNode('b', 300, 0)];
    const edges: Edge[] = [{ id: 'e1', source: 'a', target: 'b' }];
    useFlowStore.getState().loadGraph(nodes, edges);
    expect(useFlowStore.getState().yamlPreview).toBe(generateYaml(nodes, edges));
  });

  it('undo 在 loadGraph 之后为 no-op（不回到旧图）', () => {
    const s = useFlowStore.getState();
    s.addNode('skill', { x: 0, y: 0 }, '将被替换');
    useFlowStore.getState().loadGraph([skillNode('only')], []);
    useFlowStore.getState().undo();
    expect(useFlowStore.getState().nodes).toHaveLength(1);
    expect(useFlowStore.getState().nodes[0].id).toBe('only');
  });
});
