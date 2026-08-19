/**
 * 极简 Logger（零依赖）
 * - 级别：debug < info < warn < error，由 VITE_LOG_LEVEL 控制（默认 info）
 * - 统一输出 [yunshu] 前缀，便于控制台过滤与后续接入上报
 */
type LogLevel = 'debug' | 'info' | 'warn' | 'error'

const LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 }

const currentLevel = LEVELS[(import.meta.env.VITE_LOG_LEVEL as LogLevel | undefined) ?? 'info'] ?? LEVELS.info

function shouldLog(level: LogLevel): boolean {
  return LEVELS[level] >= currentLevel
}

export const logger = {
  debug: (...args: unknown[]) => {
    if (shouldLog('debug')) console.debug('[yunshu]', ...args)
  },
  info: (...args: unknown[]) => {
    if (shouldLog('info')) console.info('[yunshu]', ...args)
  },
  warn: (...args: unknown[]) => {
    if (shouldLog('warn')) console.warn('[yunshu]', ...args)
  },
  error: (...args: unknown[]) => {
    if (shouldLog('error')) console.error('[yunshu]', ...args)
  },
}
