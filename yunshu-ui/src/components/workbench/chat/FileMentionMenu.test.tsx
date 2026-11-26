/**
 * @ 文件引用（E3-3）：输入 `@关键词` → 列出匹配文件 → 选中插入 `@相对路径`。
 *
 * 钉住：
 *  1. 只在**末尾**的 @片段且长度 ≥2 时才搜（否则每敲一个字符都遍历目录）；
 *  2. 只复用 /api/filesystem/search（不新增路由、不造第二套索引）；
 *  3. 搜索失败/无匹配要如实显示并可继续手打；选中后把 @片段替换成 @路径（不留残字）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { MessageInput } from './MessageInput';
import { applyMention, extractMentionQuery } from './FileMentionMenu';

function mockFetch(body: unknown, ok = true) {
  const fn = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  }) as unknown as Response);
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('mention 文本工具', () => {
  it('取末尾 @ 片段', () => {
    expect(extractMentionQuery('看看 @read')).toBe('read');
    expect(extractMentionQuery('看看 @read_file 这个')).toBeNull();
    expect(extractMentionQuery('没有引用')).toBeNull();
  });

  it('替换时不留残字', () => {
    expect(applyMention('看看 @re', 'agent/read.md')).toBe('看看 @agent/read.md ');
  });
});

describe('MessageInput · @ 文件菜单', () => {
  it('输入 @关键词 会搜索并列出候选', async () => {
    const fetchMock = mockFetch({
      ok: true,
      results: [{ path: 'agent/read_file.py', name: 'read_file.py' }],
    });
    render(<MessageInput value="看看 @readf" onChange={() => {}} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText('read_file.py')).toBeTruthy(), { timeout: 3000 });
    const url = String((fetchMock.mock.calls[0] as unknown as [string])[0]);
    expect(url).toContain('/api/filesystem/search');
    expect(url).toContain('readf');
  });

  it('选中后回传替换好的文本', async () => {
    mockFetch({ ok: true, results: [{ path: 'agent/read_file.py', name: 'read_file.py' }] });
    const onChange = vi.fn();
    render(<MessageInput value="看看 @readf" onChange={onChange} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText('read_file.py')).toBeTruthy(), { timeout: 3000 });
    fireEvent.click(screen.getByText('read_file.py'));
    expect(onChange).toHaveBeenCalledWith('看看 @agent/read_file.py ');
  });

  it('无匹配如实提示', async () => {
    mockFetch({ ok: true, results: [] });
    render(<MessageInput value="@zzzzqq" onChange={() => {}} onSend={() => {}} />);
    await waitFor(() => expect(screen.getByText(/无匹配/)).toBeTruthy(), { timeout: 3000 });
  });

  it('片段太短不弹菜单', () => {
    const fetchMock = mockFetch({ ok: true, results: [] });
    render(<MessageInput value="@a" onChange={() => {}} onSend={() => {}} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('文件引用建议')).toBeNull();
  });
});
