/**
 * 通用设置 · 快捷键表与按键判定（唯一事实源）
 * ------------------------------------------------------------------
 * 表只列**今天真的存在**的快捷键：没有的不要写进来自我感动。
 * 判定函数单独导出，便于单元测试（不需要渲染组件）。
 */

export interface ShortcutItem {
  /** 按键组合（展示用，按顺序渲染 keycap） */
  keys: string[]
  /** 这个组合做什么 */
  action: string
  /** 生效范围，缺省 = 全局 */
  scope?: string
}

/** 修饰键判定：Ctrl 或 ⌘，且不按 Alt */
function withPrimaryModifier(event: Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey'>): boolean {
  return (event.ctrlKey || event.metaKey) && !event.altKey
}

/** Ctrl/⌘ + , ⇒ 打开 / 关闭通用设置 */
export function isSettingsShortcut(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>,
): boolean {
  return withPrimaryModifier(event) && event.key === ','
}

/** Ctrl/⌘ + / ⇒ 打开通用设置并定位到「快捷键」分区 */
export function isShortcutHelpShortcut(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>,
): boolean {
  return withPrimaryModifier(event) && event.key === '/'
}

/** 快捷键表（顺序即展示顺序；与组件里的真实绑定逐条对应） */
export const SHORTCUT_ITEMS: ShortcutItem[] = [
  { keys: ['Ctrl/⌘', ','], action: '打开 / 关闭通用设置' },
  { keys: ['Ctrl/⌘', '/'], action: '打开通用设置 · 快捷键' },
  { keys: ['Esc'], action: '关闭弹窗 / 收起菜单' },
  { keys: ['Enter'], action: '发送消息', scope: '对话输入框' },
  { keys: ['Shift', 'Enter'], action: '换行', scope: '对话输入框' },
]
