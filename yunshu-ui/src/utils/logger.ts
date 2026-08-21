/**
 * 极简 Logger（零依赖）
 * - 级别：debug < info < warn < error，由 VITE_LOG_LEVEL 控制（默认 info）
 * - 统一输出 [yunshu] 前缀，便于控制台过滤与后续接入上报
 * - 生产裁剪：import.meta.env.PROD 时 debug/info 编译期为 noop（Vite 静态替换
 *   PROD=true 后 esbuild 自动消除不可达分支），避免生产环境高频 console 开销；
 *   warn/error 保留（数据异常 / 接口失败是生产排障必需）。
 */
type LogLevel = 'debug' | 'info' | 'warn' | 'error'

const LEVELS: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 }

const currentLevel = LEVELS[(import.meta.env.VITE_LOG_LEVEL as LogLevel | undefined) ?? 'info'] ?? LEVELS.info

function shouldLog(level: LogLevel): boolean {
  return LEVELS[level] >= currentLevel
}

/** 生产环境 debug/info 占位：零开销 noop（保证调用点类型签名一致） */
const noop = () => {}

/** 生产构建为 true（Vite 静态替换），debug/info 编译期裁剪；开发/测试环境走级别控制 */
const isProd = import.meta.env.PROD

export const logger = {
  debug: isProd
    ? noop
    : (...args: unknown[]) => {
        if (shouldLog('debug')) console.debug('[yunshu]', ...args)
      },
  info: isProd
    ? noop
    : (...args: unknown[]) => {
        if (shouldLog('info')) console.info('[yunshu]', ...args)
      },
  warn: (...args: unknown[]) => {
    if (shouldLog('warn')) console.warn('[yunshu]', ...args)
  },
  error: (...args: unknown[]) => {
    if (shouldLog('error')) console.error('[yunshu]', ...args)
  },
}
