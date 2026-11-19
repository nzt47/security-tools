/**
 * 安全与权限 —— 权限开关 / 紧急控制 / 危险关键词 / 隐私（11 条）
 * ------------------------------------------------------------------
 *   /api/permission/status|stats|log|access-log      只读：现状与审计
 *   /api/permission/toggle|emergency                写：开关与紧急控制
 *   /api/safety/alerts|check|keywords               告警 / 文本校验 / 关键词库
 *   /api/privacy/info|window/consent                 隐私清单与窗口授权
 *
 * 【为什么把「紧急控制」放在这页顶部而不是折叠起来】
 * 它是出事时第一个要按的按钮（停止/暂停/封网/取消当前操作）。
 * 出事时再展开三层菜单找它，等于没有。
 *
 * 【为什么 toggle 要回读】每个开关都联动运行时（窗口监控会去申请授权、
 * 网络开关会改拦截器）。前端不乐观更新：写完必须重拉 status，让界面说的是
 * 后端真实状态，而不是我以为的状态。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, Ban, Lock, Loader2, Pause, Play, Power, RefreshCw, RotateCcw, Shield, ShieldAlert, Sliders,
} from 'lucide-react'
import { Button, Checkbox, CodeBlock, ErrorBox, hubGet, hubPost, Input, Notice, Select, Tabs, Textarea } from '../components/ui'
import {
  PERMISSION_ACCESS_LOG,
  PERMISSION_EMERGENCY,
  PERMISSION_LOG,
  PERMISSION_STATS,
  PERMISSION_STATUS,
  PERMISSION_TOGGLE,
  PRIVACY_INFO,
  SAFETY_ALERTS,
  SAFETY_CHECK,
  SAFETY_KEYWORDS,
  WINDOW_CONSENT,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-caption text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'

const TOGGLE_LABEL: Record<string, string> = {
  dangerous_ops: '危险操作',
  file_write: '写文件',
  network_access: '网络访问',
  sensor: '传感器',
  window_monitor: '窗口监控',
}

type Tab = 'perm' | 'safety' | 'privacy'

export default function SecurityPanel() {
  const [tab, setTab] = useState<Tab>('perm')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [status, setStatus] = useState<Record<string, unknown> | null>(null)
  const [stats, setStats] = useState<Record<string, unknown> | null>(null)
  const [logs, setLogs] = useState<Record<string, unknown> | null>(null)
  const [accessLogs, setAccessLogs] = useState<Record<string, unknown> | null>(null)
  const [confirmEmergency, setConfirmEmergency] = useState('')

  const [alerts, setAlerts] = useState<Record<string, unknown> | null>(null)
  const [keywords, setKeywords] = useState<Record<string, unknown> | null>(null)
  const [checkText, setCheckText] = useState('')
  const [checkResult, setCheckResult] = useState<Record<string, unknown> | null>(null)
  const [kwPattern, setKwPattern] = useState('')
  const [kwDesc, setKwDesc] = useState('')
  const [kwLevel, setKwLevel] = useState('warning')

  const [privacy, setPrivacy] = useState<Record<string, unknown> | null>(null)
  const [confirmConsent, setConfirmConsent] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const loadPerm = useCallback(async () => {
    setBusy('perm'); setErr('')
    try {
      const [st, sa, lg, al] = await Promise.allSettled([
        hubGet<Record<string, unknown>>(PERMISSION_STATUS),
        hubGet<Record<string, unknown>>(PERMISSION_STATS),
        hubGet<Record<string, unknown>>(PERMISSION_LOG + '?limit=20'),
        hubGet<Record<string, unknown>>(PERMISSION_ACCESS_LOG + '?limit=20'),
      ])
      if (st.status === 'fulfilled') setStatus(st.value)
      if (sa.status === 'fulfilled') setStats(sa.value)
      if (lg.status === 'fulfilled') setLogs(lg.value)
      if (al.status === 'fulfilled') setAccessLogs(al.value)
    } catch (e) { fail('读取权限状态失败', e) } finally { setBusy('') }
  }, [])

  const loadSafety = useCallback(async () => {
    setBusy('safety'); setErr('')
    try {
      const [al, kw] = await Promise.allSettled([
        hubGet<Record<string, unknown>>(SAFETY_ALERTS),
        hubGet<Record<string, unknown>>(SAFETY_KEYWORDS),
      ])
      if (al.status === 'fulfilled') setAlerts(al.value)
      if (kw.status === 'fulfilled') setKeywords(kw.value)
    } catch (e) { fail('读取安全信息失败', e) } finally { setBusy('') }
  }, [])

  const loadPrivacy = useCallback(async () => {
    setBusy('privacy'); setErr('')
    try {
      const r = await hubGet<Record<string, unknown>>(PRIVACY_INFO)
      setPrivacy(r)
    } catch (e) { fail('读取隐私清单失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadPerm(); void loadSafety(); void loadPrivacy() }, [loadPerm, loadSafety, loadPrivacy])

  const toggle = async (key: string, next: boolean) => {
    setBusy('toggle:' + key); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(PERMISSION_TOGGLE, { key, enabled: next })
      if (r?.ok === false) { setErr('切换失败：' + (r.error || 'ok=false')); return }
      setMsg('已' + (next ? '开启 ' : '关闭 ') + (TOGGLE_LABEL[key] ?? key))
      await loadPerm()   // 写完回读：界面说的是后端真实状态
    } catch (e) { fail('切换失败', e) } finally { setBusy('') }
  }

  const emergency = async (action: string) => {
    setBusy('emergency:' + action); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string; paused?: boolean; blocked?: boolean }>(
        PERMISSION_EMERGENCY, { action },
      )
      if (r?.ok === false) { setErr('紧急操作失败：' + (r.error || 'ok=false')); return }
      setMsg(r?.message ?? ('已执行：' + action))
      setConfirmEmergency('')
      await loadPerm()
    } catch (e) { fail('紧急操作失败', e) } finally { setBusy('') }
  }

  const runCheck = async () => {
    setBusy('check'); setErr(''); setCheckResult(null)
    try {
      const r = await hubPost<Record<string, unknown>>(SAFETY_CHECK, { text: checkText })
      setCheckResult(r)
    } catch (e) { fail('文本校验失败', e) } finally { setBusy('') }
  }

  const addKeyword = async () => {
    setBusy('kw-add'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(SAFETY_KEYWORDS, {
        pattern: kwPattern, description: kwDesc, level: kwLevel, category: '自定义',
      })
      if (r?.ok === false) { setErr('添加关键词失败：' + (r.error || 'ok=false')); return }
      setMsg('已添加危险关键词：' + kwPattern)
      setKwPattern(''); setKwDesc('')
      await loadSafety()
    } catch (e) { fail('添加关键词失败', e) } finally { setBusy('') }
  }

  const consent = async () => {
    setBusy('consent'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; consent?: boolean }>(WINDOW_CONSENT, { consent: true })
      if (r?.ok === false) { setErr('授权失败：' + (r.error || 'ok=false')); return }
      setMsg('窗口活动读取已授权（可随时在权限开关里关闭）')
      setConfirmConsent(false)
      await Promise.all([loadPrivacy(), loadPerm()])
    } catch (e) { fail('授权失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const toggles = (status?.toggles ?? {}) as Record<string, boolean>
  const emer = (status?.emergency ?? {}) as Record<string, boolean>
  const st = (status?.stats ?? {}) as Record<string, number>

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Shield size={14} className="text-cyan-400" />
        <h2 className="text-ui font-semibold text-slate-100">安全与权限</h2>
        <span className="text-caption text-slate-500">权限开关 · 紧急控制 · 危险关键词 · 隐私清单</span>
        <div className="ml-auto">
          <Button type="button" variant="default" size="xs" onClick={() => { void loadPerm(); void loadSafety(); void loadPrivacy() }} disabled={loading} aria-label="刷新安全与权限">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </Button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <Notice tone="success" size="sm" className="mb-2">{msg}</Notice>}

      {/* 紧急控制：出事时第一个要按的 */}
      <div className="mb-3 rounded-lg border border-red-900/50 bg-red-500/5 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <ShieldAlert size={12} className="text-red-400" />
          <h3 className="text-body font-semibold text-red-200">紧急控制</h3>
          <span className="text-micro text-red-300/80">
            当前：{emer.stopped ? '已停止' : '运行中'}
            {emer.paused ? ' · 已暂停' : ''}
            {emer.network_blocked ? ' · 网络已封锁' : ''}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button type="button" variant="danger-outline" size="xs" onClick={() => void emergency('pause')} disabled={loading} aria-label={emer.paused ? '恢复智能体' : '暂停智能体'}>
              {emer.paused ? <Play size={11} /> : <Pause size={11} />} {emer.paused ? '恢复' : '暂停'}
            </Button>
            <Button type="button" variant="danger-outline" size="xs" onClick={() => void emergency('network_block')} disabled={loading} aria-label={emer.network_blocked ? '恢复网络' : '封锁网络'}>
              <Ban size={11} /> {emer.network_blocked ? '恢复网络' : '封锁网络'}
            </Button>
            <Button type="button" variant="danger-outline" size="xs" onClick={() => void emergency('cancel')} disabled={loading} aria-label="取消当前操作">
              取消当前操作
            </Button>
            <Button
              type="button"
              variant="danger-outline" size="xs"
              onClick={() => setConfirmEmergency(confirmEmergency === 'stop' ? '' : 'stop')}
              disabled={loading}
              aria-label="紧急停止"
            >
              <Power size={11} /> 紧急停止
            </Button>
            <Button type="button" variant="default" size="xs" onClick={() => void emergency('reset')} disabled={loading} aria-label="重置追踪器">
              <RotateCcw size={11} /> 重置
            </Button>
          </div>
        </div>
        {confirmEmergency === 'stop' && (
          <div className="flex flex-wrap items-center gap-2 rounded-sm border border-red-800/60 bg-red-500/10 px-2 py-1.5">
            <span className="text-micro text-red-200">紧急停止会中止正在进行的操作并锁定危险动作。确定？</span>
            <Button type="button" variant="danger-outline" size="xs" onClick={() => void emergency('stop')} disabled={loading} aria-label="确认紧急停止">
              {busy === 'emergency:stop' ? <Loader2 size={11} className="animate-spin" /> : null} 确认停止
            </Button>
            <Button type="button" variant="default" size="xs" onClick={() => setConfirmEmergency('')}>取消</Button>
          </div>
        )}
      </div>

      <div className="mb-2 flex items-center gap-1">
        <Tabs
          variant="segmented"
          size="sm"
          value={tab}
          onChange={(v) => setTab(v as typeof tab)}
          items={([['perm', '权限与审计'], ['safety', '危险关键词'], ['privacy', '隐私']] as const).map(([id, label]) => ({ value: id, label }))}
        />
      </div>

      {tab === 'perm' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {[
              ['工具数', st.tools],
              ['权限检查', st.perm_checks],
              ['拦截', st.blocked],
              ['告警', st.total_alerts],
            ].map(([k, v]) => (
              <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-900/50 px-2 py-1.5">
                <div className="text-micro text-slate-500">{String(k)}</div>
                <div className="font-mono text-body-lg text-cyan-300">{String(v ?? 0)}</div>
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
              <Sliders size={11} className="text-cyan-400" /> 权限开关（切换后回读，界面只说后端真实状态）
            </div>
            <div className="flex flex-col gap-1">
              {Object.keys(TOGGLE_LABEL).map((key) => {
                const on = toggles[key] === true
                return (
                  <div key={key} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-caption">
                    <span className="text-slate-300">{TOGGLE_LABEL[key]}</span>
                    <span className="font-mono text-micro text-slate-600">{key}</span>
                    <span className={on ? 'text-emerald-400' : 'text-slate-500'}>{on ? '开' : '关'}</span>
                    <Button
                      type="button"
                      variant={on ? 'danger-outline' : 'accent'}
                      size="xs"
                      onClick={() => void toggle(key, !on)}
                      disabled={loading}
                      aria-label={'切换开关 ' + key}
                    >
                      {busy === 'toggle:' + key ? <Loader2 size={11} className="animate-spin" /> : null} {on ? '关闭' : '开启'}
                    </Button>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-caption text-slate-400">权限统计</div>
            <CodeBlock maxH={208}>{JSON.stringify(stats ?? {}, null, 2)}</CodeBlock>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-caption text-slate-400">操作 / 权限日志（最近 20 条）</div>
            <CodeBlock maxH={208}>{JSON.stringify(logs ?? {}, null, 2)}</CodeBlock>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-caption text-slate-400">数据访问记录（最近 20 条）</div>
            <CodeBlock maxH={208}>{JSON.stringify(accessLogs ?? {}, null, 2)}</CodeBlock>
          </div>
        </div>
      )}

      {tab === 'safety' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
              <Lock size={11} className="text-cyan-400" /> 文本危险校验
            </div>
            <Textarea
              size="sm"
              className="h-20 w-full"
              value={checkText}
              onChange={(e) => setCheckText(e.target.value)}
              placeholder="粘贴一段命令或文本，看是否会被安全守卫判定为危险"
              aria-label="待校验文本"
            />
            <div className="mt-1">
              <Button type="button" variant="accent" size="xs" onClick={() => void runCheck()} disabled={loading || checkText.trim() === ''} aria-label="校验危险文本">
                {busy === 'check' ? <Loader2 size={11} className="animate-spin" /> : null} 校验
              </Button>
            </div>
            {checkResult && <CodeBlock maxH={208} className="mt-1">{JSON.stringify(checkResult, null, 2)}</CodeBlock>}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 flex items-center gap-2 text-caption text-slate-400">
              <AlertTriangle size={11} className="text-amber-400" /> 最近告警
            </div>
            <CodeBlock maxH={208}>{JSON.stringify(alerts ?? {}, null, 2)}</CodeBlock>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
              <Lock size={11} className="text-cyan-400" /> 危险关键词库
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Input size="sm" className="w-64" value={kwPattern} onChange={(e) => setKwPattern(e.target.value)} placeholder="正则，如 rm\s+-rf" aria-label="关键词正则" />
              <Input size="sm" className="w-56" value={kwDesc} onChange={(e) => setKwDesc(e.target.value)} placeholder="说明" aria-label="关键词说明" />
              <Select value={kwLevel} onChange={(v) => setKwLevel(v)} aria-label="关键词级别" size="sm" hidePlaceholder options={[{ label: 'warning', value: 'warning' }, { label: 'critical', value: 'critical' }]} />
              <Button type="button" variant="accent" size="xs" onClick={() => void addKeyword()} disabled={loading || kwPattern.trim() === ''} aria-label="添加危险关键词">
                {busy === 'kw-add' ? <Loader2 size={11} className="animate-spin" /> : null} 添加
              </Button>
            </div>
            <CodeBlock maxH={288}>{JSON.stringify(keywords ?? {}, null, 2)}</CodeBlock>
          </div>
        </div>
      )}

      {tab === 'privacy' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-1 text-caption text-slate-400">数据采集范围与用途</div>
            <CodeBlock maxH={320}>{JSON.stringify(privacy ?? {}, null, 2)}</CodeBlock>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-caption text-slate-400">
              <Lock size={11} className="text-cyan-400" /> 窗口活动授权
              <span className="text-micro text-slate-500">需要用户明确同意才会读取窗口标题与进程</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Checkbox checked={confirmConsent} onChange={(e) => setConfirmConsent(e.target.checked)} label="我同意读取窗口活动" aria-label="确认窗口授权" />
              <Button type="button" variant="accent" size="xs" onClick={() => void consent()} disabled={loading || !confirmConsent} aria-label="授权窗口活动">
                {busy === 'consent' ? <Loader2 size={11} className="animate-spin" /> : null} 授权
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
