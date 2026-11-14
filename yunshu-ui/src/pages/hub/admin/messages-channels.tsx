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
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircle2, Copy, Download, Loader2, PlugZap, Plus, Send, Settings2, SlidersHorizontal, Trash2,
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
/** 「下一步该点的那个」按钮（安装 / 启用）：整段独立写，不拼 BTN ——
 *  否则 border-slate-700 与 border-emerald-600 同时在 class 里，谁生效取决于样式表顺序，
 *  看起来就会"有时候高亮有时候不高亮"（2026-11-13 Owner：启用按钮应高亮）。 */
const BTN_PRIMARY = 'inline-flex items-center gap-1 rounded-md border border-emerald-600/70 bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-50'
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

/** 后端解析视图：一个字段的"值 + 来源"（/api/extensions/discover 的 resolved_channels） */
interface ResolvedField {
  field: string
  env_key: string
  secret?: boolean
  local?: string
  local_configured?: boolean
  effective?: string
  source?: string
  source_label?: string
  configured?: boolean
}

/**
 * 一条通道的解析视图（2026-11-14 报警通道写 .env）
 * 后端是唯一真相：字段→环境键的映射、来源、空态范例与说明都由它给，前端不再抄一份。
 */
interface ResolvedChannel {
  channel_id: string
  channel_type: string
  primary: boolean
  env_keys?: string[]
  fields?: ResolvedField[]
  extra?: Record<string, unknown>
  example?: Record<string, unknown>
  notes?: string[]
}

/** 字段的中文标签（纯展示；映射关系不在这里） */
const FIELD_LABELS: Record<string, string> = {
  smtp_server: '服务器地址',
  smtp_port: '端口',
  smtp_username: '用户名',
  smtp_password: '口令（授权码）',
  from_address: '发件人',
  default_to: '收件人（逗号分隔）',
  webhook_url: 'Webhook 地址',
}

/** 来源徽标配色：本地 > .env 专用键 > .env 旧键 > 默认 */
const SOURCE_BADGE: Record<string, string> = {
  channel: 'text-cyan-300',
  env: 'text-emerald-300',
  legacy_env: 'text-amber-300',
  default: 'text-slate-500',
}

/** 合并视图的一行：内置模板 ∪ 已装通道（装了的带 installed 行） */
interface ChannelRow {
  id: string
  name: string
  description: string
  installed?: ExtRow
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
  // 逐行测试发送（2026-11-13）：不再有单独的通道选择器 —— 发送框挂在通道那一行，
  // 通道 id 由行决定（彻底消掉"填 1 被当成通道 id"的歧义），且只有启用时才能输入。
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [sendBusyId, setSendBusyId] = useState('')
  // 事件出站开关（读开关中心同一份登记表；失败单独提示，不拖垮通道列表）
  const [eventItems, setEventItems] = useState<Record<string, SettingItem>>({})
  const [eventErr, setEventErr] = useState('')
  // 已装通道的三个自管动作（原「扩展市场」的 configure / toggle / uninstall）
  const [confirmUninstall, setConfirmUninstall] = useState('')
  const [configTarget, setConfigTarget] = useState<ExtRow | null>(null)
  const [configText, setConfigText] = useState('')
  // 【2026-11-14 报警通道写 .env】后端解析视图（逐字段值/来源）+ 结构化表单草稿
  const [views, setViews] = useState<Record<string, ResolvedChannel>>({})
  const [form, setForm] = useState<Record<string, string>>({})
  // 口令的"清除"是显式动作（留空 = 不修改）；单独记，避免和"没填"混在一起
  const [clearedSecrets, setClearedSecrets] = useState<Record<string, boolean>>({})
  const [copiedId, setCopiedId] = useState('')
  // 多通道：同类型可加多条；主通道（email_smtp / webhook）之外只存本机
  const [newType, setNewType] = useState('smtp')
  const [newId, setNewId] = useState('')
  const [newName, setNewName] = useState('')

  const load = useCallback(async () => {
    setBusy('load'); setErr('')
    try {
      const [d, l] = await Promise.all([
        hubGet<{ ok?: boolean; error?: string; builtin_channels?: BuiltinChannel[]; resolved_channels?: ResolvedChannel[] }>(EXTENSIONS_DISCOVER),
        hubGet<{ ok?: boolean; error?: string; extensions?: ExtRow[] }>(EXTENSIONS_LIST),
      ])
      if (d?.ok === false) { setErr('读取内置通道失败：' + (d.error || '后端返回 ok=false')); return }
      if (l?.ok === false) { setErr('读取已装通道失败：' + (l.error || '后端返回 ok=false')); return }
      setBuiltin(d?.builtin_channels ?? [])
      const viewMap: Record<string, ResolvedChannel> = {}
      for (const v of (d?.resolved_channels ?? []) as ResolvedChannel[]) {
        if (v?.channel_id) viewMap[v.channel_id] = v
      }
      setViews(viewMap)
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

  /** 打开通道配置：结构化字段（含"来自 .env"徽标）+ 高级 JSON（其余字段） */
  const openConfig = (row: ExtRow) => {
    const id = row.ext_id ?? ''
    const view = views[id]
    const draft: Record<string, string> = {}
    for (const f of view?.fields ?? []) {
      // 口令绝不回显：输入框始终从空开始（留空 = 不修改）
      draft[f.field] = f.secret ? '' : String(f.local ?? '')
    }
    setForm(draft)
    setClearedSecrets({})
    setConfigTarget(row)
    // 高级 JSON 只带"非映射字段"（method / headers / use_tls …）与其它自定义键；
    // 口令等敏感项不进来，避免在文本框里回显凭据。
    const secretFields = new Set((view?.fields ?? []).filter((f) => f.secret).map((f) => f.field))
    const seed: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(row.config ?? {})) {
      if (secretFields.has(key)) continue
      seed[key] = value
    }
    setConfigText(JSON.stringify(seed, null, 2))
  }

  const saveConfig = async () => {
    if (!configTarget) return
    const id = configTarget.ext_id ?? ''
    const view = views[id]
    let parsed: Record<string, unknown>
    try {
      const raw = JSON.parse(configText || '{}')
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        setErr('通道配置必须是 JSON 对象 —— 没有提交')
        return
      }
      parsed = raw as Record<string, unknown>
    } catch {
      setErr('配置不是合法 JSON —— 没有提交')
      return
    }
    // 结构化字段覆盖 JSON 里的同名键（表单是主编辑面，高级框只管其余字段）
    const payload: Record<string, unknown> = { ...parsed }
    for (const f of view?.fields ?? []) {
      const text = (form[f.field] ?? '').trim()
      if (f.secret) {
        // 口令：显式清除 ⇒ null；填了新值 ⇒ 新值；留空 ⇒ **不带该键**（= 不修改）
        if (clearedSecrets[id]) payload[f.field] = null
        else if (text) payload[f.field] = text
        else delete payload[f.field]
        continue
      }
      // 非口令：空串 = 清空（后端会把它从 .env 删掉，读取回落到 env/默认）
      payload[f.field] = text
    }
    setBusy('configure'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_CONFIGURE, {
        type: 'channel', id, config: payload,
      })
      if (r?.ok === false) { setErr('保存配置失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg('配置已保存：' + id + (r?.message ? '（' + r.message + '）' : ''))
      setConfigTarget(null)
      setClearedSecrets({})
      await load()
    } catch (ev) { setErr('保存配置失败：' + (ev instanceof Error ? ev.message : String(ev))) } finally { setBusy('') }
  }

  /** 复制空态范例（后端给的那一份；前端不自己拼模板） */
  const copyExample = async (id: string, example?: Record<string, unknown>) => {
    const text = JSON.stringify(example ?? {}, null, 2)
    try {
      if (navigator?.clipboard?.writeText) await navigator.clipboard.writeText(text)
      setCopiedId(id)
    } catch {
      setErr('复制失败：可以手动选中上面的范例')
    }
  }

  /** 新增一条通道（多通道）：同类型多条，主通道之外的值只存本机 */
  const addChannel = async () => {
    const id = newId.trim()
    if (!id) return
    const channelType = newType === 'smtp' ? 'smtp' : 'webhook'
    setBusy('add'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_INSTALL, {
        type: 'channel', source: id,
        params: {
          channel_type: channelType,
          name: newName.trim() || id,
          description: '界面新增的通道',
          config: { channel_type: channelType },
        },
      })
      if (r?.ok === false) { setErr('新增通道失败：' + (r?.error || r?.message || '后端返回 ok=false')); return }
      setMsg('已新增通道 ' + id + '（空的：展开「配置」照抄范例填值）')
      setNewId('')
      setNewName('')
      await load()
    } catch (ev) {
      setErr('新增通道失败：' + (ev instanceof Error ? ev.message : String(ev)))
    } finally { setBusy('') }
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

  /** 就地测试发送：通道 id 由行决定（不再让用户手填/选择通道） */
  const sendTo = async (id: string) => {
    const text = (drafts[id] ?? '').trim()
    if (!id || !text) return
    setSendBusyId(id); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(EXTENSIONS_CHANNELS_SEND, {
        channel_id: id,
        message: text,
      })
      if (r?.ok === false) { setErr('发送失败：' + (r.error || r.message || '后端返回 ok=false')); return }
      setMsg('已发送' + (r?.message ? '：' + r.message : ''))
      setDrafts((prev) => ({ ...prev, [id]: '' }))
    } catch (e) {
      setErr('发送失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setSendBusyId('') }
  }

  /** 合并视图（2026-11-13）：内置模板 ∪ 已装通道，同 id 只出现一行。
   *  已装但不在模板里的（手工装/自定义）追加在末尾 —— 否则"装了却在列表里看不到"。 */
  const merged = useMemo<ChannelRow[]>(() => {
    const byId = new Map(installed.map((r) => [r.ext_id ?? '', r]))
    const seen = new Set<string>()
    const rows: ChannelRow[] = []
    for (const b of builtin) {
      const id = b.id ?? ''
      if (!id || seen.has(id)) continue
      seen.add(id)
      rows.push({ id, name: b.name ?? id, description: b.description ?? '', installed: byId.get(id) })
    }
    for (const r of installed) {
      const id = r.ext_id ?? ''
      if (!id || seen.has(id)) continue
      seen.add(id)
      rows.push({ id, name: r.name ?? id, description: '', installed: r })
    }
    // 已装且启用的排最前（日常要看的就是它们），未装的沉底；同组内保持模板顺序
    const rank = (row: ChannelRow) => (row.installed ? (row.installed.enabled ? 0 : 1) : 2)
    return rows
      .map((row, index) => ({ row, index }))
      .sort((a, b) => rank(a.row) - rank(b.row) || a.index - b.index)
      .map((item) => item.row)
  }, [builtin, installed])

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


      {/* 【2026-11-13 合并】原来是「内置通道模板」+「已装通道」两张卡片，同一个通道会出现两次
          （模板行显示"已装（未启用）"、已装行再显示一遍配置/启停/卸载）—— 一列到底更好读：
          未装的给「安装」，装了的直接带「配置/启停/卸载」。 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Settings2 size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">
            {'通道（' + merged.length + ' 个 · 已装 ' + installed.length + ' · 启用 ' + installed.filter((c) => c.enabled).length + '）'}
          </h3>
          <span className="text-[10px] text-slate-500">未装的点「安装」；装了的在这里配置 / 启停 / 卸载 / 就地发一条测试消息</span>
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
        {merged.length === 0 && busy !== 'load' && (
          <div className="text-[11px] text-slate-500">没有可用的通道模板。</div>
        )}
        <div className="flex flex-col gap-1">
          {merged.map((item) => {
            const id = item.id
            const row = item.installed
            const view = views[id]
            const isConfirm = confirmUninstall === id
            const editing = configTarget?.ext_id === id
            return (
              <div key={'installed-' + id} className="flex flex-col gap-1 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                  {view && (
                    <span
                      className={'text-[9px] ' + (view.primary ? 'text-emerald-300' : 'text-slate-500')}
                      title={view.primary
                        ? '主通道：值同时写进 .env（重启后仍然生效）'
                        : '本机通道：值只存本机台账，不写 .env'}
                    >
                      {view.primary ? '主通道 · 写 .env' : '本机 · 不写 .env'}
                    </span>
                  )}
                  <span className="text-slate-300">{row?.name || item.name}</span>
                  {item.description ? (
                    <span className="truncate text-[10px] text-slate-500">{item.description}</span>
                  ) : null}
                  <span className={'text-[10px] ' + (!row ? 'text-slate-500' : row.enabled ? 'text-emerald-400' : 'text-amber-300')}>
                    {!row ? '未安装' : row.enabled ? '已启用' : '已装（未启用）'}
                  </span>
                  <span className="ml-auto flex items-center gap-1.5">
                    {!row && (
                      <button
                        type="button"
                        className={BTN_PRIMARY}
                        onClick={() => void install(id)}
                        disabled={loading}
                        aria-label={'安装通道 ' + id}
                      >
                        {busy === 'install:' + id ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 安装
                      </button>
                    )}
                    {row && (
                    <>
                    <button
                      type="button"
                      className={BTN}
                      onClick={() => (editing ? setConfigTarget(null) : openConfig(row))}
                      disabled={loading}
                      aria-label={'配置通道 ' + id}
                    >
                      <Settings2 size={11} /> {editing ? '收起配置' : '配置'}
                    </button>
                    {/* 停用态下「启用」是下一步该点的动作 ⇒ 高亮；已启用的「停用」保持中性（避免误点） */}
                    <button
                      type="button"
                      className={row.enabled ? BTN : BTN_PRIMARY}
                      onClick={() => void toggleInstalled(row)}
                      disabled={loading}
                      aria-label={'切换通道 ' + id}
                    >
                      {busy === 'toggle:' + id ? <Loader2 size={11} className="animate-spin" /> : null} {row.enabled ? '停用' : '启用'}
                    </button>
                    <button type="button" className={BTN} onClick={() => setConfirmUninstall(isConfirm ? '' : id)} disabled={loading} aria-label={'卸载通道 ' + id}>
                      <Trash2 size={11} /> 卸载
                    </button>
                    </>
                    )}
                  </span>
                </div>
                {row && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <input
                      className={INPUT + ' w-64 text-[11px]'}
                      placeholder={row.enabled ? '发一条测试消息…' : '启用后可测试发送'}
                      value={drafts[id] ?? ''}
                      onChange={(e) => setDrafts((prev) => ({ ...prev, [id]: e.target.value }))}
                      disabled={loading || !row.enabled}
                      aria-label={'测试消息 ' + id}
                    />
                    <button
                      type="button"
                      className={BTN_EM}
                      onClick={() => void sendTo(id)}
                      disabled={loading || !row.enabled || !(drafts[id] ?? '').trim()}
                      aria-label={'发送测试消息 ' + id}
                    >
                      {sendBusyId === id ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />} 发送
                    </button>
                    {!row.enabled && <span className="text-[10px] text-slate-500">先「启用」再测试发送</span>}
                  </div>
                )}
                {row && isConfirm && (
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
                {row && editing && (
                  <div className="flex flex-col gap-1.5">
                    {/* 空通道：范例 + 说明 + 一键复制（后端给文案，前端不抄第二份） */}
                    {view && (view.fields ?? []).every((f) => !f.configured) && (
                      <div className="rounded-sm border border-amber-900/50 bg-amber-500/5 p-2">
                        <div className="text-[10px] text-amber-200">
                          这条通道还是空的（未配置）。照抄下面的范例，改成自己的值就能用：
                        </div>
                        <pre className="mt-1 overflow-x-auto rounded bg-slate-950 p-2 text-[10px] leading-4 text-slate-300">
{JSON.stringify(view.example ?? {}, null, 2)}
                        </pre>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            className={BTN}
                            onClick={() => void copyExample(id, view.example)}
                            aria-label={'复制范例 ' + id}
                          >
                            <Copy size={11} /> {copiedId === id ? '已复制' : '复制范例'}
                          </button>
                          <span className="text-[10px] text-slate-500">保存后这些值同时写进 .env，重启后仍然生效</span>
                        </div>
                        <ul className="mt-1 list-disc pl-4 text-[10px] text-slate-400">
                          {(view.notes ?? []).map((note) => (<li key={note}>{note}</li>))}
                        </ul>
                      </div>
                    )}
                    {view && (
                      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                        {(view.fields ?? []).map((f) => {
                          const label = FIELD_LABELS[f.field] ?? f.field
                          const value = f.secret && clearedSecrets[id] ? '' : (form[f.field] ?? '')
                          const placeholder = f.secret
                            ? (f.configured ? '已配置（留空 = 不修改）' : '未配置')
                            : (f.configured && !f.local_configured
                              ? '当前回落到 ' + String(f.effective ?? '')
                              : '未配置')
                          return (
                            <div key={f.field} className="flex flex-col gap-0.5">
                              <span className="flex flex-wrap items-center gap-1 text-[10px] text-slate-400">
                                {label}
                                <code className="font-mono text-[9px] text-slate-600">{f.env_key}</code>
                                {f.configured && (
                                  <span className={'text-[9px] ' + (SOURCE_BADGE[f.source ?? ''] ?? 'text-slate-500')}>
                                    {f.source_label}
                                  </span>
                                )}
                              </span>
                              <input
                                type={f.secret ? 'password' : 'text'}
                                className={INPUT + ' text-[11px]'}
                                value={value}
                                placeholder={placeholder}
                                onChange={(ev) => setForm((prev) => ({ ...prev, [f.field]: ev.target.value }))}
                                aria-label={'通道字段 ' + id + ' ' + f.field}
                              />
                              {f.secret && f.configured && (
                                <button
                                  type="button"
                                  className="self-start text-[9px] text-amber-300 hover:underline"
                                  onClick={() => setClearedSecrets((prev) => ({ ...prev, [id]: !prev[id] }))}
                                  aria-label={'清除口令 ' + id}
                                >
                                  {clearedSecrets[id] ? '撤销清除' : '清除口令（保存后生效）'}
                                </button>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                    <div className="text-[10px] text-slate-500">
                      其余字段（method / headers / use_tls …）用下面的高级 JSON；口令不会在这里回显。
                      留空字段保存后 = <strong>从 .env 删除该键</strong>（读取回落到 .env 旧键 / 默认值，不写空串占位）。
                    </div>
                    <textarea
                      className={INPUT + ' h-32 font-mono text-[11px]'}
                      value={configText}
                      onChange={(ev) => setConfigText(ev.target.value)}
                      aria-label={'通道配置 ' + id}
                    />
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button type="button" className={BTN_EM} onClick={() => void saveConfig()} disabled={loading} aria-label={'保存通道配置 ' + id}>
                        {busy === 'configure' ? <Loader2 size={11} className="animate-spin" /> : null} 保存配置
                      </button>
                      <button type="button" className={BTN} onClick={() => setConfigTarget(null)} disabled={loading}>取消</button>
                      <span className="text-[10px] text-slate-500">坏 JSON 不会提交</span>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* 多通道（2026-11-14）：同一种类型可以有多条；主通道之外的值只存本机 */}
      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Plus size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">新增通道（多通道）</h3>
          <span className="text-[10px] text-slate-500">
            同一种类型可以加多条；<span className="text-emerald-300">主通道</span>
            （id 为 email_smtp / webhook）的值会写进 .env，其余只存本机
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <select
            className={INPUT + ' text-[11px]'}
            value={newType}
            onChange={(ev) => setNewType(ev.target.value)}
            aria-label="新通道类型"
          >
            <option value="smtp">邮件（SMTP）</option>
            <option value="webhook">Webhook</option>
          </select>
          <input
            className={INPUT + ' w-44 text-[11px]'}
            placeholder="通道 id，如 email_smtp_2"
            value={newId}
            onChange={(ev) => setNewId(ev.target.value)}
            aria-label="新通道 id"
          />
          <input
            className={INPUT + ' w-44 text-[11px]'}
            placeholder="显示名称（可空）"
            value={newName}
            onChange={(ev) => setNewName(ev.target.value)}
            aria-label="新通道名称"
          />
          <button
            type="button"
            className={BTN_PRIMARY}
            onClick={() => void addChannel()}
            disabled={loading || !newId.trim()}
            aria-label="新增通道"
          >
            {busy === 'add' ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} 新增
          </button>
        </div>
        <div className="mt-1 text-[10px] text-slate-500">
          范例：邮件通道填 <code className="font-mono">email_smtp_2</code> / 值班邮件；
          Webhook 填 <code className="font-mono">webhook_ops</code> / 运维群。
          新增出来是空通道，展开「配置」照抄范例填值即可（留空保存 = 从 .env 删键）。
        </div>
      </div>

    </div>
  )
}
