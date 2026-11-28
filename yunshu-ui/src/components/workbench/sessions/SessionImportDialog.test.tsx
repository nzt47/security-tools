/**
 * SessionImportDialog 测试（E4-5 第四批）：
 * 预览只展示计数 / 开关关如实提示 / 写路径需勾选确认才可导入 / 成功回调新会话 id。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SessionImportDialog } from './SessionImportDialog';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

const PREVIEW = {
  ok: true,
  write_enabled: true,
  filename: 'export.json',
  format: 'deepseek.export.v1',
  session_count: 2,
  duplicates: 1,
  sessions: [
    { title: '编排讨论', messages: 4, chars: 120, first_ts: '', last_ts: '', fingerprint: 'f1', duplicate: false },
    { title: '导入过的', messages: 2, chars: 30, first_ts: '', last_ts: '', fingerprint: 'f2', duplicate: true },
  ],
  stats: {},
};

let previewPayload: Record<string, unknown>;
let applyPayload: Record<string, unknown>;
let applyBody: FormData | null;

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === '/api/sessions/import' && init?.method === 'POST') {
    const form = init.body as FormData;
    const mode = String(form.get('mode') || '');
    if (mode === 'apply') {
      applyBody = form;
      return jsonResponse(applyPayload, (applyPayload.status as number) === undefined
        ? true
        : (applyPayload.status as number) < 400);
    }
    return jsonResponse(previewPayload, true);
  }
  return jsonResponse({ ok: false, error: 'unexpected' }, false, 500);
});

beforeEach(() => {
  previewPayload = { ...PREVIEW };
  applyPayload = { ok: true, created: [{ id: 'sess_new1', title: '编排讨论', messages: 4 }], created_count: 1, skipped_duplicates: 1 };
  applyBody = null;
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function pickFile() {
  const input = screen.getByLabelText('选择会话导出文件') as HTMLInputElement;
  const file = new File(['{}'], 'export.json', { type: 'application/json' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe('SessionImportDialog', () => {
  it('选文件后只预览：回计数与去重标记，未勾选不能导入', async () => {
    render(<SessionImportDialog open onClose={() => {}} />);
    pickFile();
    await waitFor(() => expect(screen.getByTestId('session-import-preview')).toBeTruthy());
    expect(screen.getByTestId('session-import-counts').textContent).toContain('2 个会话');
    expect(screen.getByTestId('session-import-counts').textContent).toContain('1 个已导入');
    expect(screen.getAllByTestId('session-import-row')).toHaveLength(2);
    const apply = screen.getByTestId('session-import-apply') as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    // 预览请求不含 confirm（只读路径）
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('开关关：如实显示未启用，且不给导入按钮机会', async () => {
    previewPayload = { ok: false, code: 'disabled', error: '会话导入未启用（CP_SESSION_IMPORT_ENABLED=0）' };
    render(<SessionImportDialog open onClose={() => {}} />);
    pickFile();
    await waitFor(() => expect(screen.getByTestId('session-import-error')).toBeTruthy());
    expect(screen.getByTestId('session-import-error').textContent).toContain('未启用');
    expect(screen.queryByTestId('session-import-preview')).toBeNull();
  });

  it('写路径关：预览可用但提示只能预览，无确认勾选框', async () => {
    previewPayload = { ...PREVIEW, write_enabled: false };
    render(<SessionImportDialog open onClose={() => {}} />);
    pickFile();
    await waitFor(() => expect(screen.getByTestId('session-import-write-disabled')).toBeTruthy());
    expect(screen.queryByTestId('session-import-confirm')).toBeNull();
  });

  it('勾选确认后才导入：带 confirm=1，成功后回调新会话 id', async () => {
    const onImported = vi.fn();
    render(<SessionImportDialog open onClose={() => {}} onImported={onImported} />);
    pickFile();
    await waitFor(() => expect(screen.getByTestId('session-import-confirm')).toBeTruthy());
    fireEvent.click(screen.getByTestId('session-import-confirm'));
    const apply = screen.getByTestId('session-import-apply') as HTMLButtonElement;
    await waitFor(() => expect(apply.disabled).toBe(false));
    fireEvent.click(apply);
    await waitFor(() => expect(screen.getByTestId('session-import-result')).toBeTruthy());
    expect(applyBody?.get('confirm')).toBe('1');
    expect(applyBody?.get('mode')).toBe('apply');
    expect(onImported).toHaveBeenCalledWith(['sess_new1']);
  });

  it('导入失败：显示后端中文短句，不回调', async () => {
    const onImported = vi.fn();
    applyPayload = { ok: false, code: 'write_disabled', error: '写路径未开启（CP_SESSION_IMPORT_WRITE_ENABLED=0），当前只能预览' };
    render(<SessionImportDialog open onClose={() => {}} onImported={onImported} />);
    pickFile();
    await waitFor(() => expect(screen.getByTestId('session-import-confirm')).toBeTruthy());
    fireEvent.click(screen.getByTestId('session-import-confirm'));
    fireEvent.click(screen.getByTestId('session-import-apply'));
    await waitFor(() => expect(screen.getByTestId('session-import-error')).toBeTruthy());
    expect(screen.getByTestId('session-import-error').textContent).toContain('写路径未开启');
    expect(onImported).not.toHaveBeenCalled();
  });
});
