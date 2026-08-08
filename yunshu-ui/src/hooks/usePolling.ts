/**
 * 通用轮询 Hook — 定时调用异步函数并暴露 { data, error }
 *
 * 不变量【不易】：
 * - fetcher 接收 AbortSignal；卸载或下一轮触发时中止在途请求
 * - 卸载时清理定时器与 AbortController，避免内存泄漏与 setState on unmounted
 * - AbortError 静默（被中止不算错误），避免误报 error
 *
 * 简易【简易】：
 * - 仅暴露 { data, error }；loading 由调用方按 `data === undefined && !error` 推导
 * - 与 useChatStream.ts 风格一致：useRef 持有 AbortController
 * - fetcher 通过 ref 保鲜，避免 useEffect 因 fetcher 引用变化重置轮询节奏
 *   （仅 intervalMs 变化才重置轮询）
 *
 * 使用示例（见 useContextMonitor.ts）：
 *   const { data, error } = usePolling<ContextStatus>(
 *     (signal) => contextMonitorApi.status(signal),
 *     5000,
 *   );
 */
import { useEffect, useRef, useState } from 'react';

export interface UsePollingReturn<T> {
  data: T | undefined;
  error: Error | null;
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): UsePollingReturn<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);

  // 保鲜 fetcher：调用方每次渲染可能传入新闭包，但 effect 仅依赖 intervalMs
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  // 在途请求的 AbortController，便于清理时中止
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const tick = async () => {
      // 中止前一个在途请求（防止重叠请求堆积）
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const result = await fetcherRef.current(controller.signal);
        // 卸载或被中止时不更新状态（防 setState on unmounted）
        if (stopped || controller.signal.aborted) return;
        setData(result);
        setError(null);
      } catch (err: unknown) {
        if (stopped || controller.signal.aborted) return;
        // AbortError 静默（被中止不算错误，常见于卸载/下一轮覆盖）
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        // 仅在仍在轮询时排程下一轮（卸载/中止后停止）
        if (!stopped && !controller.signal.aborted) {
          timer = setTimeout(tick, intervalMs);
        }
      }
    };

    // 首轮立即触发，后续按 intervalMs 排程
    tick();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      abortRef.current?.abort();
    };
  }, [intervalMs]);

  return { data, error };
}
