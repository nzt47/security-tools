/**
 * 对话显示与风格偏好（Zustand + LocalStorage 持久化）
 * ------------------------------------------------
 * 两块相互独立的偏好：
 *  1. 显示开关（display）：思考过程 / 工具调用 —— 对应 legacy「💭 Thought / 🔧 工具」
 *     两个开关（legacy 的 localStorage 键为 yunshu_display_<key>，此处沿用同名语义）。
 *  2. 对话风格（style）：输出格式（气泡 / 紧凑 / 终端）+ 颜色主题 + 气泡样式 + 字号，
 *     选项与 legacy「🎨 对话风格」一致（颜色主题 5 套、气泡 3 档、字号 3 档），
 *     另加「输出格式」用于切换**对话输出展示样式**。
 *
 * 样式落地方式：组件把 style 折算成 CSS 变量（--chat-*）挂在容器上，
 * 由 workbench.css 的 .wb-chat-format-* 规则消费（不改动全局主题变量）。
 */
import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'

// ─── 对话输出格式（对话输出展示样式） ───────────────────────────

export type ChatFormat = 'bubble' | 'compact' | 'terminal'

export const CHAT_FORMATS: Record<ChatFormat, { label: string; hint: string }> = {
  bubble: { label: '气泡', hint: '经典聊天气泡：头像 + 气泡 + 时间（默认）' },
  compact: { label: '紧凑', hint: '紧凑列表：隐藏头像，双栏角色标签，信息密度更高' },
  terminal: { label: '终端', hint: '等宽终端风格：适合看代码/日志类长回答' },
}

// ─── 颜色主题（5 套，取自 legacy CHAT_THEMES） ──────────────────

export type ChatThemeKey = 'midnight' | 'ocean' | 'forest' | 'warm' | 'purple'

export const CHAT_THEMES: Record<ChatThemeKey, {
  label: string
  icon: string
  vars: Record<string, string>
}> = {
  midnight: {
    label: '午夜',
    icon: '🌙',
    vars: {
      '--chat-user-bg': '#1f6feb30',
      '--chat-user-border': '#1f6feb50',
      '--chat-user-text': '#c9d1d9',
      '--chat-bot-bg': '#161b22',
      '--chat-bot-border': '#30363d',
      '--chat-bot-text': '#c9d1d9',
    },
  },
  ocean: {
    label: '海洋',
    icon: '🌊',
    vars: {
      '--chat-user-bg': '#0ea5e920',
      '--chat-user-border': '#0ea5e950',
      '--chat-user-text': '#e0f2fe',
      '--chat-bot-bg': '#0f172a',
      '--chat-bot-border': '#1e3a5f',
      '--chat-bot-text': '#e0f2fe',
    },
  },
  forest: {
    label: '森林',
    icon: '🌿',
    vars: {
      '--chat-user-bg': '#22c55e20',
      '--chat-user-border': '#22c55e40',
      '--chat-user-text': '#dcfce7',
      '--chat-bot-bg': '#052e16',
      '--chat-bot-border': '#166534',
      '--chat-bot-text': '#dcfce7',
    },
  },
  warm: {
    label: '暖阳',
    icon: '☀️',
    vars: {
      '--chat-user-bg': '#f9731620',
      '--chat-user-border': '#f9731640',
      '--chat-user-text': '#fff7ed',
      '--chat-bot-bg': '#1c1917',
      '--chat-bot-border': '#7c2d12',
      '--chat-bot-text': '#fff7ed',
    },
  },
  purple: {
    label: '紫韵',
    icon: '🔮',
    vars: {
      '--chat-user-bg': '#a855f720',
      '--chat-user-border': '#a855f740',
      '--chat-user-text': '#f3e8ff',
      '--chat-bot-bg': '#150e1f',
      '--chat-bot-border': '#4a245e',
      '--chat-bot-text': '#f3e8ff',
    },
  },
}

// ─── 气泡样式 / 字号（legacy 同款三档） ────────────────────────

export type ChatBubbleKey = 'rounded' | 'modern' | 'minimal'

export const CHAT_BUBBLE_STYLES: Record<ChatBubbleKey, {
  label: string
  vars: Record<string, string>
}> = {
  rounded: {
    label: '圆润',
    vars: {
      '--chat-bubble-radius': '14px',
      '--chat-bubble-radius-user': '14px 14px 4px 14px',
      '--chat-bubble-radius-bot': '14px 14px 14px 4px',
    },
  },
  modern: {
    label: '现代',
    vars: {
      '--chat-bubble-radius': '10px',
      '--chat-bubble-radius-user': '10px 10px 2px 10px',
      '--chat-bubble-radius-bot': '10px 10px 10px 2px',
    },
  },
  minimal: {
    label: '简约',
    vars: {
      '--chat-bubble-radius': '6px',
      '--chat-bubble-radius-user': '6px',
      '--chat-bubble-radius-bot': '6px',
    },
  },
}

export type ChatFontKey = 'small' | 'normal' | 'large'

export const CHAT_FONT_SIZES: Record<ChatFontKey, { label: string; value: string }> = {
  small: { label: '小', value: '13px' },
  normal: { label: '中', value: '14px' },
  large: { label: '大', value: '16px' },
}

// ─── Store ────────────────────────────────────────────────────

interface ChatPrefsState {
  /** 显示思考过程（💭 Thought） */
  showThinking: boolean
  /** 显示工具调用步骤（🔧 工具） */
  showToolCalls: boolean
  /** 对话输出格式 */
  format: ChatFormat
  theme: ChatThemeKey
  bubbleStyle: ChatBubbleKey
  fontSize: ChatFontKey

  toggleDisplay: (key: 'thinking' | 'toolcalls') => void
  setDisplay: (key: 'thinking' | 'toolcalls', value: boolean) => void
  setStyle: (patch: Partial<Pick<ChatPrefsState, 'format' | 'theme' | 'bubbleStyle' | 'fontSize'>>) => void
  resetStyle: () => void
}

export const DEFAULT_STYLE = {
  format: 'bubble' as ChatFormat,
  theme: 'midnight' as ChatThemeKey,
  bubbleStyle: 'rounded' as ChatBubbleKey,
  fontSize: 'normal' as ChatFontKey,
}

/** 持久化键（带版本，升级结构时变更） */
export const CHAT_PREFS_STORAGE_KEY = 'yunshu:chat:prefs:v1'

export const useChatPrefsStore = create<ChatPrefsState>()(
  persist(
    (set) => ({
      // 与 legacy 默认一致：思考/工具默认显示
      showThinking: true,
      showToolCalls: true,
      ...DEFAULT_STYLE,

      toggleDisplay: (key) =>
        set((s) => (key === 'thinking' ? { showThinking: !s.showThinking } : { showToolCalls: !s.showToolCalls })),
      setDisplay: (key, value) =>
        set(key === 'thinking' ? { showThinking: value } : { showToolCalls: value }),
      setStyle: (patch) => set(patch),
      resetStyle: () => set({ ...DEFAULT_STYLE }),
    }),
    {
      name: CHAT_PREFS_STORAGE_KEY,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({
        showThinking: s.showThinking,
        showToolCalls: s.showToolCalls,
        format: s.format,
        theme: s.theme,
        bubbleStyle: s.bubbleStyle,
        fontSize: s.fontSize,
      }),
    },
  ),
)

/** 由偏好折算容器 CSS 变量（主题 + 气泡 + 字号），供消息列表容器直接铺开 */
export function chatStyleVars(
  prefs: Pick<ChatPrefsState, 'theme' | 'bubbleStyle' | 'fontSize'>,
): Record<string, string> {
  return {
    ...(CHAT_THEMES[prefs.theme]?.vars ?? CHAT_THEMES.midnight.vars),
    ...(CHAT_BUBBLE_STYLES[prefs.bubbleStyle]?.vars ?? CHAT_BUBBLE_STYLES.rounded.vars),
    '--chat-fs': CHAT_FONT_SIZES[prefs.fontSize]?.value ?? '14px',
  }
}
