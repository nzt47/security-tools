/**
 * ChatPanel 单元测试 —— 模拟流式中断场景，验证日志依然正确打印
 * ------------------------------------------------
 * 场景：sendMessage 消费流期间用户点击"停止生成"（stopStreaming → AbortController.abort）
 *   → 生成器抛 AbortError → store 判定为主动中止（不视为错误）→ emit abort 事件
 *   → ChatPanel 日志订阅器打印 console.warn '[云枢·SSE] ⏹ 用户中止'
 * 本测试将 lib/sse 的 createChatStream mock 为"产出 1 片后挂起、abort 时抛 AbortError"
 * 的可控流，完整走通 渲染 → 发送 → chunk 日志 → 中断 → 中止日志 链路。
 *
 * 实现说明：
 *  - vi.mock 工厂会被提升到 import 之前执行，须用 vi.hoisted 定义 mock 函数
 *  - Zustand persist 在 store 模块加载时捕获 localStorage 引用，而 vitest 2.x 的
 *    jsdom localStorage 不可用 → 先打桩 localStorage，再顶层 await 动态加载 store
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

// ── mock SSE 客户端（vi.hoisted 供提升的 mock 工厂引用） ──
const { mockCreateStream } = vi.hoisted(() => ({ mockCreateStream: vi.fn() }));
vi.mock('../../../lib/sse', () => ({ createChatStream: mockCreateStream }));

// ── 打桩 localStorage（必须在动态 import store 之前） ──
vi.stubGlobal('localStorage', {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
  key: vi.fn(() => null),
  length: 0,
});

// ── 动态加载被测模块（store 模块加载时捕获 localStorage 引用） ──
const [{ ChatPanel }, { useLayoutStore }] = await Promise.all([
  import('./ChatPanel'),
  import('../../../stores/useLayoutStore'),
]);

// jsdom 未实现 scrollIntoView，ChatPanel 的自动滚动 useEffect 需要它
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

/** 产出 1 片后挂起的可控流：收到 abort 信号时抛 AbortError（与真实 sse.ts 行为一致） */
async function* interruptibleStream(_question: string, signal?: AbortSignal) {
  yield { type: 'chunk', text: '第一片', seq: 1 };
  await new Promise<never>((_resolve, reject) => {
    const onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

describe('ChatPanel 流式日志（中断场景）', () => {
  beforeEach(() => {
    mockCreateStream.mockImplementation(interruptibleStream);
    // 重置全局 store，避免跨用例状态污染
    useLayoutStore.setState({
      messages: [],
      thinking: [],
      streaming: false,
      activeStreamId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    // 清理可能未结束的流
    useLayoutStore.getState().stopStreaming();
  });

  it('模拟流式中断：chunk 日志正常打印，中止后 warn 日志正确输出', async () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    // 挂载 ChatPanel（激活其日志订阅器）
    render(<ChatPanel />);

    // 发起对话 → 流式消费 chunk#1 → 触发"停止生成"中断
    const sendPromise = useLayoutStore.getState().sendMessage('中断测试');
    await waitFor(() => {
      expect(
        debugSpy.mock.calls.some((c) => String(c[0]).includes('chunk#1')),
      ).toBe(true);
    });
    useLayoutStore.getState().stopStreaming();
    await sendPromise;

    // 1) 业务层 chunk 日志已打印（含 seq/len/accumulated 字段）
    const chunkLog = debugSpy.mock.calls.find((c) => String(c[0]).includes('chunk#1'));
    expect(chunkLog?.[1]).toMatchObject({ seq: 1, len: 3, accumulated: 3 });

    // 2) 中断日志：warn 级别"用户中止"，且带已累计字符数
    const warns = warnSpy.mock.calls.map((c) => String(c[0])).join('\n');
    expect(warns).toContain('⏹ 用户中止');

    // 3) 主动停止不产生业务层 error 日志（AbortError 不应落入错误分支；
    //    忽略 React/framer 在 jsdom 下的渲染噪音 error）
    const businessErrors = errorSpy.mock.calls.filter((c) =>
      String(c[0]).includes('[云枢·SSE]'),
    );
    expect(businessErrors).toHaveLength(0);

    // 4) store 状态收敛：流结束、消息标记完成、保留已生成内容
    const state = useLayoutStore.getState();
    expect(state.streaming).toBe(false);
    expect(state.messages.find((m) => m.status === 'streaming')).toBeUndefined();
    expect(state.messages.at(-1)?.content).toBe('第一片');
  });
});

describe('ChatPanel 显示开关（思考 / 工具）', () => {
  beforeEach(() => {
    useLayoutStore.setState({ messages: [], thinking: [], streaming: false, activeStreamId: null });
  });

  afterEach(cleanup);

  /** 造一条带步骤的助手消息：1 个思考步骤 + 1 次工具调用 */
  function seedAssistantWithSteps() {
    useLayoutStore.setState({
      messages: [
        {
          id: 'asst-x',
          role: 'assistant',
          content: '好的',
          createdAt: Date.now(),
          status: 'done',
          steps: [
            { id: 'intent', title: '意图识别', detail: '解析输入', status: 'done', at: 1 },
            { id: 'tool-real-search', title: '工具调用：search', detail: '结果: ok', status: 'done', at: 2 },
          ],
        },
      ],
    });
  }

  it('开关上显示本对话可显隐的步骤计数（思考 1 / 工具 1）', () => {
    seedAssistantWithSteps();
    const { container } = render(<ChatPanel />);
    const toggles = container.querySelectorAll<HTMLButtonElement>('.wb-display-toggle');
    expect(toggles).toHaveLength(2);
    // [0]=思考 [1]=工具：计数徽标 = 本对话可显隐的步骤数
    expect(toggles[0].textContent).toContain('思考');
    expect(toggles[0].querySelector('.wb-display-count')?.textContent).toBe('1');
    expect(toggles[1].textContent).toContain('工具');
    expect(toggles[1].querySelector('.wb-display-count')?.textContent).toBe('1');
    expect(toggles[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('点击开关切换 aria-pressed 与提示文案（无可显隐内容时说明原因）', () => {
    const { container } = render(<ChatPanel />);
    const toggles = container.querySelectorAll<HTMLButtonElement>('.wb-display-toggle');
    // 无步骤：不显示计数徽标，title 说明"暂无可显隐内容"（避免误以为开关坏了）
    expect(toggles[0].querySelector('.wb-display-count')).toBeNull();
    expect(toggles[0].getAttribute('title')).toContain('暂无可显隐的思考步骤');
    expect(toggles[1].getAttribute('title')).toContain('未触发工具调用');
    expect(toggles[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('点击思考开关后 aria-pressed 变为 false（开关确实生效）', () => {
    seedAssistantWithSteps();
    const { container } = render(<ChatPanel />);
    const toggles = container.querySelectorAll<HTMLButtonElement>('.wb-display-toggle');
    fireEvent.click(toggles[0]);
    expect(
      container.querySelectorAll<HTMLButtonElement>('.wb-display-toggle')[0].getAttribute('aria-pressed'),
    ).toBe('false');
  });
});
