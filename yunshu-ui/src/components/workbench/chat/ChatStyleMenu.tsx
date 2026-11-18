/**
 * ChatStyleMenu —— 对话风格设置（对齐 legacy「🎨 对话风格」）
 * ------------------------------------------------------------------
 * 三组可选项：
 *   - 颜色主题（午夜/海洋/森林/暖阳/紫韵）
 *   - 气泡样式（圆润/现代/简约）
 *   - 字体大小（小/中/大）
 * 另有实时预览气泡与「恢复默认」。所有选择写入 useChatPrefsStore（LocalStorage 持久化）。
 * 输出格式（气泡/紧凑/终端）另由 ChatPanel 工具栏的切换器控制（同一 store）。
 */
import { useEffect, useRef, useState } from 'react'
import { Palette, RotateCcw, X } from 'lucide-react'
import { Button } from '@/components/ui'
import {
  CHAT_BUBBLE_STYLES,
  CHAT_FONT_SIZES,
  CHAT_THEMES,
  DEFAULT_STYLE,
  chatStyleVars,
  useChatPrefsStore,
  type ChatBubbleKey,
  type ChatFontKey,
  type ChatThemeKey,
} from '../../../stores/useChatPrefsStore'

export function ChatStyleMenu() {
  const [open, setOpen] = useState(false)
  const theme = useChatPrefsStore((s) => s.theme)
  const bubbleStyle = useChatPrefsStore((s) => s.bubbleStyle)
  const fontSize = useChatPrefsStore((s) => s.fontSize)
  const setStyle = useChatPrefsStore((s) => s.setStyle)
  const resetStyle = useChatPrefsStore((s) => s.resetStyle)
  const boxRef = useRef<HTMLDivElement>(null)

  // 点击外部 / Esc 关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const previewVars = chatStyleVars({ theme, bubbleStyle, fontSize })
  const isDefault =
    theme === DEFAULT_STYLE.theme &&
    bubbleStyle === DEFAULT_STYLE.bubbleStyle &&
    fontSize === DEFAULT_STYLE.fontSize

  return (
    <div className="relative" ref={boxRef}>
      <Button
        size="xs"
        variant="default"
        active={open}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="对话风格设置（主题 / 气泡样式 / 字号）"
      >
        <Palette size={11} />
        风格
      </Button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-70 w-[286px] rounded-xl border border-slate-700 bg-slate-900/98 p-3 shadow-2xl backdrop-blur-sm">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-body font-medium text-slate-200">🎨 对话风格</span>
            <Button type="button" onClick={() => setOpen(false)} aria-label="关闭风格设置" variant="ghost" size="icon-xs">
              <X size={12} />
            </Button>
          </div>

          {/* 颜色主题 */}
          <div className="mb-3">
            <div className="mb-1.5 text-micro uppercase tracking-wider text-slate-500">颜色主题</div>
            <div className="flex gap-2">
              {(Object.keys(CHAT_THEMES) as ChatThemeKey[]).map((k) => {
                const t = CHAT_THEMES[k]
                return (
                  <button
                    key={k}
                    type="button"
                    title={t.label}
                    onClick={() => setStyle({ theme: k })}
                    style={{
                      background: t.vars['--chat-bot-bg'],
                      borderColor: theme === k ? '#22d3ee' : t.vars['--chat-bot-border'],
                    }}
                    className={`flex h-8 w-8 items-center justify-center rounded-lg border text-body-lg transition-transform ${
                      theme === k ? 'scale-105 ring-2 ring-cyan-400/60' : 'hover:scale-105'
                    }`}
                  >
                    {t.icon}
                  </button>
                )
              })}
            </div>
          </div>

          {/* 气泡样式 */}
          <div className="mb-3">
            <div className="mb-1.5 text-micro uppercase tracking-wider text-slate-500">气泡样式</div>
            <div className="flex gap-1.5">
              {(Object.keys(CHAT_BUBBLE_STYLES) as ChatBubbleKey[]).map((k) => (
                <Button
                  key={k}
                  size="xs"
                  variant={bubbleStyle === k ? 'accent' : 'default'}
                  className="flex-1"
                  onClick={() => setStyle({ bubbleStyle: k })}
                >
                  {CHAT_BUBBLE_STYLES[k].label}
                </Button>
              ))}
            </div>
          </div>

          {/* 字号 */}
          <div className="mb-3">
            <div className="mb-1.5 text-micro uppercase tracking-wider text-slate-500">字体大小</div>
            <div className="flex gap-1.5">
              {(Object.keys(CHAT_FONT_SIZES) as ChatFontKey[]).map((k) => (
                <Button
                  key={k}
                  size="xs"
                  variant={fontSize === k ? 'accent' : 'default'}
                  className="flex-1"
                  onClick={() => setStyle({ fontSize: k })}
                >
                  {CHAT_FONT_SIZES[k].label}
                </Button>
              ))}
            </div>
          </div>

          {/* 预览 */}
          <div className="mb-2 rounded-lg border border-slate-700/70 bg-slate-950/60 p-2">
            <div className="mb-1 text-micro text-slate-500">效果预览</div>
            <div style={previewVars as React.CSSProperties} className="flex flex-col gap-1.5">
              <div className="wb-bubble wb-bubble-bot" style={{ fontSize: previewVars['--chat-fs'] }}>
                云枢回复将显示为这种样式
              </div>
              <div className="wb-bubble wb-bubble-user self-end" style={{ fontSize: previewVars['--chat-fs'] }}>
                你的消息将显示为这种样式
              </div>
            </div>
          </div>

          <Button type="button" onClick={resetStyle} disabled={isDefault} className="w-full" variant="default" size="xs">
            <RotateCcw size={11} /> 恢复默认风格
          </Button>
        </div>
      )}
    </div>
  )
}
