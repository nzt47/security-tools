/**
 * 主题类多选（主线装载 / 分身装载共用）。
 *
 * 【为什么做成受控组件】两处都要用，且选项词表只能有一份
 * （`@/lib/topics`）；页面里各写一排按钮就会出现两套文案。
 * 【交互契约】点掉最后一个类 ⇒ 自动回到 `general`（"一个都不装"不是合法状态：
 * 那等于任何检索都返回空，看起来像坏了）。
 */
import { TOPIC_VOCABULARY, DEFAULT_TOPIC, normalizeTopics } from '@/lib/topics'

const ON = 'border-cyan-700 bg-cyan-950/50 text-cyan-200'
const OFF = 'border-slate-700 bg-slate-900/60 text-slate-400 hover:border-slate-600'

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
          <button
            key={t.id}
            type="button"
            disabled={disabled}
            aria-pressed={on}
            title={t.hint}
            onClick={() => toggle(t.id)}
            data-testid={`${testId}-${t.id}`}
            className={`rounded-full border px-2.5 py-1 text-[12px] transition ${on ? ON : OFF} ${disabled ? 'opacity-60' : ''}`}
          >
            {t.label}
            <span className="ml-1 font-mono text-[10px] opacity-70">{t.id}</span>
          </button>
        )
      })}
    </div>
  )
}
