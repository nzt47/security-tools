/**
 * ComponentPalette — 左侧组件面板（拖拽源）
 *
 * 5 种节点类型可拖拽到画布。dragStart 时写入 dataTransfer，FlowCanvas onDrop 解析。
 */
import type { DragEvent } from 'react';
import type { NodeType } from '../types';

export interface PaletteItem {
  type: NodeType;
  label: string;
  icon: string;
  group: '技能' | '控制流' | 'Agent' | '工作流';
}

const PALETTE_ITEMS: PaletteItem[] = [
  { type: 'skill', label: '技能节点', icon: '⚙', group: '技能' },
  { type: 'conditional', label: '条件分支', icon: '◇', group: '控制流' },
  { type: 'loop', label: '循环', icon: '⟳', group: '控制流' },
  { type: 'agent', label: 'Agent', icon: '🤖', group: 'Agent' },
  { type: 'workflow', label: '子流程', icon: '📦', group: '工作流' },
];

const GROUP_ORDER: PaletteItem['group'][] = ['技能', '控制流', 'Agent', '工作流'];

function onDragStart(e: DragEvent<HTMLDivElement>, item: PaletteItem) {
  e.dataTransfer.setData('application/visual-editor-node', JSON.stringify(item));
  e.dataTransfer.effectAllowed = 'move';
}

/** onPick：点击（或键盘 Enter/空格）直接把节点加到画布 —— 拖拽之外的第二条路。
 *  2026-10-26：此前只有 draggable，点一下没有任何反应，用户以为编辑器坏了。 */
export function ComponentPalette({ onPick }: { onPick?: (item: PaletteItem) => void }) {
  return (
    <aside className="ve-palette" data-testid="ve-palette">
      <div className="ve-palette-header">组件面板</div>
      <div className="ve-palette-hint">点击添加，或拖拽到画布指定位置</div>
      {GROUP_ORDER.map((group) => {
        const items = PALETTE_ITEMS.filter((i) => i.group === group);
        if (items.length === 0) return null;
        return (
          <div key={group} className="ve-palette-group">
            <div className="ve-palette-group-title">{group}</div>
            {items.map((item) => (
              <div
                key={item.type}
                className="ve-palette-item"
                draggable
                role="button"
                tabIndex={0}
                onClick={() => onPick?.(item)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onPick?.(item);
                  }
                }}
                onDragStart={(e) => onDragStart(e, item)}
                title={`点击添加到画布，或拖拽到指定位置：${item.label}`}
                data-testid={`palette-item-${item.type}`}
              >
                <span className="ve-palette-icon">{item.icon}</span>
                <span className="ve-palette-label">{item.label}</span>
              </div>
            ))}
          </div>
        );
      })}
    </aside>
  );
}
