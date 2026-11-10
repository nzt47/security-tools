/**
 * AlarmBell —— 顶栏「报警器」（2026-11-11）
 * ================================================================
 * 【它是什么】系统运行期问题的**汇报入口**：未读数 + 最近几条 + 静默/确认 + 直达消息中心。
 * 【它不是什么】不是第四个收件箱：
 *   · 审批收件箱 = 要动作（批/驳，单次消费 + 审计），本组件不碰；
 *   · 消息中心   = 要知悉的历史列表，读的是**同一张事件台账**（/api/alarms）；
 *   · 铃铛       = 那个台账的"即刻视图"，点「查看全部」跳到消息中心，自己不存第二套未读。
 *
 * 【降级口径】
 * 读数失败（后端没起 / 令牌缺失 / 403）**不静默**：徽标不显示假数字，面板里写明
 * 「报警器不可用：<原因>」。也不跳登录页（这两点与 utils/request 的 401 行为刻意不同）。
 *
 * 【轮询】60s（与后端采集线程同频：再快也拿不到新事件）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, BellOff, CheckCheck, Clock, ExternalLink } from 'lucide-react'

import {
  ackAlarm,
  getUnreadAlarms,
  listAlarms,
  markAllAlarmsRead,
  markAlarmRead,
  silenceAlarm,
  type AlarmItem,
} from '@/api/alarms'

/** 轮询间隔（毫秒）：与后端报警采集同一节奏（60s），避免无意义的空转请求 */
export const POLL_INTERVAL_MS = 60_000
/** 面板里最多展示几条（再多就应该去消息中心看） */
export const PANEL_ITEM_LIMIT = 5
/** 「静默 1 小时」的分钟数 */
export const SILENCE_MINUTES = 60

const SEVERITY_DOT: Record<string, string> = {
  critical: 'bg-rose-400',
  warning: 'bg-amber-400',
  info: 'bg-cyan-400',
}

const SEVERITY_TEXT: Record<string, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
}

function errText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

function fmtClock(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export interface AlarmBellProps {
  /** 「查看全部」：由工作台注入（切到消息中心导航项），组件本身不认识路由 */
  onOpenAll?: () => void
}

export default function AlarmBell({ onOpenAll }: AlarmBellProps) {
  const [unread, setUnread] = useState(0)
  const [items, setItems] = useState<AlarmItem[]>([])
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)

  const load = useCallback(async () => {
    try {
      const [countRes, listRes] = await Promise.all([
        getUnreadAlarms(),
        listAlarms({ limit: PANEL_ITEM_LIMIT, unreadOnly: true }),
      ])
      if (!mounted.current) return
      setUnread(Math.max(0, Number(countRes?.unread ?? 0)))
      setItems(Array.isArray(listRes?.items) ? listRes.items : [])
      setError('')
    } catch (reason: unknown) {
      if (!mounted.current) return
      // 失败要上屏（不显示假未读）；保留上一轮 items 会造成"看起来还在"的错觉，故清空
      setUnread(0)
      setItems([])
      setError(errText(reason))
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void load()
    const timer = setInterval(() => { void load() }, POLL_INTERVAL_MS)
    return () => {
      mounted.current = false
      clearInterval(timer)
    }
  }, [load])

  const handleMarkAll = async () => {
    try {
      await markAllAlarmsRead()
      await load()
    } catch (reason: unknown) {
      setError(errText(reason))
    }
  }

  const handleSilence = async (item: AlarmItem) => {
    try {
      await silenceAlarm(item.id, SILENCE_MINUTES)
      await load()
    } catch (reason: unknown) {
      setError(errText(reason))
    }
  }

  const handleAck = async (item: AlarmItem) => {
    try {
      await ackAlarm(item.id)
      await load()
    } catch (reason: unknown) {
      setError(errText(reason))
    }
  }

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (next) void load()
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        data-testid="alarm-bell"
        aria-label={unread > 0 ? '报警器（' + unread + ' 条未读）' : '报警器'}
        title={error ? '报警器不可用：' + error : unread > 0 ? unread + ' 条未读报警' : '报警器：暂无未读'}
        className="relative flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-900/70 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800"
      >
        {error ? <BellOff size={13} className="text-slate-500" /> : <Bell size={13} />}
        <span className="hidden sm:inline">报警</span>
        {unread > 0 && (
          <span
            data-testid="alarm-badge"
            className="ml-0.5 min-w-[16px] rounded-full bg-rose-500 px-1 text-center text-[10px] font-semibold leading-4 text-white"
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          data-testid="alarm-panel"
          className="absolute right-0 z-50 mt-2 w-80 rounded-xl border border-slate-700 bg-slate-900/95 p-3 shadow-xl backdrop-blur"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-200">报警器</span>
            <button
              type="button"
              data-testid="alarm-mark-all"
              onClick={() => void handleMarkAll()}
              disabled={unread === 0}
              className="flex items-center gap-1 rounded-sm border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-300 disabled:opacity-40 hover:bg-slate-800"
            >
              <CheckCheck size={11} /> 全部已读
            </button>
          </div>

          {error && (
            <p data-testid="alarm-error" className="mb-2 rounded-md border border-rose-900/60 bg-rose-950/30 px-2 py-1 text-[10px] text-rose-300">
              报警器不可用：{error}
            </p>
          )}

          {!error && items.length === 0 && (
            <p data-testid="alarm-empty" className="py-4 text-center text-[11px] text-slate-500">
              暂无未读报警（系统运行正常）
            </p>
          )}

          <ul className="max-h-72 space-y-1 overflow-y-auto">
            {items.map((item) => (
              <li
                key={item.id}
                data-testid={'alarm-item-' + item.id}
                className="rounded-md border border-slate-800 bg-slate-900/60 px-2 py-1.5"
              >
                <div className="flex items-start gap-1.5">
                  <span
                    className={'mt-1 h-1.5 w-1.5 shrink-0 rounded-full ' + (SEVERITY_DOT[item.severity] ?? 'bg-slate-400')}
                    aria-label={SEVERITY_TEXT[item.severity] ?? item.severity}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[11px] text-slate-200" title={item.title}>{item.title}</div>
                    {item.detail && (
                      <div className="mt-0.5 line-clamp-2 text-[10px] text-slate-500" title={item.detail}>{item.detail}</div>
                    )}
                    <div className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-500">
                      <span>{fmtClock(item.last_seen)}</span>
                      {item.state === 'resolved' && <span className="text-emerald-400">已恢复</span>}
                      {(item.count ?? 1) > 1 && <span>×{item.count}</span>}
                      <button
                        type="button"
                        onClick={() => void handleSilence(item)}
                        className="ml-auto flex items-center gap-0.5 rounded-sm border border-slate-700 px-1 text-[9px] text-slate-400 hover:bg-slate-800"
                        title={'静默 1 小时（不删除、到期自动恢复提醒）'}
                      >
                        <Clock size={9} /> 静默
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleAck(item)}
                        className="rounded-sm border border-slate-700 px-1 text-[9px] text-slate-400 hover:bg-slate-800"
                        title="确认已知悉（记谁在何时确认）"
                      >
                        确认
                      </button>
                      {!item.read && (
                        <button
                          type="button"
                          onClick={() => void markAlarmRead(item.id).then(load).catch(() => undefined)}
                          className="rounded-sm border border-slate-700 px-1 text-[9px] text-slate-400 hover:bg-slate-800"
                        >
                          已读
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>

          <div className="mt-2 flex items-center justify-between border-t border-slate-800 pt-2">
            <span className="text-[10px] text-slate-500">
              审批单据不在报警器里（去「审批收件箱」裁决）
            </span>
            <button
              type="button"
              data-testid="alarm-open-all"
              onClick={() => { setOpen(false); onOpenAll?.() }}
              className="flex items-center gap-0.5 text-[10px] text-cyan-300 hover:underline"
            >
              查看全部 <ExternalLink size={10} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
