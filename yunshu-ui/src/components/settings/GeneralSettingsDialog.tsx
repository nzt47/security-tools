/**
 * GeneralSettingsDialog —— 通用设置（2026-11-28，借鉴 DSH「通用」分区）
 * ------------------------------------------------------------------
 * 【它解决什么】云枢的用户偏好此前散在对话风格菜单 / 权限菜单 / 各处开关里，
 * 没有一处能看全，也没有全局入口。本弹窗只做「收拢入口」：
 *   · 内容全部镜像**既有** store（useChatPrefsStore）与只读版本读数；
 *   · 不新增持久化键、不新增路由、不新增环境变量 ⇒ 不触发 registry / 路由派生纪律；
 *   · 写入即时生效（zustand 持久化），失败无从发生（本地存储），故不需要 DSH 那套
 *     「写入中禁用 + 失败重试」的状态机；开关立即反映 store 真值。
 *
 * 【为什么没有「界面主题 亮/暗」】云枢目前只有一套深色 token（index.css 无 .dark 覆盖块），
 * 做一个点了没效果的开关比不做更糟；等真有浅色 token 再加这一行。
 */
import type { ReactNode } from 'react'
import { Button, ModalBase, Select, Switch, Tabs } from '@/components/ui'
import {
  CHAT_BUBBLE_STYLES,
  CHAT_FONT_SIZES,
  CHAT_FORMATS,
  CHAT_MODES,
  CHAT_THEMES,
  PERMISSION_MODES,
  useChatPrefsStore,
  type ChatBubbleKey,
  type ChatFontKey,
  type ChatFormat,
  type ChatMode,
  type ChatThemeKey,
  type PermissionMode,
} from '@/stores/useChatPrefsStore'
import AboutSection from './AboutSection'
import { SHORTCUT_ITEMS } from './shortcuts'

export type SettingsSectionId = 'general' | 'appearance' | 'shortcuts' | 'about'

/** 分区表（顺序即导航顺序；与 Tabs 的 items 一一对应） */
export const SETTINGS_SECTIONS: { id: SettingsSectionId; label: string }[] = [
  { id: 'general', label: '通用' },
  { id: 'appearance', label: '外观' },
  { id: 'shortcuts', label: '快捷键' },
  { id: 'about', label: '关于' },
]

/** 一行偏好：左标题+说明，右控件（本文件内唯一形态，暂不提为 ui 原语） */
function Row({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 py-3">
      <div className="min-w-0">
        <div className="text-ui text-foreground">{title}</div>
        {description ? <div className="mt-0.5 text-caption text-muted-foreground">{description}</div> : null}
      </div>
      <div className="shrink-0 pt-0.5">{children}</div>
    </div>
  )
}

function toOptions<T extends string>(table: Record<T, { label: string }>): { label: string; value: string }[] {
  return (Object.keys(table) as T[]).map((key) => ({ label: table[key].label, value: key }))
}

export interface GeneralSettingsDialogProps {
  open: boolean
  /** 当前分区（受控：快捷键 Ctrl+/ 需要直接打开「快捷键」） */
  section: SettingsSectionId
  onSectionChange: (section: SettingsSectionId) => void
  onClose: () => void
}

export default function GeneralSettingsDialog({
  open,
  section,
  onSectionChange,
  onClose,
}: GeneralSettingsDialogProps) {
  const showThinking = useChatPrefsStore((s) => s.showThinking)
  const showToolCalls = useChatPrefsStore((s) => s.showToolCalls)
  const setDisplay = useChatPrefsStore((s) => s.setDisplay)
  const mode = useChatPrefsStore((s) => s.mode)
  const setMode = useChatPrefsStore((s) => s.setMode)
  const permissionMode = useChatPrefsStore((s) => s.permissionMode)
  const setPermissionMode = useChatPrefsStore((s) => s.setPermissionMode)
  const format = useChatPrefsStore((s) => s.format)
  const theme = useChatPrefsStore((s) => s.theme)
  const bubbleStyle = useChatPrefsStore((s) => s.bubbleStyle)
  const fontSize = useChatPrefsStore((s) => s.fontSize)
  const setStyle = useChatPrefsStore((s) => s.setStyle)

  return (
    <ModalBase
      open={open}
      onClose={onClose}
      title="通用设置"
      width="max-w-2xl"
      closeButton
      scrollable
      testId="general-settings-dialog"
      footer={(
        <Button type="button" variant="ghost" onClick={onClose}>关闭</Button>
      )}
    >
      <div className="space-y-4">
        <Tabs
          items={SETTINGS_SECTIONS.map((item) => ({ value: item.id, label: item.label }))}
          value={section}
          onChange={(value) => onSectionChange(value as SettingsSectionId)}
          aria-label="设置分区"
        />

        {section === 'general' && (
          <div className="divide-y divide-border">
            <Row title="对话模式" description={CHAT_MODES[mode]?.hint}>
              <Select
                aria-label="对话模式"
                className="w-40"
                size="sm"
                options={toOptions(CHAT_MODES)}
                value={mode}
                onChange={(value) => setMode(value as ChatMode)}
              />
            </Row>
            <Row title="权限档位" description={PERMISSION_MODES[permissionMode]?.hint}>
              <Select
                aria-label="权限档位"
                className="w-40"
                size="sm"
                options={toOptions(PERMISSION_MODES)}
                value={permissionMode}
                onChange={(value) => setPermissionMode(value as PermissionMode)}
              />
            </Row>
            <Row title="显示思考过程" description="对话里展示模型的思考/推理片段（💭）。">
              <Switch
                checked={showThinking}
                onCheckedChange={(next) => setDisplay('thinking', next)}
                label="显示思考过程"
              />
            </Row>
            <Row title="显示工具调用" description="对话里展示工具调用步骤与结果（🔧）。">
              <Switch
                checked={showToolCalls}
                onCheckedChange={(next) => setDisplay('toolcalls', next)}
                label="显示工具调用"
              />
            </Row>
          </div>
        )}

        {section === 'appearance' && (
          <div className="divide-y divide-border">
            <Row title="输出格式" description={CHAT_FORMATS[format]?.hint}>
              <Select
                aria-label="输出格式"
                className="w-40"
                size="sm"
                options={toOptions(CHAT_FORMATS)}
                value={format}
                onChange={(value) => setStyle({ format: value as ChatFormat })}
              />
            </Row>
            <Row title="对话主题" description="只改对话气泡配色，不动全局界面主题。">
              <Select
                aria-label="对话主题"
                className="w-40"
                size="sm"
                options={toOptions(CHAT_THEMES)}
                value={theme}
                onChange={(value) => setStyle({ theme: value as ChatThemeKey })}
              />
            </Row>
            <Row title="气泡样式" description="圆角大小与尾巴形状。">
              <Select
                aria-label="气泡样式"
                className="w-40"
                size="sm"
                options={toOptions(CHAT_BUBBLE_STYLES)}
                value={bubbleStyle}
                onChange={(value) => setStyle({ bubbleStyle: value as ChatBubbleKey })}
              />
            </Row>
            <Row title="对话字号" description="只作用于消息区，不影响其他页面。">
              <Select
                aria-label="对话字号"
                className="w-40"
                size="sm"
                options={toOptions(CHAT_FONT_SIZES)}
                value={fontSize}
                onChange={(value) => setStyle({ fontSize: value as ChatFontKey })}
              />
            </Row>
          </div>
        )}

        {section === 'shortcuts' && (
          <div className="divide-y divide-border">
            {SHORTCUT_ITEMS.map((item) => (
              <div key={item.action} className="flex items-center justify-between gap-6 py-3">
                <div className="min-w-0">
                  <div className="text-ui text-foreground">{item.action}</div>
                  {item.scope ? (
                    <div className="mt-0.5 text-caption text-muted-foreground">仅在{item.scope}</div>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {item.keys.map((key) => (
                    <kbd
                      key={key}
                      className="rounded-sm border border-border bg-muted px-1.5 py-0.5 font-mono text-micro text-foreground"
                    >
                      {key}
                    </kbd>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {section === 'about' && <AboutSection />}
      </div>
    </ModalBase>
  )
}
