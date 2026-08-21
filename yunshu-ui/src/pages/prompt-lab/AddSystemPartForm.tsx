/**
 * AddSystemPartForm —— 添加自定义系统提示词组件弹窗
 * 从 PromptLab.tsx 拆分（原 245-282 行，逻辑不变）。
 */
import { useState } from 'react';
import { usePromptLabStore } from '../../stores/usePromptLabStore';

interface AddSystemPartFormProps {
  onClose: () => void;
}

export default function AddSystemPartForm({ onClose }: AddSystemPartFormProps) {
  const addSystemPart = usePromptLabStore((s) => s.addSystemPart);
  const [label, setLabel] = useState('');
  const [text, setText] = useState('');

  const submit = () => {
    if (!label.trim() || !text.trim()) return;
    addSystemPart({
      id: `sp-custom-${Date.now()}`,
      label: label.trim(),
      enabled: true,
      text: text.trim(),
    });
    onClose();
  };

  return (
    <div className="pl-modal-mask" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="pl-modal-title">添加系统提示词组件</h3>
        <label className="pl-field">
          <span>组件名称 *</span>
          <input className="pl-text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="例如：知识库检索规则" />
        </label>
        <label className="pl-field">
          <span>组件文本 *（注入 system message 的一段）</span>
          <textarea className="pl-textarea" value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder="输入要注入的内容…" />
        </label>
        <div className="pl-modal-actions">
          <button type="button" className="pl-btn ghost" onClick={onClose}>取消</button>
          <button type="button" className="pl-btn primary" onClick={submit} disabled={!label.trim() || !text.trim()}>
            添加
          </button>
        </div>
      </div>
    </div>
  );
}
