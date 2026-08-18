import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { FlowNodeData } from '../types';

function AgentNodeComponent({ data, selected }: { data: FlowNodeData; selected?: boolean }) {
  return (
    <div className={`ve-node ve-node-agent ${selected ? 've-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ve-node-header">
        <span className="ve-node-icon">🤖</span>
        <span className="ve-node-title">{data.label}</span>
      </div>
      <div className="ve-node-body">
        {data.agentType && <div className="ve-node-meta">类型: {data.agentType}</div>}
        {data.maxTurns !== undefined && <div className="ve-node-meta">轮数: {data.maxTurns}</div>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const AgentNode = memo(AgentNodeComponent);
