import type { Node } from '@xyflow/react';

export type NodeType = 'skill' | 'workflow' | 'agent' | 'conditional' | 'loop';

// @xyflow/react v12 的 Node<NodeData> 要求 NodeData extends Record<string, unknown>
// interface 默认不满足该约束，需用 type 字面量形式声明
export type FlowNodeData = {
  label: string;
  nodeType: NodeType;
  skillId?: string;
  skillName?: string;
  params?: Record<string, unknown>;
  condition?: string;
  trueBranch?: string;
  falseBranch?: string;
  loopCount?: number;
  loopVariable?: string;
  description?: string;
  timeout?: number;
  retryCount?: number;
  agentType?: string;
  maxTurns?: number;
  workflowId?: string;
};

export type FlowNode = Node<FlowNodeData>;

export interface NodeProps {
  id: string;
  data: FlowNodeData;
  selected?: boolean;
}

export interface ValidationRule {
  field: string;
  type: 'string' | 'number' | 'object';
  required: boolean;
  min?: number;
  max?: number;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}
