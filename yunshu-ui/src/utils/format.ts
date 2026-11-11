/**
 * 格式化工具（零依赖纯函数）
 * ------------------------------------------------------
 * - formatDate：日期格式化（YYYY/MM/DD/HH/mm/ss 占位）
 * - formatBytes：字节 → 人类可读（B/KB/MB/GB）
 * - formatNumber：千分位数字
 * - formatAge：距今秒数 → 中文相对时间（刚刚 / N 分钟前 / N 小时前 / N 天前）
 */

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

/**
 * 日期格式化。支持时间戳（number）/ ISO 字符串 / Date。
 * pattern 占位：YYYY / MM / DD / HH / mm / ss；缺省 'YYYY-MM-DD HH:mm:ss'。
 * 非法输入返回 ''（调用方自行决定展示空态）。
 */
export function formatDate(input: number | string | Date, pattern = 'YYYY-MM-DD HH:mm:ss'): string {
  const date = typeof input === 'object' ? input : new Date(input)
  if (Number.isNaN(date.getTime())) return ''

  const map: Record<string, string> = {
    YYYY: String(date.getFullYear()),
    MM: pad2(date.getMonth() + 1),
    DD: pad2(date.getDate()),
    HH: pad2(date.getHours()),
    mm: pad2(date.getMinutes()),
    ss: pad2(date.getSeconds()),
  }
  return pattern.replace(/YYYY|MM|DD|HH|mm|ss/g, (token) => map[token])
}

/** 字节 → 人类可读（1024 进制）；负数视为非法返回 '0 B' */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB'] as const
  let value = bytes / 1024
  let unit: (typeof units)[number] = units[0]
  for (let i = 1; i < units.length; i += 1) {
    if (value < 1024) {
      unit = units[i - 1]
      break
    }
    value /= 1024
    unit = units[i]
  }
  // 保留 2 位并去掉多余的 0（如 1.50 MB → 1.5 MB）
  const rounded = Math.round(value * 100) / 100
  return `${rounded} ${unit}`
}

/** 千分位数字：1234567.891 → '1,234,567.891'；非法输入回退 '0' */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return '0'
  const [intPart, decimalPart] = String(n).split('.')
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return decimalPart === undefined ? grouped : `${grouped}.${decimalPart}`
}

/**
 * 距今秒数 → 中文相对时间。
 *
 * 【为什么要一个共用实现】"最后同步是几点 / 最后事件是几点"这类读数散落在多个面板，
 * 各写一份就会出现"3 分钟前"和"0.05 小时前"两套口径（本仓已有两处本地实现，
 * 新增读数统一走这里）。
 *
 * 口径：null/undefined/负数/非有限值 **不返回 0**，返回 '—'（L5：没读到 ≠ 刚刚发生）；
 * 60s 内 '刚刚'，60 分钟 '/ N 分钟前'，24 小时 '/ N 小时前'，否则 '/ N 天前'。
 */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 60) return '刚刚'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

