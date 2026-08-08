/**
 * 轮询 Hook — 按固定间隔拉取数据（useContextMonitor 上下文监视器用）。
 *
 * 不变量【不易】：
 * - 立即执行一次 + 每 intervalMs 重复
 * - 使用 AbortSignal 取消在途请求（卸载/重启轮询时）
 * - 失败不抛异常：错误放入返回值 error，等待下轮重试（与上下文监视器"轮询失败不阻塞"一致）
 * - 卸载时清理定时器与在途请求
 */
import { useEffect, useRef, useState } from 'react';

interface PollingResult<T> {
  data: T | undefined;
  error: unknown;
}

/**
 * @param fetcher 返回 Promise 的拉取函数，接收 AbortSignal 用于取消
 * @param intervalMs 轮询间隔（毫秒）
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): PollingResult<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(undefined);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const abort = new AbortController();

    const run = async () => {
      try {
        const result = await fetcherRef.current(abort.signal);
        if (!disposed) {
          setData(result);
          setError(undefined);
        }
      } catch (e) {
        if (!disposed && !(e instanceof DOMException && e.name === 'AbortError')) {
          setError(e);
        }
      } finally {
        if (!disposed) {
          timer = setTimeout(run, intervalMs);
        }
      }
    };

    void run();

    return () => {
      disposed = true;
      abort.abort();
      if (timer) clearTimeout(timer);
    };
  }, [intervalMs]);

  return { data, error };
}
