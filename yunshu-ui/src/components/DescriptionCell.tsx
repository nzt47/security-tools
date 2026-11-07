/**
 * 「说明」列的**唯一**渲染口径：技能中心 / 工作流 / 工具三张表共用这一份。
 *
 * 【为什么要抽出来（2026-11-07 Operator 反馈）】三张表的说明列此前各写一份：
 * - 工作流：line-clamp-3 + max-w-[420px]（#1495 才从 truncate 改过来）；
 * - 技能中心：line-clamp-3 + text-[11px]，另带「查看全文」按钮；
 * - 工具：**根本没有说明列**，描述挤在「工具」列名字底下。
 * 于是同一个字段在三个地方三种长相。现在统一到这里：最多 3 行 + 自动换行 + 悬停看全文，
 * 宽度上限与字号只有一处定义。新增列表要显示说明，直接用本组件，不要再手写 className。
 *
 * 【为什么宽度上限走 inline style 而不是 class】Tailwind 的任意值类（max-w-[420px]）不能
 * 动态拼接（JIT 扫不到），而列宽在这三张表里又是可拖动/可变的，故用数值常量 + style。
 */

/** 说明列的默认宽度上限（px）：与 #1495 定下的工作流口径一致 */
export const DESCRIPTION_MAX_WIDTH = 420

export interface DescriptionCellProps {
  /** 说明正文（中文说明常见 20–400 字；空/空白一律按「无说明」处理） */
  text?: string | null
  /** 需要在弹窗里看全文时给回调（技能中心）；不给则不渲染「查看全文」 */
  onExpand?: () => void
  /** 宽度上限覆盖（默认 {@link DESCRIPTION_MAX_WIDTH}） */
  maxWidth?: number
}

export default function DescriptionCell({
  text,
  onExpand,
  maxWidth = DESCRIPTION_MAX_WIDTH,
}: DescriptionCellProps) {
  const body = String(text ?? '').trim()
  if (!body) {
    return <span className="text-[10px] text-slate-600">（无说明）</span>
  }
  return (
    <div style={{ maxWidth }}>
      {/* 【字号必须写死】三张表的行继承字号不同（技能中心 12px、工具/工作流 14px）——只靠继承
          就会出现"同组件不同长相"。这里钉成 text-xs，三表逐像素一致（leading-relaxed ⇒ 19.5px）。 */}
      <div className="line-clamp-3 wrap-break-word text-xs leading-relaxed text-slate-400" title={body}>
        {body}
      </div>
      {onExpand && (
        <button
          type="button"
          className="mt-1 text-[10px] text-cyan-400 hover:text-cyan-300"
          onClick={onExpand}
          title="在弹窗里看完整说明（列宽不会被撑开）"
        >
          查看全文
        </button>
      )}
    </div>
  )
}
