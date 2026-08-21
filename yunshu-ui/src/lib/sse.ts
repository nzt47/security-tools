/**
 * 真实 SSE 客户端
 * ------------------------------------------------
 * 契约边界（【不易】约束）：对外暴露 AsyncGenerator<StreamEvent>，
 * 上层 store / UI 不感知传输实现。接后端：POST /api/chat/stream
 * （Flask 版见 app_server.py，事件格式与本节一致）。
 *
 * SSE 解析要点：
 *  1. 事件块以空行（\n\n）分隔，逐块解析 `data:` 行（兼容多行 data 拼接与 CRLF）
 *  2. `:` 开头的注释行跳过
 *  3. chunk 事件携带 seq 序号，供上层做乱序/丢包检测
 */

export type ThinkingStatus = 'pending' | 'running' | 'done' | 'error';

export type StreamEvent =
  | { type: 'thinking'; id: string; title: string; detail?: string; status: ThinkingStatus }
  | { type: 'chunk'; text: string; seq?: number }
  | { type: 'done' };

/**
 * 后端地址：
 *  - Web/Flask 同域部署：相对路径 /api/chat/stream
 *  - Electron 桌面版：由构建时 VITE_API_BASE 指向本地/远程后端（file:// 下无相对 API）
 */
const API_URL = `${import.meta.env.VITE_API_BASE ?? ''}/api/chat/stream`;

/** 将 SSE 文本块中第一个 data: 载荷解析为事件对象 */
function parseEventBlock(block: string): StreamEvent | null {
  const dataLine = block
    .split('\n')
    .map((l) => (l.endsWith('\r') ? l.slice(0, -1) : l))
    .find((l) => l.startsWith('data:'));
  if (!dataLine) return null; // 注释行 / 心跳 / 空块
  const payload = dataLine.slice(5).trim();
  if (!payload) return null;
  try {
    return JSON.parse(payload) as StreamEvent;
  } catch {
    console.error('[云枢·SSE] 解析事件失败，原始载荷:', payload);
    return null;
  }
}

/**
 * 建立真实 SSE 流并逐事件产出。
 * - 由调用方传入 AbortSignal 以支持"停止生成"
 * - HTTP 非 2xx 或响应体缺失时抛出带状态码的错误
 * - 可选 onError 回调：网络/HTTP 失败时触发（默认行为不变，调用方可不传）
 *
 * 三类终止语义（调用方判定）：
 *   1. 正常结束：产出 type:'done' 事件（或流自然读完）——非错误
 *   2. 主动停止：AbortError（isAbortError 判定）——非错误，保留已生成内容
 *   3. 异常终止：HTTP 非 2xx / 解析失败 —— 抛出 Error，可经 onError 通知
 */
export async function* createChatStream(
  question: string,
  signal?: AbortSignal,
  options?: { onError?: (err: Error) => void },
): AsyncGenerator<StreamEvent> {
  const startedAt = performance.now();
  console.debug('[云枢·SSE] 请求发出:', { url: API_URL, ts: Date.now() });

  let res: Response;
  try {
    res = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: question }),
      signal,
    });
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err));
    options?.onError?.(e);
    throw e;
  }

  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => '');
    const e = new Error(`SSE 请求失败: HTTP ${res.status} ${body.slice(0, 200)}`);
    options?.onError?.(e);
    throw e;
  }
  console.debug('[云枢·SSE] 连接建立:', { httpStatus: res.status, connectMs: Math.round(performance.now() - startedAt) });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let bytesReceived = 0; // 传输层字节数（SSE 帧 + 事件分隔符），用于与业务字符对账

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      bytesReceived += value.byteLength;
      buffer += decoder.decode(value, { stream: true });
      // 事件块之间以空行分隔；最后一截留在 buffer 等待下一轮或收尾
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() ?? '';

      for (const block of blocks) {
        const event = parseEventBlock(block);
        if (event) yield event;
      }
    }

    // 流结束后可能残留未以空行结尾的最后一个事件
    if (buffer.trim()) {
      const event = parseEventBlock(buffer);
      if (event) yield event;
    }
  } finally {
    // 注意：调用方（store）收到 done 事件后会 break，触发生成器 return()，
    // 循环提前退出 —— 必须用 finally 保证"流结束"统计日志在正常/中止两条路径都上报
    console.debug('[云枢·SSE] 流结束:', {
      bytesReceived,
      durationMs: Math.round(performance.now() - startedAt),
    });
  }
}
