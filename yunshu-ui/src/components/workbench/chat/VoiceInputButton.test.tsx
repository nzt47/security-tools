/**
 * VoiceInputButton 测试：不支持录音时给稳定提示；支持时录音 → 转写 → 回填文本。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { VoiceInputButton, voiceSupported } from './VoiceInputButton';

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => body, text: async () => JSON.stringify(body) } as Response;
}

class FakeRecorder {
  static isTypeSupported = () => true;
  mimeType = 'audio/webm';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() {}
  stop() {
    this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ok: true, text: '你好世界' })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  // @ts-expect-error 清理测试注入
  delete navigator.mediaDevices;
});

describe('VoiceInputButton', () => {
  it('浏览器不支持录音：点一下给稳定提示，不崩', async () => {
    render(<VoiceInputButton onText={() => {}} />);
    expect(voiceSupported()).toBe(false);
    fireEvent.click(screen.getByTestId('voice-input'));
    await waitFor(() => expect(screen.getByTestId('voice-error')).toBeTruthy());
    expect(screen.getByTestId('voice-error').textContent).toContain('不支持录音');
  });

  it('录音 → 转写 → 回填文本', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [{ stop: () => {} }] }) },
    });
    vi.stubGlobal('MediaRecorder', FakeRecorder);
    const onText = vi.fn();
    render(<VoiceInputButton onText={onText} />);
    fireEvent.click(screen.getByTestId('voice-input'));
    await waitFor(() => expect(screen.getByTestId('voice-input').getAttribute('aria-label')).toBe('停止录音'));
    fireEvent.click(screen.getByTestId('voice-input'));
    await waitFor(() => expect(onText).toHaveBeenCalledWith('你好世界'));
    const call = (fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(String(call[0])).toBe('/api/voice/transcribe');
    expect(call[1].body instanceof FormData).toBe(true);
  });

  it('后端未启用：显示服务端中文短句', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [{ stop: () => {} }] }) },
    });
    vi.stubGlobal('MediaRecorder', FakeRecorder);
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(
      { ok: false, code: 'disabled', error: '语音输入未启用（CP_STT_ENABLED=0）' })));
    render(<VoiceInputButton onText={() => {}} />);
    fireEvent.click(screen.getByTestId('voice-input'));
    await waitFor(() => expect(screen.getByTestId('voice-input').getAttribute('aria-label')).toBe('停止录音'));
    fireEvent.click(screen.getByTestId('voice-input'));
    await waitFor(() => expect(screen.getByTestId('voice-error')).toBeTruthy());
    expect(screen.getByTestId('voice-error').textContent).toContain('未启用');
  });
});
