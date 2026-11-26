/**
 * 附件（E3-3）：上传 + chips + 消息 manifest
 *
 * 钉住：
 *  1. 选文件就打 POST /api/chat/attachments（multipart，带 session_id）；成功才进列表；
 *  2. 失败只给一行提示，**不清空**已选附件（不悄悄丢用户东西）；
 *  3. manifest 一行一个附件（文件名 → 路径），图片带 OCR 摘要且截断；空列表返回空串。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { AttachmentPicker, buildAttachmentManifest, formatBytes, type AttachmentMeta } from './AttachmentPicker';

const meta: AttachmentMeta = {
  path: 'data/uploads/s1/abc__report.txt',
  name: 'report.txt',
  bytes: 2048,
  kind: 'text',
};

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

describe('AttachmentPicker', () => {
  it('选文件后上传成功才进列表', async () => {
    const fetchMock = mockFetch({ code: 200, data: meta });
    const onChange = vi.fn();
    render(<AttachmentPicker sessionId="s1" value={[]} onChange={onChange} />);
    const input = screen.getByLabelText('选择附件') as HTMLInputElement;
    const file = new File(['hello'], 'report.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/chat/attachments');
    expect(init.method).toBe('POST');
    expect(init.body instanceof FormData).toBe(true);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([meta]));
  });

  it('上传失败给提示且不改列表', async () => {
    mockFetch({ code: 500, detail: 'boom' }, false);
    const onChange = vi.fn();
    render(<AttachmentPicker value={[meta]} onChange={onChange} />);
    const input = screen.getByLabelText('选择附件') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'b.txt')] } });
    await waitFor(() => expect(screen.getByText('上传失败')).toBeTruthy());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('移除附件回传剩余列表', () => {
    const onChange = vi.fn();
    render(<AttachmentPicker value={[meta]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('移除附件 report.txt'));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});

describe('manifest 与体积格式', () => {
  it('空列表返回空串', () => {
    expect(buildAttachmentManifest([])).toBe('');
  });

  it('一行一个附件并带路径', () => {
    const text = buildAttachmentManifest([meta]);
    expect(text.startsWith('【附件】')).toBe(true);
    expect(text).toContain('report.txt');
    expect(text).toContain('2.0 KB');
    expect(text).toContain(meta.path);
    expect(text.endsWith('\n\n')).toBe(true);
  });

  it('图片带 OCR 摘要且截断', () => {
    const long = 'x'.repeat(900);
    const text = buildAttachmentManifest([{
      ...meta, name: 'shot.png', kind: 'image',
      ocr: { available: true, text: long },
    }]);
    expect(text).toContain('OCR：');
    expect(text.length).toBeLessThan(900);
  });

  it('OCR 不可用时不写 OCR 段', () => {
    const text = buildAttachmentManifest([{ ...meta, ocr: { available: false, note: 'OCR 不可用' } }]);
    expect(text).not.toContain('OCR：');
  });

  it('体积格式分档', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
    expect(formatBytes(-1)).toBe('—');
  });
});
