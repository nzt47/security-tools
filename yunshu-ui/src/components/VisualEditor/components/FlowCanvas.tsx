/**
 * FlowCanvas — 中央画布（ReactFlow 集成）
 *
 * 交互逻辑：
 * - onDrop：解析 dataTransfer 创建节点
 * - onConnect：连线建立依赖
 * - onNodeClick：选中节点
 * - Delete 键：删除选中节点（连带边）
 * - Ctrl+Z / Ctrl+Y：撤销/重做
 */
import { useCallback, useEffect, useRef, type DragEvent } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type NodeMouseHandler,
} from '@xyflow/react';
import { useFlowStore } from '../stores/useFlowStore';
import { nodeTypes } from '../nodes/nodeTypes';
import type { NodeType } from '../types';

interface PaletteDragPayload {
  type: NodeType;
  label: string;
  icon: string;
  group: string;
}

export function FlowCanvas() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();
  const { nodes, edges, onNodesChange, onEdgesChange, onConnect, addNode, selectNode, removeNode, undo, redo, selectedNodeId } =
    useFlowStore();

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const raw = event.dataTransfer.getData('application/visual-editor-node');
      if (!raw) return;
      const payload = JSON.parse(raw) as PaletteDragPayload;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addNode(payload.type, position, payload.label);
    },
    [screenToFlowPosition, addNode],
  );

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_e, node) => selectNode(node.id),
    [selectNode],
  );

  const onPaneClick = useCallback(() => selectNode(null), [selectNode]);

  // ─── 键盘快捷键：Delete 删除 / Ctrl+Z 撤销 / Ctrl+Y 重做 ───
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Delete' && selectedNodeId) {
        removeNode(selectedNodeId);
      } else if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedNodeId, removeNode, undo, redo]);

  return (
    <div ref={wrapperRef} className="ve-canvas-wrapper" data-testid="ve-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        deleteKeyCode={null}
      >
        <Background gap={16} />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
