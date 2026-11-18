/**
 * CodeBlock —— 代码 / JSON / 原始文本块（语义 Token 驱动）
 * ------------------------------------------------------------------
 * 【为什么补这一档】2026-11-18 封装审计：19 个文件各自定义了一份 `const PRE = '…'`
 * （max-h-40/48/52/56/64/72 六种高度、rounded-sm/md、p-2/p-2.5、text-micro/caption），
 * 内容完全一样：滚动 + 换行 + 等宽 + 深底。每处复制一份的结果是"同一屏里代码块三种底色/字号"。
 *
 * 与旧 PRE 常量的对应关系：
 *   · 高度 → `maxH`（px，默认 224 = 原 max-h-56）；
 *   · 字号 → `size`（micro 10px / caption 11px，后者用于长文预览）；
 *   · 其余布局（mt-2 / mb-2 / 覆盖底色）→ `className`，与原写法一致。
 *
 * 颜色走 Token：border-border / bg-background / text-muted-foreground（不再写 slate 字面值）。
 */
import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

export type CodeBlockSize = 'micro' | 'caption'

export interface CodeBlockProps extends React.HTMLAttributes<HTMLPreElement> {
  /** 最大高度（px），超出滚动；默认 224 */
  maxH?: number
  /** 字号档：micro（10px，默认，JSON/控制台）/ caption（11px，长文预览） */
  size?: CodeBlockSize
}

const SIZE_CLASSES: Record<CodeBlockSize, string> = {
  micro: 'text-micro',
  caption: 'text-caption leading-relaxed',
}

const CodeBlock = forwardRef<HTMLPreElement, CodeBlockProps>(function CodeBlock(
  { className, maxH = 224, size = 'micro', style, children, ...props },
  ref,
) {
  return (
    <pre
      ref={ref}
      className={cn(
        'overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-border bg-background p-2 font-mono text-muted-foreground',
        SIZE_CLASSES[size],
        className,
      )}
      style={{ maxHeight: maxH, ...style }}
      {...props}
    >
      {children}
    </pre>
  )
})

export default CodeBlock
