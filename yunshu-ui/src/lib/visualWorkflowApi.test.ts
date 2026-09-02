/**
 * visualWorkflowApi 单元测试
 *
 * 覆盖：
 * - 画布 → 平面 JSON（丢弃 xyflow 运行时附加字段）
 * - 平面 JSON → 画布 往返（含条件分支 sourceHandle）
 * - 未知 nodeType 兜底
 * - HTTP 客户端（列表/详情/保存/删除，含 Authorization 注入）
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import type { FlowNodeData } from '../components/VisualEditor/types';
import {
  serializeGraph,
  deserializeNodes,
  deserializeEdges,
  toPlainNode,
  listVisualWorkflows,
  getVisualWorkflow,
  saveVisualWorkflow,
  deleteVisualWorkflow,
} from './visualWorkflowApi';
import { clearApiToken, setApiToken } from './apiToken';

function makeNode(overrides: Partial<Node<FlowNodeData>> = {}): Node<FlowNodeData> {
  return {
    id: 'skill-1',
    type: 'skill',
    position: { x: 10, y: 20 },
    data: {
      label: '收集',
      nodeType: 'skill',
      skillId: 'collector',
      skillName: '数据收集',
      timeout: 30,
      retryCount: 0,
      params: { source: 'api' },
    },
    // xyflow 运行时会附加的字段（落盘时应被剔除）
    measured: { width: 160, height: 60 },
    selected: true,
    dragging: false,
    ...overrides,
  };
}

describe('serializeGraph → 平面 JSON', () => {
  it('toPlainNode 只保留 id/type/position/data', () => {
    const plain = toPlainNode(makeNode());
    expect(plain).toEqual({
      id: 'skill-1',
      type: 'skill',
      position: { x: 10, y: 20 },
      data: expect.objectContaining({ skillId: 'collector', nodeType: 'skill' }),
    });
    expect('measured' in plain).toBe(false);
    expect('selected' in plain).toBe(false);
    expect('dragging' in plain).toBe(false);
  });

  it('serializeGraph 收敛整图（条件分支 sourceHandle 保留）', () => {
    const nodes = [
      makeNode(),
      makeNode({
        id: 'cond-2',
        type: 'conditional',
        data: {
          label: '是否继续', nodeType: 'conditional',
          condition: 'count > 0', trueBranch: '', falseBranch: '',
        },
      }),
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'skill-1', target: 'cond-2', animated: true },
      { id: 'e2', source: 'cond-2', target: 'skill-1', sourceHandle: 'true', animated: true },
    ];
    const graph = serializeGraph(nodes, edges);
    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toHaveLength(2);
    expect(graph.edges[0].sourceHandle).toBeUndefined();
    expect(graph.edges[1].sourceHandle).toBe('true');
    expect('animated' in graph.edges[0]).toBe(false);
  });
});

describe('平面 JSON → 画布（加载往返）', () => {
  it('deserializeNodes 重建最小 xyflow 节点结构', () => {
    const plain = serializeGraph([makeNode()], []).nodes;
    const nodes = deserializeNodes(plain);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].id).toBe('skill-1');
    expect(nodes[0].type).toBe('skill');
    expect(nodes[0].position).toEqual({ x: 10, y: 20 });
    expect(nodes[0].data.skillId).toBe('collector');
    expect(nodes[0].data.nodeType).toBe('skill');
  });

  it('往返保持图结构与条件分支连线', () => {
    const original = serializeGraph(
      [
        makeNode(),
        makeNode({
          id: 'cond-2',
          type: 'conditional',
          data: {
            label: '分支', nodeType: 'conditional',
            condition: 'a > b', trueBranch: '', falseBranch: '',
          },
        }),
      ],
      [
        { id: 'e1', source: 'skill-1', target: 'cond-2' },
        { id: 'e2', source: 'cond-2', target: 'skill-1', sourceHandle: 'false' },
      ],
    );
    const nodes = deserializeNodes(original.nodes);
    const edges = deserializeEdges(original.edges);
    expect(nodes.map((n) => n.id)).toEqual(['skill-1', 'cond-2']);
    expect(edges.find((e) => e.id === 'e2')?.sourceHandle).toBe('false');
    expect(edges.find((e) => e.id === 'e1')?.sourceHandle).toBeUndefined();
  });

  it('未知 nodeType / 脏数据兜底为 skill 且不抛异常', () => {
    const nodes = deserializeNodes([
      { id: 'x', type: 'unknown-type', position: { x: 1, y: 2 }, data: { label: 'X', nodeType: 'loop', loopCount: 3, loopVariable: 'i' } },
      // nodeType 非法 → 兜底 skill
      { id: 'y', type: 'skill', position: { x: 0, y: 0 }, data: { label: 'Y', nodeType: 'weird' as never } },
      // 无效项直接过滤
      null as never,
      { id: '' } as never,
    ]);
    expect(nodes).toHaveLength(2);
    expect(nodes[0].type).toBe('loop'); // type 非法但 data.nodeType 合法 → 用 data
    expect(nodes[1].type).toBe('skill'); // data.nodeType 非法 → 兜底
  });
});

describe('HTTP 客户端', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    clearApiToken();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearApiToken();
  });

  it('listVisualWorkflows 解析 {items:[...]}', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({
        ok: true,
        items: [{ id: 'wf-1', name: 'A', node_count: 2, edge_count: 1 }],
        total: 1,
      }),
    });
    const items = await listVisualWorkflows();
    expect(items).toHaveLength(1);
    expect(items[0].name).toBe('A');
    expect(fetchMock).toHaveBeenCalledWith('/api/visual-workflows', expect.anything());
  });

  it('saveVisualWorkflow POST 携带 body 与 Authorization（配置令牌后）', async () => {
    setApiToken('tok-123');
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ ok: true, workflow: { id: 'wf-new' }, action: 'created' }),
    });
    const res = await saveVisualWorkflow({
      name: '测试流程',
      nodes: [toPlainNode(makeNode())],
      edges: [],
    });
    expect(res).toEqual({ id: 'wf-new', action: 'created' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/visual-workflows');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
    const body = JSON.parse(String(init.body));
    expect(body.name).toBe('测试流程');
    expect(body.nodes[0].id).toBe('skill-1');
  });

  it('getVisualWorkflow / deleteVisualWorkflow 命中正确 URL', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ ok: true, workflow: { id: 'wf-1', nodes: [], edges: [] } }),
    });
    const detail = await getVisualWorkflow('wf-1');
    expect(detail.id).toBe('wf-1');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/visual-workflows/wf-1');

    await deleteVisualWorkflow('wf-1');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/visual-workflows/wf-1');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('DELETE');
  });
});
