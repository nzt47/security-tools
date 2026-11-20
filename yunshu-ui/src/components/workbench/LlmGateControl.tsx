/**
 * LlmGateControl —— 工作台顶栏的「LLM 禁用时段」开关 + 配置入口（2026-11-20）
 * ------------------------------------------------------------------
 * Owner 口径：开关放在顶栏；禁用时段（默认工作日 9-12 / 14-18，节假日除外）
 * 内拒绝所有 LLM 请求；时段可在弹窗里自由增删。后端唯一事实源：
 * agent/llm_idle_gate.py（/api/llm-gate）。
 *
 * 降级：读数失败不静默 —— 开关禁用并给出 error 提示，不假装"已开启"。
 */
import { useCallback, useEffect, useState } from 'react'
import { Ban, Plus, Settings2, Trash2 } from 'lucide-react'
import { Button, Checkbox, Input, ModalBase, Notice, Switch } from '@/components/ui'
import {
  getLlmGate,
  setLlmGate,
  type GateConfig,
  type GateState,
  type GateWindow,
} from '@/api/llmGate'

const DAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']

function cloneConfig(cfg: GateConfig): GateConfig {
  return {
    enabled: cfg.enabled,
    windows: cfg.windows.map((w) => ({ days: [...w.days], start: w.start, end: w.end })),
    skip_holidays: cfg.skip_holidays,
    lead_seconds: cfg.lead_seconds,
  }
}

function errText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

export default function LlmGateControl() {
  const [config, setConfig] = useState<GateConfig | null>(null)
  const [state, setState] = useState<GateState | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<GateConfig | null>(null)
  const [leadMinutes, setLeadMinutes] = useState('5')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  const load = useCallback(async () => {
    try {
      const payload = await getLlmGate()
      setConfig(payload.config)
      setState(payload.state)
      setError('')
    } catch (reason: unknown) {
      setError(errText(reason))
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { void load() }, 60_000)
    return () => { window.clearInterval(timer) }
  }, [load])

  const toggle = async (next: boolean) => {
    if (!config || busy) return
    setBusy(true)
    setError('')
    try {
      const payload = await setLlmGate({ enabled: next })
      setConfig(payload.config)
      setState(payload.state)
    } catch (reason: unknown) {
      setError(errText(reason))
      void load()
    } finally {
      setBusy(false)
    }
  }

  const openSettings = () => {
    if (!config) return
    setDraft(cloneConfig(config))
    setLeadMinutes(String(Math.max(0, Math.round(config.lead_seconds / 60))))
    setSaveError('')
    setOpen(true)
  }

  const save = async () => {
    if (!draft || saving) return
    setSaving(true)
    setSaveError('')
    try {
      const lead = Math.max(0, Math.min(60, Number(leadMinutes) || 0)) * 60
      const payload = await setLlmGate({ ...draft, lead_seconds: lead })
      setConfig(payload.config)
      setState(payload.state)
      setOpen(false)
    } catch (reason: unknown) {
      setSaveError(errText(reason))
    } finally {
      setSaving(false)
    }
  }

  const updateWindow = (index: number, patch: Partial<GateWindow>) => {
    setDraft((prev) => {
      if (!prev) return prev
      const windows = prev.windows.map((w, i) => (i === index ? { ...w, ...patch } : w))
      return { ...prev, windows }
    })
  }

  const toggleDay = (index: number, day: number, checked: boolean) => {
    const w = draft?.windows[index]
    if (!w) return
    const days = checked
      ? Array.from(new Set([...w.days, day])).sort((a, b) => a - b)
      : w.days.filter((d) => d !== day)
    updateWindow(index, { days })
  }

  const addWindow = () => {
    setDraft((prev) => (prev
      ? { ...prev, windows: [...prev.windows, { days: [0, 1, 2, 3, 4], start: '09:00', end: '10:00' }] }
      : prev))
  }

  const removeWindow = (index: number) => {
    setDraft((prev) => (prev ? { ...prev, windows: prev.windows.filter((_w, i) => i !== index) } : prev))
  }

  const blocked = !!state?.blocked
  const title = state?.message || 'LLM 禁用时段'

  return (
    <>
      <div className="flex items-center gap-1.5" title={title}>
        <Ban size={14} className={blocked ? 'text-rose-400' : 'text-slate-500'} aria-hidden="true" />
        <Switch
          size="sm"
          checked={!!config?.enabled}
          onCheckedChange={(next) => { void toggle(next) }}
          label="LLM 禁用时段"
          disabled={!config}
          loading={busy}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="设置 LLM 禁用时段"
          title="设置禁用时段"
          onClick={openSettings}
          disabled={!config}
        >
          <Settings2 size={14} />
        </Button>
        {error && (
          <span className="hidden text-caption text-danger lg:inline" title={error}>
            闸门不可用
          </span>
        )}
      </div>

      <ModalBase
        open={open}
        onClose={() => setOpen(false)}
        title="LLM 禁用时段"
        width="max-w-xl"
        closeButton
        footer={(
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button type="button" variant="primary" onClick={() => { void save() }} loading={saving}>
              保存
            </Button>
          </div>
        )}
      >
        {draft && (
          <div className="space-y-4">
            {saveError && <Notice tone="danger" size="sm" icon={null}>{saveError}</Notice>}

            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-ui text-foreground">启用禁用时段</div>
                <div className="text-caption text-muted-foreground">
                  启用后，下列时段内所有 LLM 请求（含对话）会被拒绝；其余时段放行。
                </div>
              </div>
              <Switch
                checked={draft.enabled}
                onCheckedChange={(next) => setDraft({ ...draft, enabled: next })}
                label="启用禁用时段"
              />
            </div>

            <Checkbox
              label="中国法定节假日不拦截（按空闲放行）"
              checked={draft.skip_holidays}
              onChange={(e) => setDraft({ ...draft, skip_holidays: e.target.checked })}
            />

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-ui text-foreground">提前收尾</span>
              <Input
                type="number"
                min={0}
                max={60}
                value={leadMinutes}
                onChange={(e) => setLeadMinutes(e.target.value)}
                className="w-24"
                aria-label="提前收尾分钟数"
              />
              <span className="text-caption text-muted-foreground">
                分钟（临近禁用时段时提醒收尾，并顺延尚未启动的 LLM 任务）
              </span>
            </div>

            <div className="space-y-2">
              <div className="text-ui text-foreground">禁用时段（北京时间）</div>
              {draft.windows.length === 0 && (
                <div className="text-caption text-muted-foreground">未配置时段：开关打开也不会拦截。</div>
              )}
              {draft.windows.map((w, index) => (
                <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
                  {DAY_LABELS.map((label, day) => (
                    <Checkbox
                      key={day}
                      size="sm"
                      label={label}
                      checked={w.days.includes(day)}
                      onChange={(e) => toggleDay(index, day, e.target.checked)}
                    />
                  ))}
                  <Input
                    type="time"
                    value={w.start}
                    onChange={(e) => updateWindow(index, { start: e.target.value })}
                    className="w-28"
                    aria-label={'第 ' + (index + 1) + ' 条时段开始时间'}
                  />
                  <span className="text-caption text-muted-foreground">至</span>
                  <Input
                    type="time"
                    value={w.end}
                    onChange={(e) => updateWindow(index, { end: e.target.value })}
                    className="w-28"
                    aria-label={'第 ' + (index + 1) + ' 条时段结束时间'}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={'删除第 ' + (index + 1) + ' 条时段'}
                    title="删除该时段"
                    onClick={() => removeWindow(index)}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
              <Button type="button" variant="ghost" size="sm" onClick={addWindow}>
                <Plus size={14} />
                添加时段
              </Button>
            </div>

            {state && (
              <div className="text-caption text-muted-foreground">当前：{state.message}</div>
            )}
          </div>
        )}
      </ModalBase>
    </>
  )
}
