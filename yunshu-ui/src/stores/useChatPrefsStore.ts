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

// ─── 对话模式（后端 /api/chat/stream 的 mode 参数） ──────────────
// 【为什么做成三档可选而不是替他定档】三档对应**行为与成本的三种取舍**（见各 hint）：
//   plain     = 现状（真流式、1 次模型调用、不查记忆/知识库）
//   retrieval = 在 plain 之上注入检索到的记忆/知识片段（+≤3k token/轮，仍是真流式）
//   full      = 走编排器全链路（意图分层 + 检索 + 规划 + 工具）—— 最准，但**非流式**且最贵
//   full_stream = 编排前段（语义层技能命中 + 检索装配）+ **工作台流式工具循环**回答 ——
//                 2026-10-03 实测：full 在长答问题上首字 = 完成 = 8.80s（全程无输出），
//                 plain 首字 2.61s / retrieval 2.94s ⇒ 于是补这一档，兼顾"编排前段"与首字延迟。
// 默认 plain：与"改这一行之前"的行为逐字一致；想要更准由用户自己按场景切换。

export type ChatMode = 'plain' | 'retrieval' | 'full' | 'full_stream'

export const CHAT_MODES: Record<ChatMode, { label: string; icon: string; hint: string }> = {
  plain: {
    label: '轻量',
    icon: '⚡',
    hint: '只用本会话历史（默认）：最快最省，但不会去查长期记忆/知识库；纯问候/道谢本地直答、不调用模型',
  },
  retrieval: {
    label: '检索',
    icon: '🔎',
    hint: '在轻量之上注入检索到的记忆/知识片段（约 +3k token/轮，仍是真流式）；纯问候/道谢同样本地直答',
  },
  full: {
    label: '完整',
    icon: '🧠',
    hint: '走编排器全链路：意图分层 + 检索 + 规划 + 工具。回答最完整，但**非流式**且最贵（可能多轮模型调用）',
  },
  full_stream: {
    label: '完整·流式',
    icon: '🧠⚡',
    hint: '编排前段（语义层技能命中 + 检索装配）+ 流式工具循环回答：兼顾"编排器前段"与逐字输出；不跑意图拒识/规划',
  },
}

// ─── 权限档位（对齐 DSH 的 sandbox/permission modes；随每次请求发给后端） ──
//   档位只改"这次对话里工具要不要一条条点审批"，不改任何能力本身：
//   read_only 只放行只读能力；workspace 让读写免审批、执行命令仍逐次确认；
//   auto 保持既有 L0–L3 分级（默认）；full 跳过全部审批（可信环境才用）。

export type PermissionMode = 'read_only' | 'workspace' | 'auto' | 'full'

export const PERMISSION_MODES: Record<PermissionMode, { label: string; icon: string; hint: string }> = {
  read_only: {
    label: '仅可查看',
    icon: '🔒',
    hint: '只放行只读能力（读文件/列目录/检索）。写盘与执行命令一律拒绝，最安全',
  },
  workspace: {
    label: '工作区修改',
    icon: '📁',
    hint: '读写工作区文件免审批；执行命令（shell 等）仍需逐次确认',
  },
  auto: {
    label: '自动审查',
    icon: '🤖',
    hint: '默认：按 L0–L3 分级与描述符/伦理规则逐条判定，该确认的才确认',
  },
  full: {
    label: '完全权限',
    icon: '⚡',
    hint: '跳过全部审批（含 L2/L3）。黑名单与受限会话判定仍生效；仅可信环境使用',
  },
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
  /** 对话模式（决定后端跑哪条链路；见 CHAT_MODES 的说明） */
  mode: ChatMode
  /** 权限档位（决定工具要不要逐条审批；随请求体 permission_mode 发给后端） */
  permissionMode: PermissionMode
  theme: ChatThemeKey
  bubbleStyle: ChatBubbleKey
  fontSize: ChatFontKey

  toggleDisplay: (key: 'thinking' | 'toolcalls') => void
  setDisplay: (key: 'thinking' | 'toolcalls', value: boolean) => void
  setStyle: (patch: Partial<Pick<ChatPrefsState, 'format' | 'theme' | 'bubbleStyle' | 'fontSize'>>) => void
  setMode: (mode: ChatMode) => void
  setPermissionMode: (mode: PermissionMode) => void
  resetStyle: () => void
}

export const DEFAULT_STYLE = {
  format: 'bubble' as ChatFormat,
  // 默认轻量：与引入本开关之前的行为**逐字一致**（升级不该悄悄改变已有用户的对话成本）
  mode: 'plain' as ChatMode,
  // 默认自动审查：与"加这个档位之前"的行为一致（该确认的才确认）
  permissionMode: 'auto' as PermissionMode,
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
      setMode: (mode) => set({ mode }),
      setPermissionMode: (mode) => set({ permissionMode: mode }),
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
        mode: s.mode,
        permissionMode: s.permissionMode,
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
