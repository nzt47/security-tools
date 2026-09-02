/**
 * 格式化工具（零依赖纯函数）
 * ------------------------------------------------------
 * - formatDate：日期格式化（YYYY/MM/DD/HH/mm/ss 占位）
 * - formatBytes：字节 → 人类可读（B/KB/MB/GB）
 * - formatNumber：千分位数字
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
