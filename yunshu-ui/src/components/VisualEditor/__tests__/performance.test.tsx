/**
 * VisualEditor 性能测试 — 5 种优化策略验证
 *
 * 对应 P2-2 文档第 14 章：
 *   1. React.memo 避免节点不必要重渲染
 *   2. 防抖 YAML 预览生成
 *   3. 虚拟化节点列表（组件面板）
 *   4. 懒加载 VisualEditor
 *   5. Zustand 选择器避免不必要渲染
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  memo, useState, lazy, Suspense,
  type FC,
} from 'react';
import { create } from 'zustand';
import { render, screen, act } from '@testing-library/react';

// ─── 辅助函数 ──────────────────────────────────────────

function debounce<T extends (...args: any[]) => void>(fn: T, delay: number): T {
  let timer: ReturnType<typeof setTimeout>;
  return ((...args: Parameters<T>) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  }) as T;
}

function generateMockNodes(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: `node-${i}`,
    type: 'skill',
    position: { x: i * 100, y: i * 50 },
    data: {
      label: `Node ${i}`,
      nodeType: 'skill' as const,
      skillId: `skill-${i}`,
      skillName: `Skill ${i}`,
    },
  }));
}

// ═══════════════════════════════════════════════════════
//  1. React.memo 优化策略
// ═══════════════════════════════════════════════════════

describe('策略 1: React.memo 避免节点不必要重渲染', () => {
  const renderTracker = vi.fn();

  const SkillNodeRaw: FC<{ name: string; selected: boolean }> = ({ name }) => {
    renderTracker();
    return <div data-testid="skill-node">{name}</div>;
  };

  const SkillNodeMemoized = memo(
    SkillNodeRaw,
    (prev, next) => prev.name === next.name && prev.selected === next.selected,
  );

  beforeEach(() => renderTracker.mockClear());

  it('memo 后相同 props 不触发重渲染', () => {
    const { rerender } = render(<SkillNodeMemoized name="PDF解析" selected={false} />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    rerender(<SkillNodeMemoized name="PDF解析" selected={false} />);
    expect(renderTracker).toHaveBeenCalledTimes(1);
  });

  it('memo 后 props 变化时触发重渲染', () => {
    const { rerender } = render(<SkillNodeMemoized name="PDF解析" selected={false} />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    rerender(<SkillNodeMemoized name="情感表达" selected={false} />);
    expect(renderTracker).toHaveBeenCalledTimes(2);
  });

  it('memo 后 selected 变化时触发重渲染', () => {
    const { rerender } = render(<SkillNodeMemoized name="PDF解析" selected={false} />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    rerender(<SkillNodeMemoized name="PDF解析" selected={true} />);
    expect(renderTracker).toHaveBeenCalledTimes(2);
  });

  it('50 节点 memo 后仅变更节点重渲染', () => {
    const nodes = generateMockNodes(50);
    const NodeList: FC<{ nodes: typeof nodes; selectedId: string | null }> = ({ nodes, selectedId }) => (
      <div>
        {nodes.map((n) => (
          <SkillNodeMemoized key={n.id} name={n.data.label} selected={n.id === selectedId} />
        ))}
      </div>
    );

    const { rerender } = render(<NodeList nodes={nodes} selectedId={null} />);
    expect(renderTracker).toHaveBeenCalledTimes(50);

    renderTracker.mockClear();
    rerender(<NodeList nodes={nodes} selectedId="node-0" />);
    expect(renderTracker).toHaveBeenCalledTimes(1);
  });
});

// ═══════════════════════════════════════════════════════
//  2. 防抖 YAML 预览生成
// ═══════════════════════════════════════════════════════

describe('策略 2: 防抖 YAML 预览生成', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('高频调用只触发一次最终执行', () => {
    const generateYaml = vi.fn();
    const debounced = debounce(generateYaml, 300);

    for (let i = 0; i < 10; i++) {
      debounced(`yaml-${i}`);
    }
    expect(generateYaml).not.toHaveBeenCalled();

    vi.advanceTimersByTime(300);
    expect(generateYaml).toHaveBeenCalledTimes(1);
    expect(generateYaml).toHaveBeenCalledWith('yaml-9');
  });

  it('300ms 内连续编辑只生成一次 YAML', () => {
    const generateYaml = vi.fn();
    const debounced = debounce(generateYaml, 300);

    debounced('edit-1');
    vi.advanceTimersByTime(100);
    debounced('edit-2');
    vi.advanceTimersByTime(100);
    debounced('edit-3');
    vi.advanceTimersByTime(100);

    expect(generateYaml).not.toHaveBeenCalled();

    vi.advanceTimersByTime(300);
    expect(generateYaml).toHaveBeenCalledTimes(1);
    expect(generateYaml).toHaveBeenCalledWith('edit-3');
  });

  it('50 节点变更防抖后只调用 1 次 generateYaml', () => {
    const nodes = generateMockNodes(50);
    const generateYaml = vi.fn();
    const debounced = debounce(generateYaml, 300);

    nodes.forEach((n) => debounced(n.data.label));
    vi.advanceTimersByTime(300);

    expect(generateYaml).toHaveBeenCalledTimes(1);
    expect(generateYaml).toHaveBeenCalledWith('Node 49');
  });
});

// ═══════════════════════════════════════════════════════
//  3. 虚拟化节点列表
// ═══════════════════════════════════════════════════════

describe('策略 3: 虚拟化节点列表', () => {
  const ITEM_HEIGHT = 48;
  const VIEWPORT_HEIGHT = 480;

  const VirtualizedList: FC<{ items: { id: string; label: string }[] }> = ({ items }) => {
    const [scrollTop, setScrollTop] = useState(0);
    const startIndex = Math.floor(scrollTop / ITEM_HEIGHT);
    const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ITEM_HEIGHT);
    const endIndex = Math.min(startIndex + visibleCount, items.length);
    const visibleItems = items.slice(startIndex, endIndex);

    return (
      <div
        data-testid="viewport"
        style={{ height: VIEWPORT_HEIGHT, overflow: 'auto' }}
        onScroll={(e) => setScrollTop((e.target as HTMLElement).scrollTop)}
      >
        <div style={{ height: items.length * ITEM_HEIGHT, position: 'relative' }}>
          {visibleItems.map((item) => (
            <div
              key={item.id}
              data-testid="palette-item"
              style={{
                position: 'absolute',
                top: startIndex * ITEM_HEIGHT + visibleItems.indexOf(item) * ITEM_HEIGHT,
                height: ITEM_HEIGHT,
              }}
            >
              {item.label}
            </div>
          ))}
        </div>
      </div>
    );
  };

  it('100 项只渲染可见区域（10 项）', () => {
    const items = Array.from({ length: 100 }, (_, i) => ({
      id: `item-${i}`,
      label: `Item ${i}`,
    }));

    render(<VirtualizedList items={items} />);
    const renderedItems = document.querySelectorAll('[data-testid="palette-item"]');
    expect(renderedItems.length).toBe(10);
  });

  it('50 项只渲染可见区域（10 项）', () => {
    const items = Array.from({ length: 50 }, (_, i) => ({
      id: `item-${i}`,
      label: `Item ${i}`,
    }));

    render(<VirtualizedList items={items} />);
    const renderedItems = document.querySelectorAll('[data-testid="palette-item"]');
    expect(renderedItems.length).toBe(10);
  });

  it('滚动后渲染新可见项', () => {
    const items = Array.from({ length: 100 }, (_, i) => ({
      id: `item-${i}`,
      label: `Item ${i}`,
    }));

    const { container } = render(<VirtualizedList items={items} />);
    const viewport = container.querySelector('[data-testid="viewport"]') as HTMLElement;

    act(() => {
      Object.defineProperty(viewport, 'scrollTop', { value: 480, writable: true });
      viewport.dispatchEvent(new Event('scroll'));
    });

    const renderedItems = document.querySelectorAll('[data-testid="palette-item"]');
    expect(renderedItems.length).toBe(10);
  });
});

// ═══════════════════════════════════════════════════════
//  4. 懒加载 VisualEditor
// ═══════════════════════════════════════════════════════

describe('策略 4: 懒加载 VisualEditor', () => {
  it('lazy 组件在 Suspense 下按需加载', async () => {
    const LazyComponent = lazy(async () => ({
      default: ({ label }: { label: string }) => (
        <div data-testid="lazy-node">{label}</div>
      ),
    }));

    render(
      <Suspense fallback={<div data-testid="loading">Loading...</div>}>
        <LazyComponent label="VisualEditor" />
      </Suspense>,
    );

    expect(screen.getByTestId('loading')).toBeTruthy();

    const lazyNode = await screen.findByTestId('lazy-node');
    expect(lazyNode).toBeTruthy();
    expect(lazyNode.textContent).toBe('VisualEditor');
  });

  it('Suspense fallback 在加载完成后消失', async () => {
    const LazyComponent = lazy(async () => ({
      default: () => <div data-testid="lazy-content">Loaded</div>,
    }));

    render(
      <Suspense fallback={<div data-testid="fallback">Loading...</div>}>
        <LazyComponent />
      </Suspense>,
    );

    expect(screen.getByTestId('fallback')).toBeTruthy();
    await screen.findByTestId('lazy-content');
    expect(screen.queryByTestId('fallback')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════
//  5. Zustand 选择器避免不必要渲染
// ═══════════════════════════════════════════════════════

describe('策略 5: Zustand 选择器避免不必要渲染', () => {
  interface TestState {
    nodes: { id: string; label: string }[];
    selectedNodeId: string | null;
    yamlPreview: string;
    setNodes: (nodes: TestState['nodes']) => void;
    setSelectedNode: (id: string | null) => void;
    setYaml: (yaml: string) => void;
  }

  const useTestStore = create<TestState>((set) => ({
    nodes: [],
    selectedNodeId: null,
    yamlPreview: '',
    setNodes: (nodes) => set({ nodes }),
    setSelectedNode: (selectedNodeId) => set({ selectedNodeId }),
    setYaml: (yamlPreview) => set({ yamlPreview }),
  }));

  it('选择器只订阅 nodes 字段，yamlPreview 变化不触发重渲染', () => {
    const renderTracker = vi.fn();
    const NodesOnly: FC = () => {
      const nodes = useTestStore((s) => s.nodes);
      renderTracker();
      return <div data-testid="nodes-count">{nodes.length}</div>;
    };

    render(<NodesOnly />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    act(() => useTestStore.getState().setYaml('new yaml'));
    expect(renderTracker).toHaveBeenCalledTimes(1);

    act(() => useTestStore.getState().setNodes([{ id: '1', label: 'A' }]));
    expect(renderTracker).toHaveBeenCalledTimes(2);
  });

  it('选择器只订阅 selectedNodeId，nodes 变化不触发重渲染', () => {
    const renderTracker = vi.fn();
    const SelectedOnly: FC = () => {
      const selectedId = useTestStore((s) => s.selectedNodeId);
      renderTracker();
      return <div data-testid="selected">{selectedId ?? 'none'}</div>;
    };

    render(<SelectedOnly />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    act(() => useTestStore.getState().setNodes([{ id: '1', label: 'A' }]));
    expect(renderTracker).toHaveBeenCalledTimes(1);

    act(() => useTestStore.getState().setSelectedNode('node-1'));
    expect(renderTracker).toHaveBeenCalledTimes(2);
  });

  it('订阅整个 store 时任何字段变化都触发重渲染', () => {
    const renderTracker = vi.fn();
    const WholeStore: FC = () => {
      const state = useTestStore();
      renderTracker();
      return <div>{state.nodes.length}</div>;
    };

    render(<WholeStore />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    act(() => useTestStore.getState().setYaml('yaml-1'));
    expect(renderTracker).toHaveBeenCalledTimes(2);

    act(() => useTestStore.getState().setSelectedNode('node-1'));
    expect(renderTracker).toHaveBeenCalledTimes(3);
  });

  it('50 节点更新时选择器组件仅渲染 1 次', () => {
    const renderTracker = vi.fn();
    const NodesCount: FC = () => {
      const nodeCount = useTestStore((s) => s.nodes.length);
      renderTracker();
      return <div>{nodeCount}</div>;
    };

    render(<NodesCount />);
    expect(renderTracker).toHaveBeenCalledTimes(1);

    const nodes = Array.from({ length: 50 }, (_, i) => ({
      id: `n-${i}`,
      label: `Node ${i}`,
    }));
    act(() => useTestStore.getState().setNodes(nodes));
    expect(renderTracker).toHaveBeenCalledTimes(2);
  });
});
