/**
 * ChatModeMenu —— 「对话模式」下拉（放在工具栏「输出格式」右侧）
 * ------------------------------------------------------------------
 * 每档对应一种**行为与成本的取舍**（选项与说明的唯一来源：useChatPrefsStore 的 CHAT_MODES）：
 *   🤖 自动   = 按输入选路（本地直答 / 轻量 / 完整·流式），永不自动选"完整"；选中哪档会外发事件
 *   ⚡ 轻量   = 只用本会话历史（默认；真流式、1 次模型调用；纯问候/道谢本地直答、0 次调用）
 *   🔎 检索   = 轻量之上注入检索到的记忆/知识片段（+≤3k token/轮，仍是真流式；
 *               纯问候/道谢同样本地直答，不会先跑检索）
 *   🧠 完整   = 走编排器全链路（意图分层 + 检索 + 规划 + 工具）—— 最准，但**非流式**且最贵
 *
 * 为什么做成用户自己选：这三者是**取舍**而不是"越好越好"——完整模式单轮可能多次模型调用、
 * 延迟从数秒升到数十秒。把选择权交给用户，并在选项说明里直说代价。
 * 选择写入 useChatPrefsStore（LocalStorage 持久化），随每次请求体 `mode` 发给后端。
 */
import { ChevronDown } from 'lucide-react'
import { Button, MenuPanel, RowButton, useMenu } from '@/components/ui'
import { CHAT_MODES, useChatPrefsStore, type ChatMode } from '../../../stores/useChatPrefsStore'

export function ChatModeMenu() {
  const mode = useChatPrefsStore((s) => s.mode)
  const setMode = useChatPrefsStore((s) => s.setMode)
  // 【2026-11-18 封装收口】外点关闭 / Esc（捕获）/ portal 定位都交给 ui/Menu
  const menu = useMenu<HTMLButtonElement>()

  const current = CHAT_MODES[mode]

  return (
    <div className="relative">
      <Button
        ref={menu.anchorRef}
        size="xs"
        variant="default"
        active={menu.open}
        onClick={menu.toggle}
        aria-expanded={menu.open}
        aria-haspopup="menu"
        title={`对话模式：${current.label} —— ${current.hint}`}
        // 「完整」是重档，用醒目色提醒（这是模式语义，不是展开态）：与 active 的展开态并存，
        // className 在后面 ⇒ 冲突时以模式色为准。
        className={
          mode === 'full'
            ? 'border-violet-600/60 text-violet-300'
            : mode === 'retrieval'
              ? 'border-cyan-600/60 text-cyan-300'
              : undefined
        }
      >
        <span>{current.icon}</span>
        <span>模式：{current.label}</span>
        <ChevronDown size={10} className={menu.open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </Button>

      {menu.open && (
        <MenuPanel anchorRef={menu.anchorRef} onClose={menu.close} width={330} label="对话模式">

          <div className="mb-1.5 px-1 text-micro uppercase tracking-wider text-slate-500">对话模式</div>
          {(Object.keys(CHAT_MODES) as ChatMode[]).map((m) => {
            const item = CHAT_MODES[m]
            const active = m === mode
            return (
              <RowButton
                key={m}
                role="menuitemradio"
                aria-checked={active}
                selected={active}
                onClick={() => {
                  setMode(m)
                  menu.close()
                }}
                className="mb-1 rounded-lg border border-slate-800 px-2 py-1.5"
              >
                <span className="flex items-center gap-1.5">
                  <span>{item.icon}</span>
                  <b className="font-medium text-slate-200">{item.label}</b>
                  {active && <span className="ml-auto text-micro text-cyan-300">✓ 当前</span>}
                </span>
                <span className="mt-0.5 block text-caption leading-relaxed text-slate-500">{item.hint}</span>
              </RowButton>
            )
          })}
          <div className="mt-1 px-1 text-micro leading-relaxed text-slate-600">
            代价是真实的：完整模式单轮可能触发多次模型调用（规划最多 10 步），延迟与花费都会上去；
            日常问答用轻量，需要"记得住"时用检索；不想每次挑就用「自动」（它只会在任务型/长输入时升档）。
          </div>
        </MenuPanel>
      )}
    </div>
  )
}