/**
 * 显式信封解析的守卫（P1-front 第一批 · 2026-10-04）。
 *
 * 【边界用什么构造】用**合成 Response**，不打真实后端。理由同契约对拍那条纪律：
 * 锚在机制上 —— 合成输入能覆盖"头缺失 / 版本不符 / 体非 JSON / 业务码非 200"
 * 这些**真实后端今天不会产生**的分支，而靠真后端只能覆盖"一切正常"那一支。
 */
import { describe, expect, it } from 'vitest';
import {
  ENVELOPE_HEADER,
  ENVELOPE_VERSION,
  EnvelopeError,
  getEnvelope,
  postEnvelope,
  readEnvelope,
  unwrapEnvelopeBody,
} from './envelope';

function resp(body: unknown, opts: { envelope?: string | null; status?: number } = {}) {
  const headers = new Headers();
  if (opts.envelope !== null) headers.set(ENVELOPE_HEADER, opts.envelope ?? ENVELOPE_VERSION);
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status: opts.status ?? 200,
    headers,
  });
}

describe('readEnvelope', () => {
  it('取出 data', async () => {
    const r = resp({ code: 200, data: { status: 'healthy' }, message: '' });
    await expect(readEnvelope<{ status: string }>(r, '/api/x')).resolves.toEqual({ status: 'healthy' });
  });

  it('缺少信封头时**报错**而不是猜形状', async () => {
    // 这是本模块存在的核心理由：老 helper 会"猜"，把错误推到很远的地方。
    const r = resp({ status: 'healthy' }, { envelope: null });
    await expect(readEnvelope(r, '/api/not-migrated')).rejects.toBeInstanceOf(EnvelopeError);
    await expect(readEnvelope(r, '/api/not-migrated')).rejects.toThrow(/没有 X-Envelope 头/);
  });

  it('信封版本不符时报错，并说清两边版本', async () => {
    const r = resp({ code: 200, data: {} }, { envelope: 'v3' });
    await expect(readEnvelope(r, '/api/x')).rejects.toThrow(/v3/);
  });

  it('体不是 JSON 时报错（而不是抛出 SyntaxError 让人猜）', async () => {
    const r = resp('<html>502</html>');
    await expect(readEnvelope(r, '/api/x')).rejects.toBeInstanceOf(EnvelopeError);
  });

  it('业务码非 200 时报错并带上 message', async () => {
    const r = resp({ code: 500, message: '内部错误', data: null });
    await expect(readEnvelope(r, '/api/x')).rejects.toThrow(/500/);
  });

  it('缺 code 视为不合契约', async () => {
    const r = resp({ data: {} });
    await expect(readEnvelope(r, '/api/x')).rejects.toThrow(/code/);
  });
});

describe('getEnvelope', () => {
  it('非 2xx 直接抛 HTTP 状态（不试图解析错误体）', async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (async () => new Response('nope', { status: 503 })) as typeof fetch;
    try {
      await expect(getEnvelope('/api/x')).rejects.toThrow(/503/);
    } finally {
      globalThis.fetch = original;
    }
  });

  it('2xx 且带信封时返回 data', async () => {
    const original = globalThis.fetch;
    globalThis.fetch = (async () =>
      resp({ code: 200, data: { ok: true } })) as typeof fetch;
    try {
      await expect(getEnvelope<{ ok: boolean }>('/api/x')).resolves.toEqual({ ok: true });
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe('postEnvelope', () => {
  /** 捕获 fetch 收到的实参，用于断言请求形态（迁移解析方式**不得顺带改请求行为**）。 */
  function spyFetch(reply: Response) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const original = globalThis.fetch;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return reply;
    }) as typeof fetch;
    return { calls, restore: () => { globalThis.fetch = original; } };
  }

  it('2xx 且带信封时返回 data', async () => {
    const s = spyFetch(resp({ code: 200, data: { ok: true, results: [] }, message: '' }));
    try {
      await expect(postEnvelope<{ ok: boolean }>('/api/vector/search', { query: 'x' })).resolves.toEqual({ ok: true, results: [] });
      expect(s.calls[0].init.method).toBe('POST');
    } finally {
      s.restore();
    }
  });

  it('有 body 时声明 JSON 内容类型并序列化', async () => {
    const s = spyFetch(resp({ code: 200, data: null }));
    try {
      await postEnvelope('/api/memory/manual', { content: '你好' });
      const init = s.calls[0].init;
      expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
      expect(init.body).toBe(JSON.stringify({ content: '你好' }));
    } finally {
      s.restore();
    }
  });

  it('**空 body 时不得声明 JSON 内容类型**（否则后端 get_json() 会 415/400）', async () => {
    const s = spyFetch(resp({ code: 200, data: null }));
    try {
      await postEnvelope('/api/x');
      const init = s.calls[0].init;
      expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined();
      expect(init.body).toBeUndefined();
    } finally {
      s.restore();
    }
  });

  it('显式传令牌时用 Authorization，未传时走本地令牌（可能为空）', async () => {
    const s = spyFetch(resp({ code: 200, data: null }));
    try {
      await postEnvelope('/api/x', { a: 1 }, 'tok-123');
      expect((s.calls[0].init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
    } finally {
      s.restore();
    }
  });

  it('非 2xx 直接抛 HTTP 状态', async () => {
    const s = spyFetch(new Response('nope', { status: 503 }));
    try {
      await expect(postEnvelope('/api/x', { a: 1 })).rejects.toThrow(/503/);
    } finally {
      s.restore();
    }
  });
});

describe('unwrapEnvelopeBody', () => {
  it('取出 data（供走 apiClient.request() 的消费方显式拆信封）', () => {
    expect(unwrapEnvelopeBody<{ percentage: number }>(
      { code: 200, data: { percentage: 1 }, message: '' }, '/api/context/status',
    )).toEqual({ percentage: 1 });
  });

  it('不是信封体就抛错 —— **不做「兼容两态」的宽解析**', () => {
    // 这条钉的是本批最危险的形态：contextMonitorApi 走的是 request()（返回原始体），
    // 若这里宽容地把裸体当 data 返回，迁移漏改时它会**静默读到 undefined** 而不报错。
    for (const raw of [{ percentage: 1 }, {}, null, [1, 2], { data: {} }]) {
      expect(() => unwrapEnvelopeBody(raw, '/api/context/status')).toThrow(EnvelopeError);
    }
  });

  it('业务码非 200 时抛错并带上 message', () => {
    expect(() => unwrapEnvelopeBody({ code: 500, data: null, message: '内部错误' }, '/api/x'))
      .toThrow(/500/);
  });
});
