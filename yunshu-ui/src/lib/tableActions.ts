/**
 * 三张能力表（技能中心 / 工具集 / 工作流）的**行内按钮**样式 —— 唯一口径。
 *
 * 【为什么】2026-11-08 Operator 反馈：三张表的按钮大小/形状不一致（技能 11px + px-2/py-1，
 * 工具与工作流 12px + px-3/py-1.5，还有实心蓝/实心绿），同一屏对照能直接看出来。
 * 现在形状（圆角/内边距/字号/图标间距）只在这里定义一次，**颜色表达语义**：
 * - 中性动作（停用 / 查看 / 更多）：`TABLE_BTN`
 * - 主动作（启用 / 执行 / 评审-评估）：`TABLE_BTN_PRIMARY`（emerald）
 * - 信息/进阶动作（导出为 LLM 参考等）：`TABLE_BTN_INFO`（cyan）
 * - 破坏性动作（删除 / 移除）：`TABLE_BTN_DANGER`（rose）
 *
 * 不用实心填充：表里一行最多 5 个按钮，实心块会把「说明/步骤」的视觉权重抢走。
 */

/** 统一形状：11px + 紧凑内边距 + 图标与文字 4px 间距 */
const BASE = 'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-50'

/** 中性动作 */
export const TABLE_BTN = `${BASE} border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-slate-200`
/** 主动作（启用 / 执行 / 评审） */
export const TABLE_BTN_PRIMARY = `${BASE} border-emerald-800/70 text-emerald-300 hover:bg-emerald-950/40`
/** 信息/进阶动作 */
export const TABLE_BTN_INFO = `${BASE} border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10`
/** 破坏性动作（删除 / 移除） */
export const TABLE_BTN_DANGER = `${BASE} border-rose-800/70 text-rose-300 hover:bg-rose-950/40`

/** 启停按钮的统一样式：启用=主动作色，停用=中性色（三张表同一个判断） */
export function toggleBtnClass(enabled: boolean | undefined): string {
  return enabled ? TABLE_BTN : TABLE_BTN_PRIMARY
}
