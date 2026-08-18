import type { ComponentType } from 'react';
import type { NodeTypes } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import { SkillNode } from './SkillNode';
import { ConditionalNode } from './ConditionalNode';
import { LoopNode } from './LoopNode';
import { AgentNode } from './AgentNode';
import { WorkflowNode } from './WorkflowNode';

export type NodeComponent = ComponentType<{ data: FlowNodeData; selected?: boolean }>;

// ReactFlow 的 NodeTypes 期望组件接收完整 NodeProps（id/type/dragging 等），
// 但节点组件运行时只用 data/selected，多余 props 被 React 忽略，断言安全。
export const nodeTypes = {
  skill: SkillNode,
  conditional: ConditionalNode,
  loop: LoopNode,
  agent: AgentNode,
  workflow: WorkflowNode,
} as unknown as NodeTypes;

export { SkillNode, ConditionalNode, LoopNode, AgentNode, WorkflowNode };
