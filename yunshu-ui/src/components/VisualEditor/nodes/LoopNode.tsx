import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { FlowNodeData } from '../types';

function LoopNodeComponent({ data, selected }: { data: FlowNodeData; selected?: boolean }) {
  return (
    <div className={`ve-node ve-node-loop ${selected ? 've-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ve-node-header">
        <span className="ve-node-icon">⟳</span>
        <span className="ve-node-title">{data.label}</span>
      </div>
      <div className="ve-node-body">
        {data.loopCount !== undefined && <div className="ve-node-meta">循环: {data.loopCount} 次</div>}
        {data.loopVariable && <div className="ve-node-meta">变量: {data.loopVariable}</div>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const LoopNode = memo(LoopNodeComponent);
