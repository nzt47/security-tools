import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { FlowNodeData } from '../types';

function WorkflowNodeComponent({ data, selected }: { data: FlowNodeData; selected?: boolean }) {
  return (
    <div className={`ve-node ve-node-workflow ${selected ? 've-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ve-node-header">
        <span className="ve-node-icon">📦</span>
        <span className="ve-node-title">{data.label}</span>
      </div>
      <div className="ve-node-body">
        {data.workflowId && <div className="ve-node-meta">ID: {data.workflowId}</div>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const WorkflowNode = memo(WorkflowNodeComponent);
