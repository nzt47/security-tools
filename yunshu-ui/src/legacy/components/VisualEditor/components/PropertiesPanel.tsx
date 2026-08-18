/**
 * PropertiesPanel — 右侧属性编辑面板
 *
 * 选中节点时显示对应类型的可编辑字段，变更实时写回 store。
 * 未选中时显示提示。
 */
import { useMemo } from 'react';
import { useFlowStore } from '../stores/useFlowStore';
import type { FlowNodeData, NodeType } from '../types';

interface FieldDef {
  key: keyof FlowNodeData;
  label: string;
  type: 'text' | 'number' | 'textarea';
  placeholder?: string;
}

const FIELDS: Record<NodeType, FieldDef[]> = {
  skill: [
    { key: 'skillName', label: '技能名称', type: 'text', placeholder: '如 PDF 解析' },
    { key: 'skillId', label: '技能 ID', type: 'text', placeholder: '如 pdf_parser' },
    { key: 'timeout', label: '超时(秒)', type: 'number', placeholder: '1-3600' },
    { key: 'retryCount', label: '重试次数', type: 'number', placeholder: '0-10' },
  ],
  conditional: [
    { key: 'label', label: '节点名称', type: 'text' },
    { key: 'condition', label: '条件表达式', type: 'textarea', placeholder: '如 page_count > 10' },
    { key: 'trueBranch', label: 'True 分支', type: 'text' },
    { key: 'falseBranch', label: 'False 分支', type: 'text' },
  ],
  loop: [
    { key: 'label', label: '节点名称', type: 'text' },
    { key: 'loopCount', label: '循环次数', type: 'number', placeholder: '1-1000' },
    { key: 'loopVariable', label: '循环变量', type: 'text', placeholder: '如 item' },
  ],
  agent: [
    { key: 'label', label: '节点名称', type: 'text' },
    { key: 'agentType', label: 'Agent 类型', type: 'text', placeholder: '如 researcher' },
    { key: 'maxTurns', label: '最大轮数', type: 'number', placeholder: '1-50' },
  ],
  workflow: [
    { key: 'label', label: '节点名称', type: 'text' },
    { key: 'workflowId', label: '工作流 ID', type: 'text' },
  ],
};

export function PropertiesPanel() {
  const selectedNodeId = useFlowStore((s) => s.selectedNodeId);
  const nodes = useFlowStore((s) => s.nodes);
  const updateNodeData = useFlowStore((s) => s.updateNodeData);
  const removeNode = useFlowStore((s) => s.removeNode);
  const duplicateNode = useFlowStore((s) => s.duplicateNode);

  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  );

  if (!selected) {
    return (
      <aside className="ve-properties ve-properties-empty" data-testid="ve-properties">
        <div className="ve-properties-header">属性</div>
        <div className="ve-properties-placeholder">未选中节点</div>
      </aside>
    );
  }

  const data = selected.data;
  const fields = FIELDS[data.nodeType] || [];

  return (
    <aside className="ve-properties" data-testid="ve-properties">
      <div className="ve-properties-header">属性 — {data.label}</div>
      <div className="ve-properties-body">
        {fields.map((f) => (
          <div key={f.key} className="ve-field">
            <label className="ve-field-label">{f.label}</label>
            {f.type === 'textarea' ? (
              <textarea
                className="ve-field-input"
                value={(data[f.key] as string) || ''}
                placeholder={f.placeholder}
                onChange={(e) => updateNodeData(selected.id, { [f.key]: e.target.value })}
              />
            ) : (
              <input
                className="ve-field-input"
                type={f.type}
                value={(data[f.key] as string | number) ?? ''}
                placeholder={f.placeholder}
                onChange={(e) =>
                  updateNodeData(selected.id, {
                    [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value,
                  })
                }
              />
            )}
          </div>
        ))}
      </div>
      <div className="ve-properties-actions">
        <button
          className="ve-btn ve-btn-secondary"
          onClick={() => duplicateNode(selected.id)}
          data-testid="ve-duplicate-btn"
        >
          复制
        </button>
        <button
          className="ve-btn ve-btn-danger"
          onClick={() => removeNode(selected.id)}
          data-testid="ve-delete-btn"
        >
          删除
        </button>
      </div>
    </aside>
  );
}
