/**
 * PermissionModeMenu —— 「权限档位」下拉（放在工具栏「对话模式」右侧）
 * ------------------------------------------------------------------
 * 对齐 DSH 的 sandbox/permission modes，四档（选项与说明的唯一来源：useChatPrefsStore 的
 * PERMISSION_MODES）：
 *   🔒 仅可查看    = 只放行只读能力（写盘/执行命令一律拒绝）
 *   📁 工作区修改  = 读写工作区免审批；执行命令仍逐次确认
 *   🤖 自动审查    = 默认：按 L0–L3 分级逐条判定
 *   ⚡ 完全权限    = 跳过全部审批（黑名单与受限会话判定仍生效）
 *
 * 为什么要给用户切：云枢默认对写盘/执行逐条审批，长任务会被"每步都要点确认"打断。
 * 把授权姿态交回用户，档位随每次请求体 permission_mode 发给后端，工具闸门即时生效
 * （见 agent/tool_gate.py 的 _permission_mode）。
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import {
  PERMISSION_MODES,
  useChatPrefsStore,
  type PermissionMode,
} from '../../../stores/useChatPrefsStore'

const TONE: Record<PermissionMode, string> = {
  read_only: 'border-emerald-600/60 text-emerald-300',
  workspace: 'border-cyan-600/60 text-cyan-300',
  auto: 'border-slate-700 text-slate-400 hover:bg-slate-800',
  full: 'border-amber-600/70 text-amber-300',
}

export function PermissionModeMenu() {
  const permissionMode = useChatPrefsStore((s) => s.permissionMode)
  const setPermissionMode = useChatPrefsStore((s) => s.setPermissionMode)
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

  const current = PERMISSION_MODES[permissionMode]

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        data-testid="permission-mode-menu"
        title={`权限档位：${current.label} —— ${current.hint}`}
        className={`flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors ${TONE[permissionMode]}`}
      >
        <span>{current.icon}</span>
        <span>权限：{current.label}</span>
        <ChevronDown size={10} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-[80] w-[340px] rounded-xl border border-slate-700 bg-slate-900/98 p-2 text-[11.5px] shadow-2xl backdrop-blur"
        >
          <div className="mb-1.5 px-1 text-[10px] uppercase tracking-wider text-slate-500">权限档位</div>
          {(Object.keys(PERMISSION_MODES) as PermissionMode[]).map((m) => {
            const item = PERMISSION_MODES[m]
            const active = m === permissionMode
            return (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                data-testid={`permission-mode-${m}`}
                onClick={() => {
                  setPermissionMode(m)
                  setOpen(false)
                }}
                className={`mb-1 w-full rounded-lg border px-2 py-1.5 text-left transition-colors ${
                  active ? 'border-cyan-600/60 bg-cyan-500/10' : 'border-slate-800 hover:bg-slate-800/60'
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
            档位随每条消息发给后端、即时生效；它只决定"要不要逐条审批"，不改任何工具能力。
            完全权限会跳过 L2/L3 审批，请只在可信环境使用。
          </div>
        </div>
      )}
    </div>
  )
}
