/**
 * Switch —— 开关（**开=亮、关=灰**，开关本身就是状态，不再另设「开启/关闭」文字列）
 * ------------------------------------------------------------------
 * Owner 2026-11-16 三条硬口径：
 *   ① 开启时高亮（主色轨道 + 发光 + 白色滑块靠右）；
 *   ② 关闭时灰色（中性轨道 + 灰滑块靠左）；
 *   ③ 形制必须与标签/徽章区分开：**胶囊轨道 + 圆形滑块**，不带文字（没有「启用/停用」字样，
 *      也没有旁边的状态列）。读屏靠 role="switch" + aria-checked。
 *
 * 与 tags/badges 的区别是刻意的：badge 是矩形描边小标签、tag 是文字块；这里是 34×18 的
 * 控件尺寸轨道 + 14px 滑块，且占据可点击的 20px 高命中区。
 */
import { forwardRef, useId } from 'react'
import { cn } from '@/lib/cn'

export type SwitchSize = 'sm' | 'md'

export interface SwitchProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'value'> {
  checked: boolean
  /** 变更回调（受控）：拿到新值，避免调用方再写 !checked */
  onCheckedChange: (next: boolean) => void
  size?: SwitchSize
  /** 无障碍名（无可见文字，必须给）；仅读屏可见 */
  label: string
  /** 受控/非受控都可：传了 checked 就是受控 */
  loading?: boolean
}

const TRACK: Record<SwitchSize, string> = { sm: 'h-4 w-8', md: 'h-[18px] w-[34px]' }
const THUMB: Record<SwitchSize, string> = { sm: 'h-3 w-3', md: 'h-3.5 w-3.5' }
const SHIFT: Record<SwitchSize, string> = { sm: 'translate-x-4', md: 'translate-x-[16px]' }

const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { checked, onCheckedChange, size = 'md', label, className, disabled, loading, ...props },
  ref,
) {
  const id = useId()
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled || loading}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        // 轨道：胶囊形（与标签/徽章的圆角矩形区分）
        'relative inline-flex shrink-0 items-center rounded-full border transition-all duration-150',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        TRACK[size],
        // 开：主色轨道 + 发光；关：中性灰轨道
        checked
          ? 'border-primary/70 bg-primary/85 shadow-[0_0_10px_-1px_var(--color-primary)]'
          : 'border-border bg-muted',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden
        className={cn(
          'pointer-events-none absolute left-[2px] rounded-full shadow-sm transition-transform duration-150',
          THUMB[size],
          // 开：白色滑块靠右；关：灰滑块靠左
          checked ? cn('bg-primary-foreground', SHIFT[size]) : 'bg-muted-foreground/70',
        )}
      />
    </button>
  )
})

export default Switch
