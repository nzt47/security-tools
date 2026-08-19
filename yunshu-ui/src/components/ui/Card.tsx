/**
 * Card —— 通用卡片容器（语义 Token 驱动）
 * - 统一 rounded-lg + shadow-card，深浅模式自动跟随 Token
 * - 页面容器一律使用本组件，禁止手写同类 div
 */
import { cn } from '@/lib/cn'

export type CardProps = React.HTMLAttributes<HTMLDivElement>

export default function Card({ className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card text-card-foreground shadow-card',
        className,
      )}
      {...props}
    />
  )
}
