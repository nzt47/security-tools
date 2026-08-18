/**
 * VisualEditor — 可视化编辑器主入口
 *
 * 三栏布局：ComponentPalette | FlowCanvas | PropertiesPanel
 * 底部：YamlPreview 实时预览
 * 顶部：Toolbar（撤销/重做/清空/导出）
 *
 * 集成到主工程：作为 SkillManagement 的第三个 Tab 懒加载。
 */
import { lazy, Suspense, useCallback } from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useFlowStore } from './stores/useFlowStore';
import { ComponentPalette } from './components/ComponentPalette';
import { PropertiesPanel } from './components/PropertiesPanel';
import { YamlPreview } from './components/YamlPreview';
import { generateYaml } from './generator/CodeGenerator';
import './VisualEditor.css';

// FlowCanvas 依赖 useReactFlow()，必须在 ReactFlowProvider 内部渲染
// 用懒加载（性能策略 4）延迟 @xyflow/react 画布体积
const FlowCanvas = lazy(() =>
  import('./components/FlowCanvas').then((m) => ({ default: m.FlowCanvas })),
);

export function VisualEditor() {
  const undo = useFlowStore((s) => s.undo);
  const redo = useFlowStore((s) => s.redo);
  const canUndo = useFlowStore((s) => s.canUndo());
  const canRedo = useFlowStore((s) => s.canRedo());
  const clearCanvas = useFlowStore((s) => s.clearCanvas);
  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);

  const handleExport = useCallback(() => {
    const yaml = generateYaml(nodes, edges);
    const blob = new Blob([yaml], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'workflow.yaml';
    a.click();
    URL.revokeObjectURL(url);
  }, [nodes, edges]);

  return (
    <ReactFlowProvider>
      <div className="ve-root" data-testid="ve-root">
        <div className="ve-toolbar">
          <button
            className="ve-btn"
            onClick={undo}
            disabled={!canUndo}
            title="撤销 (Ctrl+Z)"
          >
            ↶ 撤销
          </button>
          <button
            className="ve-btn"
            onClick={redo}
            disabled={!canRedo}
            title="重做 (Ctrl+Y)"
          >
            ↷ 重做
          </button>
          <span className="ve-toolbar-divider" />
          <button
            className="ve-btn ve-btn-danger"
            onClick={clearCanvas}
            title="清空画布"
          >
            清空
          </button>
          <button
            className="ve-btn ve-btn-primary"
            onClick={handleExport}
            title="导出 YAML"
          >
            导出
          </button>
        </div>
        <div className="ve-main">
          <ComponentPalette />
          <div className="ve-canvas-area">
            <Suspense fallback={<div className="ve-canvas-loading">加载画布…</div>}>
              <FlowCanvas />
            </Suspense>
          </div>
          <PropertiesPanel />
        </div>
        <YamlPreview />
      </div>
    </ReactFlowProvider>
  );
}

export default VisualEditor;
