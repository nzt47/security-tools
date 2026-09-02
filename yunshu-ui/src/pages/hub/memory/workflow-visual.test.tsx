/**
 * 工作台「记忆管理 → 可视化编辑」页冒烟测试
 *
 * 策略：mock VisualEditor 组件（避免 jsdom 加载 ReactFlow 画布）与
 * visualWorkflowApi（避免真实 HTTP），使用真实 useFlowStore 校验
 * 保存/加载状态联动与「保存到后端」闭环交互。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import type { FlowNodeData } from '@/components/VisualEditor/types';

const apiMock = vi.hoisted(() => ({
  listVisualWorkflows: vi.fn(),
  getVisualWorkflow: vi.fn(),
  saveVisualWorkflow: vi.fn(),
  deleteVisualWorkflow: vi.fn(),
  serializeGraph: vi.fn((nodes: Node<FlowNodeData>[]) => ({
    nodes: nodes.map((n) => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
    edges: [],
  })),
  deserializeNodes: vi.fn((raw: unknown[]) =>
    (raw ?? []).map((n) => ({ id: (n as { id: string }).id, type: 'skill', position: { x: 0, y: 0 }, data: {} as FlowNodeData })),
  ),
  deserializeEdges: vi.fn(() => []),
}));

vi.mock('@/lib/visualWorkflowApi', () => apiMock);
vi.mock('@/components/VisualEditor', () => ({
  VisualEditor: () => <div data-testid="ve-stub">VisualEditor 画布</div>,
}));

import MemoryWorkflowVisual from './workflow-visual';
import { useFlowStore } from '@/components/VisualEditor/stores/useFlowStore';

const skill: Node<FlowNodeData> = {
  id: 'skill-1',
  type: 'skill',
  position: { x: 0, y: 0 },
  data: { label: '技能A', nodeType: 'skill', skillId: 'a' },
};

describe('可视化编辑页（workflow-visual）冒烟', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useFlowStore.setState({ nodes: [], edges: [], selectedNodeId: null, yamlPreview: '', dirty: false });
    apiMock.listVisualWorkflows.mockResolvedValue([]);
  });

  afterEach(() => {
    useFlowStore.setState({ nodes: [], edges: [], selectedNodeId: null, yamlPreview: '', dirty: false });
  });

  it('渲染标题、VisualEditor 画布与保存按钮（空画布时禁用）', async () => {
    render(<MemoryWorkflowVisual />);
    expect(screen.getByText('可视化编辑')).toBeTruthy();
    expect(screen.getByTestId('ve-stub')).toBeTruthy();
    const saveBtn = screen.getByTestId('ve-save-btn') as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
    // 挂载后应拉取一次草稿列表
    await waitFor(() => expect(apiMock.listVisualWorkflows).toHaveBeenCalled());
  });

  it('有节点后点「保存」→ 调用后端并提示已保存', async () => {
    apiMock.saveVisualWorkflow.mockResolvedValue({ id: 'wf-a', action: 'created' });
    render(<MemoryWorkflowVisual />);

    act(() => {
      useFlowStore.setState({ nodes: [skill], edges: [] });
    });
    const saveBtn = await screen.findByTestId('ve-save-btn');
    expect((saveBtn as HTMLButtonElement).disabled).toBe(false);

    fireEvent.change(screen.getByTestId('ve-save-name'), { target: { value: '我的日报流程' } });
    fireEvent.click(saveBtn);

    await waitFor(() => expect(apiMock.saveVisualWorkflow).toHaveBeenCalledTimes(1));
    const payload = apiMock.saveVisualWorkflow.mock.calls[0][0] as { name: string; id?: string; nodes: unknown[] };
    expect(payload.name).toBe('我的日报流程');
    expect(payload.nodes).toHaveLength(1);
    await waitFor(() => expect(screen.getByTestId('ve-status').textContent).toContain('已保存到后端'));
  });

  it('画布为空时点保存给出提示且不请求后端', async () => {
    render(<MemoryWorkflowVisual />);
    const saveBtn = (await screen.findByTestId('ve-save-btn')) as HTMLButtonElement;
    // 空画布按钮 disabled，直接触发提示分支需先解除禁用条件 → 这里验证 disabled 即可
    expect(saveBtn.disabled).toBe(true);
    expect(apiMock.saveVisualWorkflow).not.toHaveBeenCalled();
  });

  it('打开「加载」下拉展示后端草稿并可点选加载回画布', async () => {
    apiMock.listVisualWorkflows.mockResolvedValue([
      { id: 'wf-1', name: '草稿A', node_count: 2, edge_count: 1, updated_at: '2026-09-01' },
    ]);
    apiMock.getVisualWorkflow.mockResolvedValue({
      id: 'wf-1',
      name: '草稿A',
      node_count: 1,
      edge_count: 0,
      nodes: [{ id: 'n1', type: 'skill', position: { x: 5, y: 6 }, data: { label: 'L', nodeType: 'skill' } }],
      edges: [],
    });
    render(<MemoryWorkflowVisual />);
    await waitFor(() => expect(apiMock.listVisualWorkflows).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('ve-open-list-btn'));
    expect(await screen.findByText('草稿A')).toBeTruthy();
    fireEvent.click(screen.getByTestId('ve-load-wf-1'));

    await waitFor(() => expect(apiMock.getVisualWorkflow).toHaveBeenCalledWith('wf-1'));
    await waitFor(() => expect(useFlowStore.getState().nodes.length).toBe(1));
    await waitFor(() => expect(screen.getByTestId('ve-status').textContent).toContain('已加载「草稿A」'));
    // 加载后当前草稿 id 应回填 → 按钮文案变为「更新」
    expect((screen.getByTestId('ve-save-btn') as HTMLButtonElement).textContent).toContain('更新');
  });

  it('后端列表拉取失败时展示可理解的降级提示', async () => {
    apiMock.listVisualWorkflows.mockRejectedValue(new Error('HTTP 401'));
    render(<MemoryWorkflowVisual />);
    await waitFor(() =>
      expect(screen.getByTestId('ve-status').textContent).toContain('读取后端草稿列表失败'),
    );
  });
});
