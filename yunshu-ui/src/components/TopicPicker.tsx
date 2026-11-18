/**
 * 主题类多选（主线装载 / 分身装载共用）。
 *
 * 【为什么做成受控组件】两处都要用，且选项词表只能有一份
 * （`@/lib/topics`）；页面里各写一排按钮就会出现两套文案。
 * 【交互契约】点掉最后一个类 ⇒ 自动回到 `general`（"一个都不装"不是合法状态：
 * 那等于任何检索都返回空，看起来像坏了）。
 */
import { Button } from '@/components/ui'
import { TOPIC_VOCABULARY, DEFAULT_TOPIC, normalizeTopics } from '@/lib/topics'

/* 选中/未选中交给 Button 的 accent / default 两个语义档（2026-11-18 收口）：
   原来这里是两条 tailwind 色名（cyan-700 / slate-700），与全局主色不是同一支青。 */

export function TopicPicker({
  value,
  onChange,
  disabled = false,
  testId = 'topic-picker',
}: {
  value: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
  testId?: string
}) {
  const selected = normalizeTopics(value ?? [])

  const toggle = (id: string) => {
    if (disabled) return
    const next = selected.includes(id)
      ? selected.filter((t) => t !== id)
      : [...selected, id]
    onChange(next.length ? next : [DEFAULT_TOPIC])
  }

  return (
    <div className="flex flex-wrap gap-1.5" data-testid={testId}>
      {TOPIC_VOCABULARY.map((t) => {
        const on = selected.includes(t.id)
        return (
          <Button
            key={t.id}
            size="sm"
            className="rounded-full"
            variant={on ? 'accent' : 'default'}
            disabled={disabled}
            aria-pressed={on}
            title={t.hint}
            onClick={() => toggle(t.id)}
            data-testid={`${testId}-${t.id}`}
          >
            {t.label}
            <span className="ml-1 font-mono text-micro opacity-70">{t.id}</span>
          </Button>
        )
      })}
    </div>
  )
}
