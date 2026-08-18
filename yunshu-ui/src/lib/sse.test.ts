/**
 * sse.ts 传输层日志单元测试
 * ------------------------------------------------
 * 回归目标：调用方（store）在收到 done 事件后 break，会触发生成器 return()，
 * 使循环提前退出 —— 修复前"流结束"日志不可达；修复后由 try/finally 保证
 * 在"正常读完 / 提前中断"两条路径上都上报。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createChatStream } from './sse';

describe('createChatStream 传输层日志（finally 修复）', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** 用可控 ReadableStream 模拟 SSE 响应体 */
  function stubFetchWithStream(blocks: string[]) {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const block of blocks) controller.enqueue(encoder.encode(block));
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      ),
    );
  }

  it('正常读完流：依次输出 chunk 事件并打印"流结束"日志', async () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
    stubFetchWithStream([
      'data: {"type":"chunk","text":"你","seq":1}\n\n',
      'data: {"type":"done"}\n\n',
    ]);

    const events = [];
    for await (const evt of createChatStream('正常场景')) {
      events.push(evt);
      if (evt.type === 'done') break; // 模拟 store 收到 done 后 break
    }

    expect(events[0]).toMatchObject({ type: 'chunk', seq: 1 });
    expect(events[1]).toMatchObject({ type: 'done' });
    expect(debugSpy.mock.calls.map((c) => String(c[0]))).toContain('[云枢·SSE] 流结束:');
  });

  it('流中被中断（生成器被 return）：finally 仍打印"流结束"日志（本次修复点）', async () => {
    const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {});
    stubFetchWithStream([
      'data: {"type":"chunk","text":"你","seq":1}\n\n',
      'data: {"type":"chunk","text":"好","seq":2}\n\n',
      'data: {"type":"done"}\n\n',
    ]);

    const gen = createChatStream('中断场景');
    await gen.next(); // 消费第一个 chunk
    await gen.return(undefined); // 模拟调用方提前退出（store 收到 done 后 break / 用户停止）

    const logs = debugSpy.mock.calls.map((c) => String(c[0]));
    expect(logs).toContain('[云枢·SSE] 流结束:');
    // 流结束统计参数应存在
    const finishLog = debugSpy.mock.calls.find((c) => String(c[0]).includes('流结束'));
    expect(finishLog?.[1]).toMatchObject({ bytesReceived: expect.any(Number), durationMs: expect.any(Number) });
  });
});
