/**
 * 消息中心 · 消息通道（内置模板 + 安装 + 调试发送）
 * ------------------------------------------------------------------
 * 【2026-10-18 从「扩展市场」迁入】通道 = **消息发送通道**（webhook / SMTP / Slack /
 * Discord / Telegram），语义属于「消息中心」而不是「扩展市场」，也不属于「网络配置」
 * （后者是代理/网络环境）。原在市场里单独一组，导致"装通道"和"发消息"两件事离得很远。
 *
 * 【2026-10-19 已接线】告警状态变化（触发/恢复）经 `agent/extensions/channel_bridge.py`
 * 发往**已安装且启用**的通道：AlertManager 的状态回调 → 桥接层（摘要脱敏 / 同告警 60s
 * 冷却 / 失败重试 1 次 / `CP_CHANNEL_EVENTS=0` 可整体关闭）。
 * 注意区分：`POST /api/extensions/channels/send` 仍只供本页「手动发送（调试）」，
 * 产品事件走的是桥接层的进程内调用，不经过这个 HTTP 端点。
 *
 * 数据源：/api/extensions/discover（内置模板）、/api/extensions/list（已装通道）、
 *         /api/extensions/install {type:'channel'}、/api/extensions/channels/send（仅手动调试）
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Download, Loader2, PlugZap, Send } from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  EXTENSIONS_CHANNELS_SEND,
  EXTENSIONS_DISCOVER,
  EXTENSIONS_INSTALL,
  EXTENSIONS_LIST,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

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
}

export default function HubChannelsPanel() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [builtin, setBuiltin] = useState<BuiltinChannel[]>([])
  const [installed, setInstalled] = useState<ExtRow[]>([])
  const [channelId, setChannelId] = useState('')
  const [channelMsg, setChannelMsg] = useState('')

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

  useEffect(() => { void load() }, [load])

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
              <div key={'channel-' + id} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
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
        <div className="mb-2 flex items-center gap-2">
          <Send size={12} className="text-cyan-400" />
          <h3 className="text-xs font-semibold text-slate-200">手动发送（调试）</h3>
          <span className="text-[10px] text-slate-500">需要已安装并启用的通道</span>
        </div>
        <div className="mb-2 text-[10px] text-slate-500">
          已装通道：{installed.length === 0
            ? '—'
            : installed.map((c) => (c.ext_id ?? '') + (c.enabled ? '(启用)' : '(停用)')).join('、')}
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
