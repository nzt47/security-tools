import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * 合并 Tailwind 类名：支持条件 / 数组写法（clsx），并自动去重冲突类（tailwind-merge）。
 * 所有组件样式合并统一走此函数，禁止用模板字符串拼接 className。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
