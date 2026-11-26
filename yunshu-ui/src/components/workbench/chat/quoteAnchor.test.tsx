/**
 * 引用回跳（E3-3 尾巴）：用户消息里那段 `> 引用` 可点击，跳回被引用的消息。
 *
 * 钉住：
 *  1. 解析只认**开头**的引用块（正文里的 > 是普通 markdown）；
 *  2. 找不到原消息时**什么都不做**（不猜、不跳到第一条）；
 *  3. 命中后同时设高亮与滚动（高亮是既有机制，滚动只是锦上添花，失败不影响高亮）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { MessageItem } from './MessageItem';
import { findQuotedMessage, parseLeadingQuote } from './quoteAnchor';
import { useLayoutStore, type ChatMessage } from '../../../stores/useLayoutStore';

const assistant: ChatMessage = {
  id: 'a-1',
  role: 'assistant',
  content: '这是一段被引用的答复，内容比较长',
  createdAt: Date.now(),
  status: 'done',
};

beforeEach(() => {
  localStorage.clear();
  useLayoutStore.setState({ messages: [assistant], highlightMsgId: null });
});

afterEach(() => {
  cleanup();
});

describe('parseLeadingQuote', () => {
  it('取出引用与正文', () => {
    const got = parseLeadingQuote('> 被引用的一句\n\n我的追问');
    expect(got).toEqual({ quoted: '被引用的一句', rest: '我的追问' });
  });

  it('没有引用返回 null', () => {
    expect(parseLeadingQuote('普通消息')).toBeNull();
    expect(parseLeadingQuote('正文里的 > 不算引用')).toBeNull();
  });

  it('多行引用拼成一行', () => {
    expect(parseLeadingQuote('> 第一行\n> 第二行\n\n问')).toEqual({
      quoted: '第一行 第二行',
      rest: '问',
    });
  });
});

describe('findQuotedMessage', () => {
  it('按内容找到原消息', () => {
    expect(findQuotedMessage([assistant], '这是一段被引用的答复')?.id).toBe('a-1');
  });

  it('找不到返回 null', () => {
    expect(findQuotedMessage([assistant], '完全不相干')).toBeNull();
  });
});

describe('MessageItem · 引用块可点', () => {
  const userMsg: ChatMessage = {
    id: 'u-1',
    role: 'user',
    content: '> 这是一段被引用的答复\n\n我的追问',
    createdAt: Date.now(),
    status: 'done',
  };

  it('渲染引用块且气泡只显示正文', () => {
    render(<MessageItem message={userMsg} />);
    expect(screen.getByLabelText('跳到被引用的消息')).toBeTruthy();
    expect(screen.getByText('我的追问')).toBeTruthy();
  });

  it('点击后高亮原消息', () => {
    render(<MessageItem message={userMsg} />);
    fireEvent.click(screen.getByLabelText('跳到被引用的消息'));
    expect(useLayoutStore.getState().highlightMsgId).toBe('a-1');
  });

  it('找不到原消息时保持安静', () => {
    useLayoutStore.setState({ messages: [], highlightMsgId: null });
    render(<MessageItem message={userMsg} />);
    fireEvent.click(screen.getByLabelText('跳到被引用的消息'));
    expect(useLayoutStore.getState().highlightMsgId).toBeNull();
  });
});
