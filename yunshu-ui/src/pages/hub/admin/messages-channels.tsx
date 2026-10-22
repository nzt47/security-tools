/**
 * 消息中心 · 消息通道（内置模板 + 安装 + 调试发送）
 * ------------------------------------------------------------------
 * 【2026-10-18 从「扩展市场」迁入】通道 = **消息发送通道**（webhook / SMTP / Slack /
 * Discord / Telegram），语义属于「消息中心」而不是「扩展市场」，也不属于「网络配置」
 * （后者是代理/网络环境）。原在市场里单独一组，导致"装通道"和"发消息"两件事离得很远。
 *
 * 【2026-10-19 已接线】告警状态变化（触发/恢复）经 `agent/extensions/channel_bridge.py`
 * 发往**已安装且启用**的通道：AlertManager 的状态回调 → 桥接层（摘要脱敏 / 同告警 60s
 * 冷却 / 失败重试 1 次）。开关分两层：总开关 `CP_CHANNEL_EVENTS` + 三个事件细分
 * `CP_CHANNEL_EVENT_ALERTS` / `_TASK_FAILED` / `_TASK_COMPLETED`，都在本页「事件出站」
 * 卡片里就地切换（与「开关中心」同一份登记表，改完热生效）。
 * 注意区分：`POST /api/extensions/channels/send` 仍只供本页「手动发送（调试）」，
 * 产品事件走的是桥接层的进程内调用，不经过这个 HTTP 端点。
 *
 * 【2026-10-20 通道自管】已装通道的**配置 / 启停 / 卸载**从「扩展市场」搬到这里 ——
 * 市场页是跨类型的"已装扩展"列表，而通道的 webhook URL / SMTP 口令只有本页的语境能改对；
 * 等四类页面都补齐这三个动作后，市场页整体下线（下一轮）。
 *
 * 数据源：/api/extensions/discover（内置模板）、/api/extensions/list（已装通道）、
 *         /api/extensions/install {type:'channel'}、/api/extensions/configure（通道配置）、
 *         /api/extensions/toggle（启停）、/api/extensions/uninstall（卸载，force 只在二次确认后传 true）、
 *         /api/extensions/channels/send（仅手动调试）
 */
import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, Download, Loader2, PlugZap, Send, Settings2, SlidersHorizontal, Trash2,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  CP_SETTINGS,
  cpSetting,
  EXTENSIONS_CHANNELS_SEND,
  EXTENSIONS_CONFIGURE,
  EXTENSIONS_DISCOVER,
  EXTENSIONS_INSTALL,
  EXTENSIONS_LIST,
  EXTENSIONS_TOGGLE,
  EXTENSIONS_UNINSTALL,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

interface BuiltinChannel {
  id?: string
  name?: string
  description?: string
  installed?: boolean
  enabled?: boolean
}

interface ExtRow {
  ext_id?: string
  ext_type?: string
  name?: string
  enabled?: boolean
  /** 通道配置（webhook URL / SMTP 口令等）；/api/extensions/list 原样返回 */
  config?: Record<string, unknown>
}

/** 「开关中心」条目里本卡片用到的字段（GET /api/cp/settings 的 items[]） */
interface SettingItem {
  key?: string
  value?: unknown
  display_value?: string
  editable?: boolean
  locked_reason?: string
  needs_restart?: boolean
}

/**
 * 事件出站四个开关（总开关 + 三种事件）。
 *
 * 【为什么卡片放在这里而不是只靠开关中心】通道装在本页、发不发也在本页决定；
 * 让运维去另一个页面找 500 条里的 4 条，等于把"能关"变成"理论上能关"。
 * 开关中心仍然是同一份登记表（同一 POST 端点），这里只是**就地**入口。
 */
const EVENT_ROWS: { key: string; label: string; hint: string }[] = [
  { key: 'CP_CHANNEL_EVENTS', label: '总开关', hint: '关掉后告警与任务事件全部不出站（手动发送不受影响）' },
  { key: 'CP_CHANNEL_EVENT_ALERTS', label: '监控告警 · 触发/恢复', hint: 'AlertManager 的状态变化' },
  { key: 'CP_CHANNEL_EVENT_TASK_FAILED', label: '定时任务失败', hint: '任务失败摘要（不带 output）' },
  { key: 'CP_CHANNEL_EVENT_TASK_COMPLETED', label: '定时任务完成', hint: '任务自身 notify_on_complete 仍是一道前置门' },
]

export default function HubChannelsPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [builtin, setBuiltin] = useState<BuiltinChannel[]>([])
  const [installed, setInstalled] = useState<ExtRow[]>([])
  const [channelId, setChannelId] = useState('')
  const [channelMsg, setChannelMsg] = useState('')
  // 事件出站开关（读开关中心同一份登记表；失败单独提示，不拖垮通道列表）
  const [eventItems, setEventItems] = useState<Record<string, SettingItem>>({})
  const [eventErr, setEventErr] = useState('')
  // 已装通道的三个自管动作（原「扩展市场」的 configure / toggle / uninstall）
  const [confirmUninstall, setConfirmUninstall] = useState('')
  const [configTarget, setConfigTarget] = useState<ExtRow | null>(null)
  const [configText, setConfigText] = useState('')

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [d, l] = await Promise.all([
        hubGet<{ ok?: boolean; error?: string; builtin_channels?: BuiltinChannel[] }>(EXTENSIONS_DISCOVER),
        hubGet<{ ok?: boolean; error?: string; extensions?: ExtRow[] }>(EXTENSIONS_LIST),
      ])
      if (d?.ok === false) { setErr('读取内置通道失败：' + (d.error || '后端返回 ok=false')); return }
      if (l?.ok === false) { setErr('读取已装通道失败：' + (l.error || '后端返回 ok=false')); return }
      setBuiltin(d?.builtin_channels ?? [])
      setInstalled((l?.extensions ?? []).filter((e) => e.ext_type === 'channel'))
    } catch (e) {
      setErr('读取通道失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }, [])

  const loadEventSettings = useCallback(async () => {
    setEventErr('')
    try {
      const r = await hubGet<{ ok?: boolean; items?: SettingItem[] }>(CP_SETTINGS)
      if (r?.ok === false) { setEventErr('读取事件开关失败：后端返回 ok=false'); return }
      const map: Record<string, SettingItem> = {}
      for (const it of r?.items ?? []) {
        if (it?.key) map[it.key] = it
      }
      setEventItems(map)
    } catch (e) {
      setEventErr('读取事件开关失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }, [])

  useEffect(() => { void load(); void loadEventSettings() }, [load, loadEventSettings])

  /** 启停一条已装通道（发往哪些通道由 enabled 决定；禁用后事件不再发给它） */
  const toggleInstalled = async (row: ExtRow) => {
    const id = row.ext_id ?? ''
    setBusy('toggle:' + id); setErr(''); setMsg('')
    try {
      const next = !row.enabled
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_TOGGLE, {
        type: 'channel', id, enabled: next,
      })
      if (r?.ok === false) { setErr('切换失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg((next ? '已启用 ' : '已禁用 ') + id)
      await load()
    } catch (ev) { setErr('切换失败：' + (ev instanceof Error ? ev.message : String(ev))) } finally { setBusy('') }
  }

  /** 卸载一条已装通道（两步确认；force=true 只在第二次点击时传） */
  const uninstallInstalled = async (row: ExtRow) => {
    const id = row.ext_id ?? ''
    setBusy('uninstall:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_UNINSTALL, {
        type: 'channel', id, force: true,
      })
      if (r?.ok === false) { setErr('卸载失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg('已卸载 ' + id)
      setConfirmUninstall('')
      await load()
    } catch (ev) { setErr('卸载失败：' + (ev instanceof Error ? ev.message : String(ev))) } finally { setBusy('') }
  }

  /** 打开通道配置（JSON 文本编辑；坏 JSON 不发请求） */
  const openConfig = (row: ExtRow) => {
    setConfigTarget(row)
    setConfigText(JSON.stringify(row.config ?? {}, null, 2))
  }

  const saveConfig = async () => {
    if (!configTarget) return
    let parsed: unknown
    try {
      parsed = JSON.parse(configText || '{}')
    } catch {
      setErr('配置不是合法 JSON —— 没有提交')
      return
    }
    setBusy('configure'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_CONFIGURE, {
        type: 'channel', id: configTarget.ext_id, config: parsed,
      })
      if (r?.ok === false) { setErr('保存配置失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg('配置已保存：' + (configTarget.ext_id ?? ''))
      setConfigTarget(null)
      await load()
    } catch (ev) { setErr('保存配置失败：' + (ev instanceof Error ? ev.message : String(ev))) } finally { setBusy('') }
  }

  /** 切换一个事件开关（A 级设置 ⇒ 服务端落到进程环境变量，热生效，无需重启） */
  const toggleEvent = async (key: string, next: boolean) => {
    setBusy('event:' + key); setEventErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; message?: string; code?: string }>(cpSetting(key), { value: next })
      if (r?.ok === false) {
        setEventErr('切换失败：' + (r.message || r.code || '后端返回 ok=false'))
        return
      }
      setMsg((next ? '已开启 ' : '已关闭 ') + key + '（热生效）')
      await loadEventSettings()
    } catch (e) {
      setEventErr('切换失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const install = async (id: string) => {
    setBusy('install:' + id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; message?: string }>(EXTENSIONS_INSTALL, { type: 'channel', source: id })
      if (r?.ok === false) { setErr('安装失败：' + (r.message || '后端返回 ok=false')); return }
      setMsg('已安装通道 ' + id)
      await load()
    } catch (e) {
      setErr('安装失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const send = async () => {
    setBusy('send'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_CHANNELS_SEND, {
        channel_id: channelId.trim(),
        message: channelMsg.trim(),
      })
      if (r?.ok === false) { setErr('发送失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg('已发送' + (r?.message ? '：' + r.message : ''))
      setChannelMsg('')
    } catch (e) {
      setErr('发送失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="mt-4 flex flex-col gap-3">
      {err && <ErrorBox message={err} />}
      {msg && (
        <div className="flex items-center gap-1.5 rounded-md border border-emerald-800/60 bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300">
          <CheckCircle2 size={12} /> {msg}
        </div>
      )}

      {/* 接线状态：贴在组标题旁，不折叠、不灰掉 —— 用户必须一眼看到"现在会发什么" */}
      <div className="flex items-start gap-2 rounded-md border border-cyan-800/60 bg-cyan-500/10 px-2.5 py-1.5">
        <PlugZap size={12} className="mt-0.5 shrink-0 text-cyan-400" />
        <div className="text-[11px] text-cyan-100/90">
          已接线：<span className="text-cyan-300">监控告警（触发 / 恢复）</span> 会经**已启用**的通道自动发送摘要
          （只带告警名/级别/指标与阈值/白名单标签；同一告警 60s 冷却；失败重试 1 次）。
          <code className="font-mono">POST /api/extensions/channels/send</code> 仍只供本页「手动发送（调试）」，
          产品事件走进程内桥接、不经过该 HTTP 端点。
        </div>
      </div>

      {/* 事件出站开关：与「开关中心」同一份登记表、同一个 POST 端点，只是就地给入口 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <SlidersHorizontal size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">事件出站（总开关 + 3 类事件）</h3>
          <span className="text-[10px] text-slate-500">改完热生效（不用重启）；数据源与「开关中心」同一份登记表</span>
        </div>
        {eventErr && <div className="mb-1 text-[10px] text-red-300">{eventErr}</div>}
        <div className="flex flex-col gap-1">
          {EVENT_ROWS.map((row) => {
            const it = eventItems[row.key]
            const on = it ? Boolean(it.value) : true
            const master = eventItems['CP_CHANNEL_EVENTS']
            const masterOff = row.key !== 'CP_CHANNEL_EVENTS' && !!master && !master.value
            return (
              <div key={row.key} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <label className="flex items-center gap-1.5 text-slate-300">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={loading || !it || it.editable === false}
                    onChange={() => void toggleEvent(row.key, !on)}
                    aria-label={'事件出站 ' + row.key}
                  />
                  {row.label}
                </label>
                <span className={'text-[10px] ' + (on ? 'text-emerald-400' : 'text-slate-500')}>
                  {on ? '出站' : '不出站'}
                </span>
                <span className="truncate text-[10px] text-slate-500">{row.hint}</span>
                <span className="ml-auto flex items-center gap-2">
                  {masterOff && <span className="text-[10px] text-amber-300">总开关已关</span>}
                  {it?.needs_restart && <span className="text-[10px] text-amber-300">需重启</span>}
                  {it && it.editable === false && (
                    <span className="text-[10px] text-slate-500" title={it.locked_reason}>
                      {it.locked_reason || '不可改'}
                    </span>
                  )}
                  {!it && <span className="text-[10px] text-slate-600">未取到</span>}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-xs font-semibold text-slate-200">内置通道模板（{builtin.length}）</h3>
          <span className="text-[10px] text-slate-500">安装后可在此页调试发送；通道 id 见左侧</span>
          <button
            type="button"
            className={BTN + ' ml-auto'}
            onClick={() => void load()}
            disabled={loading}
            aria-label="刷新通道"
          >
            {busy === 'load' ? <Loader2 size={11} className="animate-spin" /> : null} 刷新
          </button>
        </div>
        {builtin.length === 0 && busy !== 'load' && (
          <div className="text-[11px] text-slate-500">没有可安装的内置通道模板。</div>
        )}
        <div className="flex flex-col gap-1">
          {builtin.map((b) => {
            const id = b.id ?? ''
            return (
              <div key={'channel-' + id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                <span className="text-slate-300">{b.name ?? ''}</span>
                <span className="truncate text-[10px] text-slate-500">{b.description ?? ''}</span>
                <span className="ml-auto flex items-center gap-1.5">
                  {b.enabled
                    ? <span className="text-[10px] text-emerald-400">已启用</span>
                    : b.installed
                      ? <span className="text-[10px] text-slate-400">已装（未启用）</span>
                      : (
                        <button
                          type="button"
                          className={BTN}
                          onClick={() => void install(id)}
                          disabled={loading}
                          aria-label={'安装通道 ' + id}
                        >
                          {busy === 'install:' + id ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 安装
                        </button>
                      )}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Settings2 size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">已装通道（{installed.length}）</h3>
          <span className="text-[10px] text-slate-500">配置 / 启停 / 卸载 都在这里（原「扩展市场」的三个动作已搬来）</span>
        </div>
        {installed.length === 0 && <div className="text-[11px] text-slate-500">还没有已装通道。</div>}
        <div className="flex flex-col gap-1">
          {installed.map((row) => {
            const id = row.ext_id ?? ''
            const isConfirm = confirmUninstall === id
            const editing = configTarget?.ext_id === id
            return (
              <div key={'installed-' + id} className="flex flex-col gap-1 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                  <span className="text-slate-300">{row.name ?? ''}</span>
                  <span className={'text-[10px] ' + (row.enabled ? 'text-emerald-400' : 'text-slate-500')}>
                    {row.enabled ? '启用' : '停用'}
                  </span>
                  <span className="ml-auto flex items-center gap-1.5">
                    <button
                      type="button"
                      className={BTN}
                      onClick={() => (editing ? setConfigTarget(null) : openConfig(row))}
                      disabled={loading}
                      aria-label={'配置通道 ' + id}
                    >
                      <Settings2 size={11} /> {editing ? '收起配置' : '配置'}
                    </button>
                    <button type="button" className={BTN} onClick={() => void toggleInstalled(row)} disabled={loading} aria-label={'切换通道 ' + id}>
                      {busy === 'toggle:' + id ? <Loader2 size={11} className="animate-spin" /> : null} {row.enabled ? '停用' : '启用'}
                    </button>
                    <button type="button" className={BTN} onClick={() => setConfirmUninstall(isConfirm ? '' : id)} disabled={loading} aria-label={'卸载通道 ' + id}>
                      <Trash2 size={11} /> 卸载
                    </button>
                  </span>
                </div>
                {isConfirm && (
                  <div className="flex flex-wrap items-center gap-2 rounded-sm border border-red-900/50 bg-red-500/5 px-2 py-1">
                    <span className="text-[10px] text-red-300">
                      卸载对「文件轨独占」的扩展是不可逆删除（force=true 才允许）。确定？
                    </span>
                    <button
                      type="button"
                      className={BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'}
                      onClick={() => void uninstallInstalled(row)}
                      disabled={loading}
                      aria-label={'确认卸载通道 ' + id}
                    >
                      {busy === 'uninstall:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认卸载
                    </button>
                    <button type="button" className={BTN} onClick={() => setConfirmUninstall('')}>取消</button>
                  </div>
                )}
                {editing && (
                  <div className="flex flex-col gap-1">
                    <textarea
                      className={INPUT + ' h-40 font-mono text-[11px]'}
                      value={configText}
                      onChange={(ev) => setConfigText(ev.target.value)}
                      aria-label={'通道配置 ' + id}
                    />
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button type="button" className={BTN_EM} onClick={() => void saveConfig()} disabled={loading} aria-label={'保存通道配置 ' + id}>
                        {busy === 'configure' ? <Loader2 size={11} className="animate-spin" /> : null} 保存配置
                      </button>
                      <button type="button" className={BTN} onClick={() => setConfigTarget(null)} disabled={loading}>取消</button>
                      <span className="text-[10px] text-slate-500">webhook URL / SMTP 口令等都在这里；坏 JSON 不会提交</span>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2">
          <Send size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">手动发送（调试）</h3>
          <span className="text-[10px] text-slate-500">需要已安装并启用的通道</span>
        </div>
        <div className="mb-2 text-[10px] text-slate-500">
          已装通道 {installed.filter((c) => c.enabled).length} 条启用（配置 / 启停 / 卸载见上方「已装通道」卡片）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input className={INPUT + ' w-52'} placeholder="channel_id" value={channelId} onChange={(e) => setChannelId(e.target.value)} aria-label="通道 id" />
          <input className={INPUT + ' w-72'} placeholder="消息内容" value={channelMsg} onChange={(e) => setChannelMsg(e.target.value)} aria-label="通道消息内容" />
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void send()}
            disabled={loading || channelId.trim() === '' || channelMsg.trim() === ''}
            aria-label="发送通道消息"
          >
            {busy === 'send' ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />} 发送
          </button>
        </div>
      </div>
    </div>
  )
}
