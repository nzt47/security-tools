/**
 * ChatModeMenu —— 「对话模式」下拉（放在工具栏「输出格式」右侧）
 * ------------------------------------------------------------------
 * 三档对应**行为与成本的三种取舍**（选项与说明的唯一来源：useChatPrefsStore 的 CHAT_MODES）：
 *   ⚡ 轻量   = 只用本会话历史（默认；真流式、1 次模型调用）
 *   🔎 检索   = 轻量之上注入检索到的记忆/知识片段（+≤3k token/轮，仍是真流式）
 *   🧠 完整   = 走编排器全链路（意图分层 + 检索 + 规划 + 工具）—— 最准，但**非流式**且最贵
 *
 * 为什么做成用户自己选：这三者是**取舍**而不是"越好越好"——完整模式单轮可能多次模型调用、
 * 延迟从数秒升到数十秒。把选择权交给用户，并在选项说明里直说代价。
 * 选择写入 useChatPrefsStore（LocalStorage 持久化），随每次请求体 `mode` 发给后端。
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { CHAT_MODES, useChatPrefsStore, type ChatMode } from '../../../stores/useChatPrefsStore'

export function ChatModeMenu() {
  const mode = useChatPrefsStore((s) => s.mode)
  const setMode = useChatPrefsStore((s) => s.setMode)
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

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

  const current = CHAT_MODES[mode]

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        title={`对话模式：${current.label} —— ${current.hint}`}
        className={`flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors ${
          mode === 'full'
            ? 'border-violet-600/60 text-violet-300'
            : mode === 'retrieval'
              ? 'border-cyan-600/60 text-cyan-300'
              : 'border-slate-700 text-slate-400 hover:bg-slate-800'
        }`}
      >
        <span>{current.icon}</span>
        <span>模式：{current.label}</span>
        <ChevronDown size={10} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-[80] w-[330px] rounded-xl border border-slate-700 bg-slate-900/98 p-2 text-[11.5px] shadow-2xl backdrop-blur"
        >
          <div className="mb-1.5 px-1 text-[10px] uppercase tracking-wider text-slate-500">对话模式</div>
          {(Object.keys(CHAT_MODES) as ChatMode[]).map((m) => {
            const item = CHAT_MODES[m]
            const active = m === mode
            return (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => {
                  setMode(m)
                  setOpen(false)
                }}
                className={`mb-1 w-full rounded-lg border px-2 py-1.5 text-left transition-colors ${
                  active
                    ? 'border-cyan-600/60 bg-cyan-500/10'
                    : 'border-slate-800 hover:bg-slate-800/60'
                }`}
              >
                <span className="flex items-center gap-1.5">
                  <span>{item.icon}</span>
                  <b className="font-medium text-slate-200">{item.label}</b>
                  {active && <span className="ml-auto text-[10px] text-cyan-300">✓ 当前</span>}
                </span>
                <span className="mt-0.5 block text-[10.5px] leading-relaxed text-slate-500">{item.hint}</span>
              </button>
            )
          })}
          <div className="mt-1 px-1 text-[10px] leading-relaxed text-slate-600">
            代价是真实的：完整模式单轮可能触发多次模型调用（规划最多 10 步），延迟与花费都会上去；
            日常问答用轻量，需要"记得住"时用检索。
          </div>
        </div>
      )}
    </div>
  )
}
