/**
 * 压缩复用对话前缀 —— 就地开关（与「手动压缩」同一行）
 * ------------------------------------------------------------------
 * 【为什么放这里】这个开关决定了「压缩到底花多少钱」：开着，压缩请求与本轮对话逐字同前缀，
 *   实测命中 99.7%、单次压缩成本 −81%；关着是旧行为（独立前缀、命中 0、每次≈一次普通对话全价）。
 *   让它和「手动压缩」按钮挨着，是让「按一下压缩」与「这次压缩怎么算钱」在同一处可见 ——
 *   放到开关中心 570 条里，等于把「能开」变成「理论上能开」。
 *
 * 【数据源】`GET /api/cp/settings` 同一份登记表（key=CP_COMPRESS_REUSE_CHAT_PREFIX），
 *   写走 `POST /api/cp/settings/<key> {value}`；A 级、hot ⇒ 改完下一次读取即生效（不用重启）。
 *   与开关中心是同一份口径，本组件只是**就地入口**，不另造开关、不缓存状态。
 *
 * 【说明为什么常驻可展开】三句话讲不清「什么时候不省钱」：冷缓存会贵 1.85 倍、
 *   模型不一致、装不下窗口 —— 这些边界写在说明里，读的人才知道什么时候该关。
 */
import { useCallback, useEffect, useState } from 'react'
import { HelpCircle, Loader2 } from 'lucide-react'

import { CP_SETTINGS, cpSetting } from '@/api/endpoints'
import { request } from '@/lib/apiClient'

//: 与 agent/settings/registry.py 的登记名逐字一致（改这里=改口径，先改注册表）
export const KEY = 'CP_COMPRESS_REUSE_CHAT_PREFIX'

interface SettingItem {
  key?: string
  value?: unknown
  editable?: boolean
  locked_reason?: string
  needs_restart?: boolean
  effect_label?: string
  source_label?: string
}

interface ChangeReceipt {
  ok?: boolean
  message?: string
  applied?: boolean
  receipt?: { detail?: string }
}

export function CompressPrefixSwitch() {
  const [item, setItem] = useState<SettingItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [open, setOpen] = useState(false)

  const on = item?.value === true
  const editable = item ? item.editable !== false : false

  const load = useCallback(async () => {
    try {
      const r = await request<{ ok?: boolean; items?: SettingItem[] }>(CP_SETTINGS)
      const found = (r?.items ?? []).find((it) => it?.key === KEY) ?? null
      setItem(found)
      if (!found) setErr('开关中心未登记该键（注册表缺项）')
      else setErr('')
    } catch (e) {
      setErr('读取开关失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const toggle = async () => {
    if (!item || !editable || busy) return
    const next = !on
    setBusy(true); setErr(''); setNote('')
    try {
      const r = await request<ChangeReceipt>(cpSetting(KEY), {
        method: 'POST',
        body: { value: next, reason: '上下文面板：压缩复用对话前缀' },
      })
      if (r?.ok === false) {
        setErr('切换失败：' + (r.message || '后端返回 ok=false'))
        return
      }
      setItem({ ...item, value: next })
      setNote(next
        ? '已开启：下一次压缩将与本轮对话同前缀（实测命中 99.7%）'
        : '已关闭：回到逐字旧行为（独立前缀、全价）')
    } catch (e) {
      setErr('切换失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-1" data-testid="compress-prefix-switch">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="压缩复用对话前缀"
          onClick={() => { void toggle() }}
          disabled={busy || !editable}
          className={`relative h-4 w-8 shrink-0 rounded-full transition-colors ${on ? 'bg-emerald-500' : 'bg-slate-700'} ${busy || !editable ? 'opacity-40' : 'hover:opacity-90'}`}
        >
          <span
            className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all ${on ? 'left-4' : 'left-0.5'}`}
          />
        </button>
        <span className="text-[11px] text-slate-300">压缩复用对话前缀</span>
        {busy && <Loader2 size={11} className="animate-spin text-slate-500" />}
        <button
          type="button"
          aria-label="压缩复用说明"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="text-slate-500 hover:text-slate-300"
        >
          <HelpCircle size={11} />
        </button>
      </div>

      {open && (
        <div className="max-w-[560px] space-y-1 rounded-md border border-slate-800 bg-slate-950/70 px-2.5 py-2 text-[10.5px] leading-relaxed text-slate-300">
          <div className="text-cyan-300">压缩（摘要）调用是否复用本轮对话的前缀缓存</div>
          <div>
            <b className="text-slate-100">开</b>：压缩请求与本轮对话<b className="text-slate-100">逐字同前缀</b>
            （同 system + 同 tools + 同消息窗口），摘要指令放末尾 —— 实测命中
            <b className="text-emerald-300"> 99.7%</b>（87,552 / 87,795），单次压缩成本
            <b className="text-emerald-300"> −81%</b>，且摘要从「对话续写」变成结构化摘要。
          </div>
          <div>
            <b className="text-slate-100">关（默认）</b>：逐字旧行为 —— 压缩是独立前缀，命中
            <b className="text-rose-300"> 0 / 57,385</b>，每次压缩≈一次普通对话的全价。
          </div>
          <div>
            <b className="text-slate-100">自动回落</b>（不用手管）：前缀超过 30 分钟（冷缓存全价是旧路径的
            <b> 1.85 倍</b>）、压缩侧模型与会话不一致（绑定了分身）、或前缀+输出装不下窗口 ⇒ 自动走旧路径。
          </div>
          <div className="text-slate-400">
            代价：改身份提示词 / 换工具集会让下一次前缀失效一次（固有，改回时旧前缀若仍在服务端缓存则不必再付）。
            生效方式：热生效，下一次压缩即按新口径；回滚 = 关掉。
          </div>
          {item && (
            <div className="text-[10px] text-slate-500">
              当前：{on ? '开' : '关'} · 来源 {item.source_label || valueOrDash(item.value)}
              {item.effect_label ? ' · ' + item.effect_label : ''}
              {item.needs_restart ? ' · 需重启' : ''}
              {item.editable === false && item.locked_reason ? ' · ' + item.locked_reason : ''}
            </div>
          )}
        </div>
      )}

      {note && <div className="text-[10px] text-emerald-300">{note}</div>}
      {err && <div className="text-[10px] text-rose-300">{err}</div>}
    </div>
  )
}

function valueOrDash(v: unknown): string {
  if (v === true) return '开'
  if (v === false) return '关'
  return '—'
}

export default CompressPrefixSwitch
