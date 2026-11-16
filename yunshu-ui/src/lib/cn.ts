import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

/**
 * 字号阶梯的 9 个自定义档位（来源：index.css @theme 的 --text-*）。
 * ----------------------------------------------------------------
 * 为什么必须在这里登记：tailwind-merge 默认只认识 text-xs / text-sm / text-base…，
 * 遇到 `text-micro` / `text-body` / `text-ui` 这类**自定义**档位会把它归进「文字颜色」组，
 * 于是和同一次调用里的 `text-muted-foreground` 判为冲突，**静默丢掉字号**：
 *   twMerge('text-micro', 'text-muted-foreground') === 'text-muted-foreground'
 * 后果是"字号档位写了等于没写"——实测 ui/Button(primary) 的 text-ui 被 text-primary-foreground
 * 吃掉、会话栏 ghost 按钮的 text-micro 被 text-muted-foreground 吃掉（2026-11-16）。
 * 登记成 font-size 组之后，字号与颜色互不冲突。
 */
const FONT_SIZE_SCALE = [
  'micro',
  'caption',
  'body',
  'body-lg',
  'ui',
  'title',
  'h2',
  'h1',
  'display',
] as const

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: [...FONT_SIZE_SCALE] }],
    },
  },
})

/**
 * 合并 Tailwind 类名：支持条件 / 数组写法（clsx），并自动去重冲突类（tailwind-merge）。
 * 所有组件样式合并统一走此函数，禁止用模板字符串拼接 className。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
