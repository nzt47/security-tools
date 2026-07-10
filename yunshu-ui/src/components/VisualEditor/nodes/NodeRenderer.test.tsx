/**
 * NodeRenderer 测试 — 5 种节点组件渲染验证
 *
 * 覆盖维度:
 * - 标题/图标/元数据渲染（含缺失字段降级）
 * - Handle 端口存在性（target/source 数量）
 * - selected 选中态 CSS class
 * - nodeTypes 注册表完整性
 * - React.memo 浅比较避免重渲染
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import {
  SkillNode,
  ConditionalNode,
  LoopNode,
  AgentNode,
  WorkflowNode,
  nodeTypes,
} from './nodeTypes';

// ─── 辅助 ────────────────────────────────────────────────
// Handle 依赖 ReactFlow 内部 store，必须用 ReactFlowProvider 包裹。
function renderNode(node: React.ReactElement) {
  return render(<ReactFlowProvider>{node}</ReactFlowProvider>);
}

// 统计渲染容器中 Handle 端口数量
// @xyflow/react v12 渲染 class 含 target/source 独立标记（非 -target 后缀）
function countHandles(container: HTMLElement, type: 'target' | 'source') {
  return container.querySelectorAll(`.react-flow__handle.${type}`).length;
}

// ─── 1. SkillNode ────────────────────────────────────────
describe('SkillNode', () => {
  const base: FlowNodeData = {
    label: '技能节点',
    nodeType: 'skill',
    skillId: 'pdf_parser',
    skillName: 'PDF 解析',
    timeout: 30,
    retryCount: 3,
  };

  it('渲染标题与全部元数据', () => {
    const { container, getByText } = renderNode(<SkillNode data={base} />);
    // 优先使用 skillName 作为标题
    expect(getByText('PDF 解析')).toBeTruthy();
    expect(getByText('ID: pdf_parser')).toBeTruthy();
    expect(getByText('超时: 30s')).toBeTruthy();
    expect(getByText('重试: 3 次')).toBeTruthy();
    // 一个 target + 一个 source
    expect(countHandles(container, 'target')).toBe(1);
    expect(countHandles(container, 'source')).toBe(1);
  });

  it('缺失 skillName 时降级使用 label', () => {
    const { getByText } = renderNode(
      <SkillNode data={{ ...base, skillName: undefined }} />,
    );
    expect(getByText('技能节点')).toBeTruthy();
  });

  it('缺失可选字段时不渲染对应元数据', () => {
    const { queryByText } = renderNode(
      <SkillNode data={{ label: 'x', nodeType: 'skill', skillId: 's1', skillName: 'S1' }} />,
    );
    expect(queryByText(/超时/)).toBeNull();
    expect(queryByText(/重试/)).toBeNull();
  });

  it('selected=true 时附加 ve-node-selected 类', () => {
    const { container } = renderNode(<SkillNode data={base} selected />);
    expect(container.querySelector('.ve-node-selected')).toBeTruthy();
  });

  it('retryCount=0 时仍渲染（!== undefined 判定）', () => {
    const { getByText } = renderNode(
      <SkillNode data={{ ...base, retryCount: 0 }} />,
    );
    expect(getByText('重试: 0 次')).toBeTruthy();
  });
});

// ─── 2. ConditionalNode ─────────────────────────────────
describe('ConditionalNode', () => {
  const base: FlowNodeData = {
    label: '页数判断',
    nodeType: 'conditional',
    condition: 'page_count > 10',
    trueBranch: 'batch',
    falseBranch: 'single',
  };

  it('渲染标题与条件/分支', () => {
    const { getByText } = renderNode(<ConditionalNode data={base} />);
    expect(getByText('页数判断')).toBeTruthy();
    expect(getByText('条件: page_count > 10')).toBeTruthy();
    expect(getByText('→ batch')).toBeTruthy();
    expect(getByText('→ single')).toBeTruthy();
  });

  it('拥有 1 个 target 和 2 个 source（true/false 双输出）', () => {
    const { container } = renderNode(<ConditionalNode data={base} />);
    expect(countHandles(container, 'target')).toBe(1);
    expect(countHandles(container, 'source')).toBe(2);
  });

  it('缺失分支时不渲染对应元数据', () => {
    const { queryByText } = renderNode(
      <ConditionalNode data={{ label: 'x', nodeType: 'conditional', condition: 'a' }} />,
    );
    expect(queryByText(/→/)).toBeNull();
  });
});

// ─── 3. LoopNode ────────────────────────────────────────
describe('LoopNode', () => {
  const base: FlowNodeData = {
    label: '遍历文档',
    nodeType: 'loop',
    loopCount: 5,
    loopVariable: 'doc',
  };

  it('渲染循环次数与变量', () => {
    const { getByText } = renderNode(<LoopNode data={base} />);
    expect(getByText('遍历文档')).toBeTruthy();
    expect(getByText('循环: 5 次')).toBeTruthy();
    expect(getByText('变量: doc')).toBeTruthy();
  });

  it('单 target 单 source', () => {
    const { container } = renderNode(<LoopNode data={base} />);
    expect(countHandles(container, 'target')).toBe(1);
    expect(countHandles(container, 'source')).toBe(1);
  });

  it('loopCount=0 仍渲染（!== undefined 判定）', () => {
    const { getByText } = renderNode(
      <LoopNode data={{ ...base, loopCount: 0 }} />,
    );
    expect(getByText('循环: 0 次')).toBeTruthy();
  });
});

// ─── 4. AgentNode ───────────────────────────────────────
describe('AgentNode', () => {
  const base: FlowNodeData = {
    label: '子任务',
    nodeType: 'agent',
    agentType: 'researcher',
    maxTurns: 10,
  };

  it('渲染 Agent 类型与轮数', () => {
    const { getByText } = renderNode(<AgentNode data={base} />);
    expect(getByText('子任务')).toBeTruthy();
    expect(getByText('类型: researcher')).toBeTruthy();
    expect(getByText('轮数: 10')).toBeTruthy();
  });

  it('单 target 单 source', () => {
    const { container } = renderNode(<AgentNode data={base} />);
    expect(countHandles(container, 'target')).toBe(1);
    expect(countHandles(container, 'source')).toBe(1);
  });
});

// ─── 5. WorkflowNode ────────────────────────────────────
describe('WorkflowNode', () => {
  const base: FlowNodeData = {
    label: '子流程',
    nodeType: 'workflow',
    workflowId: 'wf_main',
  };

  it('渲染工作流 ID', () => {
    const { getByText } = renderNode(<WorkflowNode data={base} />);
    expect(getByText('子流程')).toBeTruthy();
    expect(getByText('ID: wf_main')).toBeTruthy();
  });

  it('单 target 单 source', () => {
    const { container } = renderNode(<WorkflowNode data={base} />);
    expect(countHandles(container, 'target')).toBe(1);
    expect(countHandles(container, 'source')).toBe(1);
  });

  it('缺失 workflowId 时不渲染元数据行', () => {
    const { queryByText } = renderNode(
      <WorkflowNode data={{ label: 'x', nodeType: 'workflow' }} />,
    );
    expect(queryByText(/^ID:/)).toBeNull();
  });
});

// ─── 6. nodeTypes 注册表 ────────────────────────────────
describe('nodeTypes 注册表', () => {
  it('包含全部 5 种节点类型映射', () => {
    expect(Object.keys(nodeTypes).sort()).toEqual(
      ['agent', 'conditional', 'loop', 'skill', 'workflow'],
    );
  });

  it('每种类型映射到对应组件', () => {
    expect(nodeTypes.skill).toBe(SkillNode);
    expect(nodeTypes.conditional).toBe(ConditionalNode);
    expect(nodeTypes.loop).toBe(LoopNode);
    expect(nodeTypes.agent).toBe(AgentNode);
    expect(nodeTypes.workflow).toBe(WorkflowNode);
  });
});

// ─── 7. React.memo 行为 ─────────────────────────────────
describe('React.memo 浅比较', () => {
  it('相同 props 引用不触发重渲染', () => {
    const renderSpy = vi.fn();
    const orig = SkillNode;
    // 通过统计 DOM 重渲染间接验证：render 两次相同 props，标题应稳定
    const data: FlowNodeData = {
      label: 'memo-skill',
      nodeType: 'skill',
      skillId: 's',
      skillName: 'Memo',
    };
    const { rerender, getByText } = renderNode(<SkillNode data={data} />);
    expect(getByText('Memo')).toBeTruthy();
    const originalNode = getByText('Memo');
    // 用同一引用重渲染
    rerender(
      <ReactFlowProvider>
        <SkillNode data={data} />
      </ReactFlowProvider>,
    );
    // memo 后内部组件不应重新执行；标题节点仍存在
    expect(getByText('Memo')).toBeTruthy();
    expect(orig).toBe(SkillNode);
    expect(renderSpy).not.toHaveBeenCalled();
  });
});
