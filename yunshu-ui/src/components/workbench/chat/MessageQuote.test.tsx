/**
 * 消息引用（E3-3）：输入框引用条 + 消息上的「引用」入口
 *
 * 钉住：
 *  1. 引用条只显示**一行摘要**（原文进发送体，不在输入框里展开）；
 *  2. 清空按钮回调上层（谁持有 quoted 谁负责清，组件不自己藏状态）；
 *  3. 消息侧「引用」把**完整原文**交给上层（截断只发生在展示层）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { MessageInput } from './MessageInput';
import { MessageItem } from './MessageItem';
import type { ChatMessage } from '../../../stores/useLayoutStore';

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const assistant: ChatMessage = {
  id: 'm-1',
  role: 'assistant',
  content: '这是一段被引用的答复',
  createdAt: Date.now(),
  status: 'done',
};

describe('MessageInput · 引用条', () => {
  it('无引用时不显示', () => {
    render(<MessageInput value="" onChange={() => {}} onSend={() => {}} />);
    expect(screen.queryByText(/^引用：/)).toBeNull();
  });

  it('有引用时显示一行摘要并可清空', () => {
    const onClear = vi.fn();
    render(
      <MessageInput
        value="补充一下"
        onChange={() => {}}
        onSend={() => {}}
        quoted={'第一行\n第二行'}
        onClearQuote={onClear}
      />,
    );
    expect(screen.getByText('引用：第一行 第二行')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('取消引用'));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('超长引用只在展示层截断', () => {
    const long = 'x'.repeat(200);
    render(<MessageInput value="" onChange={() => {}} onSend={() => {}} quoted={long} />);
    const el = screen.getByText(/^引用：/);
    expect(el.textContent.length).toBeLessThan(100);
  });
});

describe('MessageItem · 引用入口', () => {
  it('点引用把完整原文交给上层', () => {
    const onQuote = vi.fn();
    render(<MessageItem message={assistant} onQuote={onQuote} />);
    fireEvent.click(screen.getByLabelText('引用这条消息'));
    expect(onQuote).toHaveBeenCalledWith(assistant.content);
  });

  it('未传 onQuote 时没有引用入口（只读视图）', () => {
    render(<MessageItem message={assistant} />);
    expect(screen.queryByLabelText('引用这条消息')).toBeNull();
  });
});
