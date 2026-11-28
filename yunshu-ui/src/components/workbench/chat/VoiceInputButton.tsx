/**
 * 语音输入按钮（E3-4）：MediaRecorder 录音 → POST /api/voice/transcribe → 文本回填输入框。
 *
 * 【失败姿态】浏览器不支持录音、后端未启用、上游未配置、音频过大都只给**稳定中文提示**，
 * 不假装可用、不静默吞掉：按钮仍可点，错误显示在下方。
 */
import { useRef, useState } from 'react';
import { Mic, Square } from 'lucide-react';
import { Button } from '@/components/ui';
import { VOICE_TRANSCRIBE } from '@/api/endpoints';
import { authHeader } from '../../../lib/apiToken';

export interface VoiceTranscribeBody {
  ok?: boolean;
  code?: string;
  error?: string;
  text?: string;
  model?: string;
  truncated?: boolean;
}

export function voiceSupported(): boolean {
  return typeof navigator !== 'undefined'
    && typeof navigator.mediaDevices?.getUserMedia === 'function'
    && typeof MediaRecorder !== 'undefined';
}

export function VoiceInputButton({
  onText,
  disabled,
}: {
  onText: (text: string) => void;
  disabled?: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  function cleanup() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    recorderRef.current = null;
    chunksRef.current = [];
    setRecording(false);
  }

  async function upload(blob: Blob) {
    setBusy(true);
    setError('');
    try {
      const form = new FormData();
      const name = blob.type.includes('ogg') ? 'voice.ogg' : 'voice.webm';
      form.append('audio', blob, name);
      const res = await fetch(VOICE_TRANSCRIBE, {
        method: 'POST',
        headers: authHeader(),
        body: form,
      });
      const body = (await res.json().catch(() => null)) as VoiceTranscribeBody | null;
      if (!res.ok || !body || body.ok === false) {
        setError(body?.error || '语音识别失败');
        return;
      }
      if (body.text) onText(body.text);
    } catch (e) {
      setError(e instanceof Error ? e.message : '语音识别失败');
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setError('');
    if (!voiceSupported()) {
      setError('当前浏览器不支持录音（需要 MediaRecorder）');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = typeof MediaRecorder.isTypeSupported === 'function'
        && MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : '';
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        cleanup();
        if (blob.size > 0) void upload(blob);
      };
      recorder.start();
      setRecording(true);
    } catch (e) {
      cleanup();
      setError(e instanceof Error ? e.message : '无法打开麦克风');
    }
  }

  function stop() {
    try {
      recorderRef.current?.stop();
    } catch {
      cleanup();
    }
  }

  return (
    <div className="wb-voice">
      <Button
        type="button"
        variant={recording ? 'danger' : 'plain'}
        size="bare"
        className="wb-vote"
        aria-label={recording ? '停止录音' : '语音输入'}
        title={recording ? '停止录音并转写' : '语音输入（录音 → 文本）'}
        data-testid="voice-input"
        disabled={disabled || busy}
        onClick={() => (recording ? stop() : void start())}
      >
        {recording ? <Square size={11} fill="currentColor" /> : <Mic size={13} />}
      </Button>
      {busy ? <span className="text-micro text-slate-400" data-testid="voice-busy">转写中…</span> : null}
      {error ? <span className="text-micro text-rose-400" data-testid="voice-error">{error}</span> : null}
    </div>
  );
}
