/**
 * DevConsole 面板共享工具
 */

/** 复制文本到剪贴板（兼容移动端，失败时回退到 execCommand） */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 继续尝试回退方案
  }
  // 回退方案：移动端与非安全上下文
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

/** 格式化时间戳为 HH:mm:ss.SSS */
export function formatTime(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number, l = 2) => String(n).padStart(l, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(
    d.getSeconds()
  )}.${pad(d.getMilliseconds(), 3)}`;
}

/** 格式化耗时 */
export function formatDuration(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)}ms`;
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/** 根据 HTTP 状态码返回 badge class */
export function statusBadgeClass(status: number): string {
  if (status === 0) return 'badge-muted';
  if (status < 300) return 'badge-ok';
  if (status < 400) return 'badge-info';
  if (status < 500) return 'badge-warn';
  return 'badge-err';
}

/** 根据 method 返回 class */
export function methodClass(method: string): string {
  const m = method.toUpperCase();
  if (m === 'GET') return 'method-get';
  if (m === 'POST') return 'method-post';
  if (m === 'PUT' || m === 'PATCH') return 'method-put';
  if (m === 'DELETE') return 'method-delete';
  return 'method-other';
}

/** 根据耗时返回颜色 class */
export function durationClass(ms: number): string {
  if (ms < 16) return 'badge-ok';
  if (ms < 100) return 'badge-warn';
  return 'badge-err';
}

/** 截断长文本 */
export function truncate(str: string, max = 80): string {
  return str.length > max ? `${str.slice(0, max)}…` : str;
}
