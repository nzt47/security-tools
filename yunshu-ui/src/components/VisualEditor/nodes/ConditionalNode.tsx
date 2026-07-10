import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { FlowNodeData } from '../types';

function ConditionalNodeComponent({ data, selected }: { data: FlowNodeData; selected?: boolean }) {
  return (
    <div className={`ve-node ve-node-conditional ${selected ? 've-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ve-node-header">
        <span className="ve-node-icon">◇</span>
        <span className="ve-node-title">{data.label}</span>
      </div>
      <div className="ve-node-body">
        {data.condition && <div className="ve-node-meta">条件: {data.condition}</div>}
        {data.trueBranch && <div className="ve-node-meta ve-branch-true">→ {data.trueBranch}</div>}
        {data.falseBranch && <div className="ve-node-meta ve-branch-false">→ {data.falseBranch}</div>}
      </div>
      <Handle type="source" position={Position.Right} id="true" style={{ top: '40%' }} />
      <Handle type="source" position={Position.Right} id="false" style={{ top: '70%' }} />
    </div>
  );
}

export const ConditionalNode = memo(ConditionalNodeComponent);
