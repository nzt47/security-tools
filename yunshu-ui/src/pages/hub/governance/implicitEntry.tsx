/**
 * 隐式入口的可点击记录（v7.2 §7 UI 五坑③：**别藏入口**）
 * ------------------------------------------------------------------
 * 云枢里存在若干"隐式入口"——不在主导航里，靠右键菜单或快捷键触发
 * （例如右鍵"沉淀为 Skill"、`/ingest`、状态栏灯）。
 *
 * 五坑③的要求：**隐式入口必留可点击记录**。也就是：
 *   1. 每次通过隐式入口发起动作，都写一条**可见、可点击**的记录；
 *   2. 记录里带够复现信息（入口、目标、时间、结果、是否成功）；
 *   3. 用户能从记录点回去（`onReplay`）——记录不是日志，是入口的替身。
 *
 * 本模块把这条纪律做成一个可复用的小组件 + 一个进程内账本；
 * 刻意**不落后端**（这些是前端交互轨迹，不是审计证据链——审计仍由后端负责）。
 */

import { useCallback, useEffect, useState } from 'react'
import { Link2, MousePointerClick } from 'lucide-react'
import { formatTime } from './usePanel'

/** 一条隐式入口记录 */
export interface ImplicitEntryRecord {
  id: string
  /** 入口标识（如 `context-menu:distill-as-skill` / `hotkey:Cmd+K` / `status-light`） */
  entry: string
  /** 人读入口名（如"右键 → 沉淀为 Skill"） */
  entryLabel: string
  /** 目标对象 */
  target: string
  /** 触发时间（epoch ms） */
  at: number
  /** 结果（成功/失败/已取消） */
  outcome: 'ok' | 'error' | 'cancelled'
  /** 结果说明 */
  detail?: string
}

type Listener = (rows: ImplicitEntryRecord[]) => void

const MAX_ROWS = 50
let _rows: ImplicitEntryRecord[] = []
const _listeners = new Set<Listener>()

function _emit() {
  _listeners.forEach((fn) => fn(_rows))
}

/** 记录一次隐式入口触发（**所有隐式入口都必须调它**） */
export function recordImplicitEntry(
  input: Omit<ImplicitEntryRecord, 'id' | 'at'>,
): ImplicitEntryRecord {
  const row: ImplicitEntryRecord = {
    ...input,
    id: `imp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    at: Date.now(),
  }
  _rows = [row, ..._rows].slice(0, MAX_ROWS)
  _emit()
  return row
}

/** 清空（测试隔离用） */
export function resetImplicitEntries(): void {
  _rows = []
  _emit()
}

/** 读取快照 */
export function implicitEntries(): ImplicitEntryRecord[] {
  return _rows
}

/** 订阅账本 */
export function useImplicitEntryLog(): {
  rows: ImplicitEntryRecord[]
  clear: () => void
} {
  const [rows, setRows] = useState<ImplicitEntryRecord[]>(_rows)
  useEffect(() => {
    const fn: Listener = (next) => setRows([...next])
    _listeners.add(fn)
    setRows([..._rows])
    return () => {
      _listeners.delete(fn)
    }
  }, [])
  const clear = useCallback(() => resetImplicitEntries(), [])
  return { rows, clear }
}

/**
 * 隐式入口记录面板（可点击 = 可回到入口）
 *
 * ★ 验收项：隐式入口（右键"沉淀为 Skill"等）**保留可点击记录**——
 *   本组件即该记录的落点；`onReplay` 让它"点得回去"。
 */
export function ImplicitEntryLog({
  onReplay,
  title = '隐式入口记录',
}: {
  onReplay?: (row: ImplicitEntryRecord) => void
  title?: string
}) {
  const { rows, clear } = useImplicitEntryLog()
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-300">
        <MousePointerClick size={13} />
        <span className="font-medium">{title}</span>
        <span className="text-slate-500">
          （§7 UI 五坑③：隐式入口必留**可点击**记录）
        </span>
        <button
          type="button"
          onClick={clear}
          className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-400 hover:text-cyan-300"
        >
          清空
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="flex items-center gap-2 py-3 text-[11px] text-slate-500">
          <Link2 size={12} /> 暂无隐式入口记录
        </div>
      ) : (
        <ul className="space-y-1">
          {rows.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onReplay?.(r)}
                disabled={!onReplay}
                data-cp-implicit-entry={r.entry}
                className="flex w-full items-center gap-2 rounded border border-slate-800 bg-slate-950/50 px-2 py-1 text-left text-[11px] disabled:cursor-default hover:border-cyan-800"
              >
                <span
                  className={
                    r.outcome === 'ok'
                      ? 'h-1.5 w-1.5 rounded-full bg-emerald-400'
                      : r.outcome === 'error'
                        ? 'h-1.5 w-1.5 rounded-full bg-red-400'
                        : 'h-1.5 w-1.5 rounded-full bg-slate-500'
                  }
                />
                <span className="shrink-0 text-slate-300">{r.entryLabel}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-cyan-300">
                  {r.target}
                </span>
                {r.detail && (
                  <span className="shrink-0 truncate text-slate-500" style={{ maxWidth: 180 }}>
                    {r.detail}
                  </span>
                )}
                <span className="shrink-0 text-[10px] text-slate-600">{formatTime(r.at)}</span>
                {onReplay && <span className="shrink-0 text-[10px] text-slate-500">点回入口 ›</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
