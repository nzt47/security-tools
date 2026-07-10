/**
 * YamlPreview — YAML 实时预览面板（防抖 300ms）
 *
 * 订阅 nodes/edges，变化后防抖触发 generateYaml。
 * 性能策略 2：避免高频编辑期间频繁序列化。
 */
import { useEffect, useMemo, useState } from 'react';
import { useFlowStore } from '../stores/useFlowStore';
import { generateYaml } from '../generator/CodeGenerator';

export function YamlPreview() {
  const nodes = useFlowStore((s) => s.nodes);
  const edges = useFlowStore((s) => s.edges);
  const [yaml, setYaml] = useState('');

  // 防抖：300ms 内 nodes/edges 多次变更只触发一次 generateYaml
  const debounced = useMemo(
    () => debounce(() => setYaml(generateYaml(nodes, edges)), 300),
    [nodes, edges],
  );

  useEffect(() => {
    debounced();
    return () => debounced.cancel();
  }, [debounced]);

  const nodeCount = nodes.length;
  const edgeCount = edges.length;

  return (
    <section className="ve-yaml-preview" data-testid="ve-yaml-preview">
      <div className="ve-yaml-header">
        <span>YAML 预览</span>
        <span className="ve-yaml-stats">{nodeCount} 节点 / {edgeCount} 连线</span>
      </div>
      <pre className="ve-yaml-content">{yaml || '# 拖拽节点到画布开始编排'}</pre>
    </section>
  );
}

// 内联防抖工具，避免引入 lodash 依赖
function debounce<T extends (...args: never[]) => void>(fn: T, delay: number): T & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const wrapped = ((...args: Parameters<T>) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  }) as T & { cancel: () => void };
  wrapped.cancel = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };
  return wrapped;
}
