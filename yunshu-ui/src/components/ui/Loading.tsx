/**
 * Loading —— 居中加载态（语义 Token 驱动）
 * 替代各页面裸用 <Loader2> 的重复代码。
 */
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface LoadingProps {
  /** 加载文案，可选 */
  text?: string
  className?: string
}

export default function Loading({ text, className }: LoadingProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 py-8 text-muted-foreground',
        className,
      )}
    >
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      {text && <p className="text-sm">{text}</p>}
    </div>
  )
}
