/**
 * CodeEditorPanel 单元测试 —— 独立窗口同步机制（localStorage 隐式共享）
 * ------------------------------------------------
 * 背景：CodeEditor 面板状态持久化到 localStorage['yunshu:editor:code:v1']，
 *       Electron 同 session 多窗口共享 localStorage —— "主窗口写入 → 独立窗口读取"
 *       即通过该隐式通道完成同步（不经过 IPC StateSync，见架构文档 8.1）。
 *
 * 本测试用一个"内存版 localStorage"模拟跨窗口共享的 session 存储：
 *  - 用例 1 模拟主窗口：渲染 → 编辑 → 断言写入 localStorage；
 *  - 用例 2 模拟独立窗口：预置 localStorage（模拟主窗口已写入）→ 渲染 → 断言读取到同一内容。
 *
 * 实现说明：
 *  - CodeEditorPanel 直接使用全局 localStorage（无注入），须先 vi.stubGlobal 打桩；
 *  - 高亮预览走 400ms 防抖 setTimeout，用 vi.useFakeTimers 推进，避免定时器悬空。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { CodeEditorPanel } from './CodeEditorPanel';

const STORAGE_KEY = 'yunshu:editor:code:v1';

/** 推进防抖计时器并包裹 React 更新（避免 act 警告） */
function flushDebounce() {
  act(() => {
    vi.advanceTimersByTime(500);
  });
}

/** 内存版 localStorage：模拟"同 session 多窗口共享"的持久化存储 */
function createMemoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => {
      data.set(k, String(v));
    },
    removeItem: (k: string) => {
      data.delete(k);
    },
    clear: () => data.clear(),
    key: (i: number) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}

describe('CodeEditorPanel 独立窗口同步（localStorage 隐式共享）', () => {
  let storage: ReturnType<typeof createMemoryStorage>;

  beforeEach(() => {
    storage = createMemoryStorage();
    vi.stubGlobal('localStorage', storage);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  /** 渲染面板并取回 textarea */
  function renderEditor() {
    render(<CodeEditorPanel />);
    return screen.getByPlaceholderText<HTMLTextAreaElement>('在这里编写或粘贴代码…（自动高亮预览）');
  }

  it('主窗口写入：编辑代码 → 内容持久化到 localStorage', () => {
    const textarea = renderEditor();
    const code = "const hub = '主窗口写入';\nconsole.log(hub);";

    fireEvent.change(textarea, { target: { value: code } });
    // 推进防抖计时器，触发 setState 后的持久化 effect
    flushDebounce();

    const saved = JSON.parse(storage.getItem(STORAGE_KEY) ?? 'null');
    expect(saved).not.toBeNull();
    expect(saved.content).toBe(code);
    expect(saved.lang).toBe('typescript'); // 默认语言
  });

  it('独立窗口读取：localStorage 已有内容 → 新渲染的面板读取到同一内容', () => {
    // 模拟主窗口已写入（独立窗口冷启动时从同 session localStorage 读取）
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        lang: 'python',
        content: "import asyncio\nprint('独立窗口读取')",
      }),
    );

    const textarea = renderEditor();
    // 独立窗口挂载即从 localStorage 初始化（loadState）
    expect(textarea.value).toBe("import asyncio\nprint('独立窗口读取')");

    // 语言下拉也应反映持久化的 lang
    expect(screen.getByLabelText<HTMLSelectElement>('代码语言').value).toBe('python');
  });

  it('双向闭环：独立窗口编辑 → 写回 localStorage → 主窗口（重挂载）读到新内容', () => {
    // 1. 独立窗口写入（渲染第一个实例并查询 textarea）
    const detTextarea = renderEditor();
    fireEvent.change(detTextarea, { target: { value: 'from-detached-window' } });
    flushDebounce();
    expect(JSON.parse(storage.getItem(STORAGE_KEY)!).content).toBe('from-detached-window');

    // 2. 卸载独立窗口实例，避免 DOM 多实例查询歧义
    cleanup();

    // 3. 模拟主窗口（重新）挂载，读取同一 localStorage
    const mainTextarea = renderEditor();
    expect(mainTextarea.value).toBe('from-detached-window');
  });

  it('清空按钮：内容清空后 localStorage 同步更新为空串', () => {
    storage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'typescript', content: 'toBeCleared' }));
    renderEditor();

    fireEvent.click(screen.getByTitle('清空内容'));
    flushDebounce();

    expect(JSON.parse(storage.getItem(STORAGE_KEY)!).content).toBe('');
  });
});
