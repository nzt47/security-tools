import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { FlowNodeData } from '../types';

function SkillNodeComponent({ data, selected }: { data: FlowNodeData; selected?: boolean }) {
  return (
    <div className={`ve-node ve-node-skill ${selected ? 've-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="ve-node-header">
        <span className="ve-node-icon">⚙</span>
        <span className="ve-node-title">{data.skillName || data.label}</span>
      </div>
      <div className="ve-node-body">
        {data.skillId && <div className="ve-node-meta">ID: {data.skillId}</div>}
        {data.timeout && <div className="ve-node-meta">超时: {data.timeout}s</div>}
        {data.retryCount !== undefined && <div className="ve-node-meta">重试: {data.retryCount} 次</div>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const SkillNode = memo(SkillNodeComponent);
