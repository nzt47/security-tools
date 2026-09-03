/**
 * identityPrompt —— 合并「身份提示词」功能的数据层 + 状态 Hook
 * ------------------------------------------------------------------
 * 提示词实验室的「系统提示词（身份提示词）」区直接编辑后端真实配置：
 *   GET   /api/system-prompt/config            载入配置（sections/registry/stats/summary）
 *   POST  /api/system-prompt/config            保存启停/自定义内容
 *   POST  /api/system-prompt/config/reset      恢复默认
 *   POST  /api/system-prompt/config/preview    按当前（可未保存）配置生成真实注入模板
 * 预览/真实输出所注入的 system message 由服务端模板引擎生成（本地不再维护
 * 一套与线上脱节的「7 段沙箱组件」——深度数据合并）。
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { hubGet, hubPost } from '../hub/components/ui'

// ─── 后端返回结构（宽容类型，字段缺失时回退） ────────────────────────

export interface IdentitySummary {
  total_enabled_tokens?: number
  total_disabled_count?: number
  grand_total?: number
  savings_when_off?: number
  base_template_tokens?: number
}

export interface IdentityMeta {
  key: string
  label?: string
  description?: string
  tokens?: number
  range?: string
  note?: string
  editable?: boolean
  configurable?: boolean
  ui_type?: string
  badge_key?: string
  sub_keys?: string[]
  children?: IdentityMeta[]
}

export interface IdentityStatsItem {
  enabled?: boolean
  tokens?: number
  range?: string
  note?: string
  editable?: boolean
  has_custom?: boolean
  token_limit?: number
}

/** 单个配置节（sections[key]，源自后端存储，宽容解析） */
export interface IdentityRawSection {
  key?: string
  enabled?: boolean
  label?: string
  description?: string
  custom_content?: string
  token_limit?: number
  extra_params?: Record<string, unknown>
  [k: string]: unknown
}

export interface IdentityConfigResponse {
  version?: number
  sections?: Record<string, IdentityRawSection>
  registry?: IdentityMeta[]
  stats?: Record<string, IdentityStatsItem>
  summary?: IdentitySummary
  has_custom_template?: boolean
}

/** 渲染行：把 sections[key] + registry/stats 元信息合成为面板可直接消费的结构 */
export interface IdentityRow {
  key: string
  enabled: boolean
  label: string
  description: string
  customContent: string
  tokenLimit: number
  editable: boolean
  estimate: number
  range: string
  note: string
  hasCustom: boolean
  moduleAvailable: boolean
  moduleBadge: boolean
}

const unwrapObj = <T,>(r: unknown): T => {
  const rr = r as { data?: T }
  return (rr?.data ?? r) as T
}

/**
 * 展示顺序：后端 registry（= 官方 UI/渲染顺序）优先，再补 registry 未覆盖的配置节。
 * 聚合节（如 current_status）本身不是配置节，其 children（body_status/mode_info）
 * 按 registry 顺序拆为独立卡片展示。
 */
export function buildDisplayOrder(sections: Record<string, IdentityRawSection>, registry?: IdentityMeta[]): string[] {
  const ordered: string[] = []
  const seen = new Set<string>()
  const append = (k: string) => {
    if (k && k in sections && !seen.has(k)) {
      seen.add(k)
      ordered.push(k)
    }
  }
  if (Array.isArray(registry)) {
    for (const item of registry) {
      append(item.key)
      for (const child of item.children ?? []) append(child.key)
    }
  }
  for (const k of Object.keys(sections)) append(k)
  return ordered
}

/** 汇总每节展示元信息（registry + 其 children 拍平，供逐节查询） */
export function buildMetaMap(registry?: IdentityMeta[]): Map<string, IdentityMeta> {
  const map = new Map<string, IdentityMeta>()
  if (!Array.isArray(registry)) return map
  for (const item of registry) {
    map.set(item.key, item)
    for (const child of item.children ?? []) map.set(child.key, { ...child, sub_keys: item.sub_keys })
  }
  return map
}

export function buildRows(
  sections: Record<string, IdentityRawSection>,
  registry?: IdentityMeta[],
  stats?: Record<string, IdentityStatsItem>,
): IdentityRow[] {
  const meta = buildMetaMap(registry)
  const statOf = (k: string) => stats?.[k]
  return buildDisplayOrder(sections, registry).map((key) => {
    const sec = sections[key] ?? {}
    const m = meta.get(key)
    const s = statOf(key)
    const extra = (sec.extra_params ?? {}) as Record<string, unknown>
    const moduleAvailable = extra.module_available !== false
    return {
      key,
      enabled: sec.enabled !== false,
      label: String(sec.label ?? m?.label ?? key),
      description: String(m?.description ?? sec.description ?? ''),
      customContent: String(sec.custom_content ?? ''),
      tokenLimit: Number(sec.token_limit ?? s?.token_limit ?? 0),
      editable: Boolean(s?.editable ?? m?.editable),
      estimate: Number(s?.tokens ?? m?.tokens ?? 0),
      range: String(s?.range ?? m?.range ?? ''),
      note: String(s?.note ?? m?.note ?? ''),
      hasCustom: Boolean(s?.has_custom ?? String(sec.custom_content ?? '').trim()),
      moduleAvailable,
      moduleBadge: Boolean(m?.badge_key),
    }
  })
}

// ─── 状态 Hook：载入 / 启停 / 编辑 / 防抖模板预览 / 保存 / 重置 ───────

const PREVIEW_DEBOUNCE_MS = 600

export interface UseIdentityPromptResult {
  rows: IdentityRow[]
  rawSections: Record<string, IdentityRawSection>
  summary: IdentitySummary | null
  template: string
  templateLoading: boolean
  loading: boolean
  saving: boolean
  dirty: boolean
  error: string
  msg: string
  onToggle: (key: string) => void
  onEditContent: (key: string, content: string) => void
  onSave: () => void
  onReset: () => void
  onReload: () => void
}

export function useIdentityPrompt(): UseIdentityPromptResult {
  const [sections, setSections] = useState<Record<string, IdentityRawSection>>({})
  const [registry, setRegistry] = useState<IdentityMeta[]>([])
  const [stats, setStats] = useState<Record<string, IdentityStatsItem>>({})
  const [summary, setSummary] = useState<IdentitySummary | null>(null)
  const [template, setTemplate] = useState('')
  const [templateLoading, setTemplateLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const sectionsRef = useRef(sections)
  useEffect(() => {
    sectionsRef.current = sections
  }, [sections])

  /** 用后端模板引擎按当前（可未保存）配置生成注入文本 */
  const refreshTemplate = useCallback(async (sec: Record<string, IdentityRawSection>) => {
    if (!sec || Object.keys(sec).length === 0) return
    setTemplateLoading(true)
    try {
      const r = await hubPost<{ template?: string }>('/api/system-prompt/config/preview', {
        config: { sections: sec },
      })
      const text = String((r as { template?: string })?.template ?? '')
      if (mountedRef.current) setTemplate(text)
    } catch {
      // 预览失败（后端瞬时不可达等）：保留最近一次模板，不打断编辑
      if (mountedRef.current) setError('模板预览失败：保持最近一次注入内容（可稍后重试）')
    } finally {
      if (mountedRef.current) setTemplateLoading(false)
    }
  }, [])

  /** 完整载入配置（GET → 立即生成模板） */
  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const resp = unwrapObj<IdentityConfigResponse>(await hubGet('/api/system-prompt/config'))
      const secs = resp.sections ?? {}
      setSections(secs)
      setRegistry(Array.isArray(resp.registry) ? resp.registry : [])
      setStats((resp.stats ?? {}) as Record<string, IdentityStatsItem>)
      setSummary((resp.summary ?? null) as IdentitySummary | null)
      setDirty(false)
      await refreshTemplate(secs)
    } catch (e) {
      setError(`身份提示词配置加载失败：${e instanceof Error ? e.message : String(e)}（请确认后端已启动）`)
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [refreshTemplate])

  useEffect(() => {
    mountedRef.current = true
    void reload()
    return () => {
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [reload])

  /** 编辑防抖：任何启停/文本变化 → 600ms 后重建真实模板（保证预览与注入一致） */
  const schedulePreview = useCallback((sec: Record<string, IdentityRawSection>) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      void refreshTemplate(sec)
    }, PREVIEW_DEBOUNCE_MS)
  }, [refreshTemplate])

  const onToggle = useCallback((key: string) => {
    const prev = sectionsRef.current
    const cur = prev[key]
    if (!cur) return
    const moduleAvailable = ((cur.extra_params ?? {}) as Record<string, unknown>).module_available !== false
    const next = { ...prev, [key]: { ...cur, enabled: moduleAvailable ? !cur.enabled : false } }
    sectionsRef.current = next
    setSections(next)
    setDirty(true)
    schedulePreview(next)
  }, [schedulePreview])

  const onEditContent = useCallback((key: string, content: string) => {
    const prev = sectionsRef.current
    const cur = prev[key]
    if (!cur) return
    const next = { ...prev, [key]: { ...cur, custom_content: content } }
    sectionsRef.current = next
    setSections(next)
    setDirty(true)
    schedulePreview(next)
  }, [schedulePreview])

  const onSave = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setMsg('')
    try {
      await hubPost('/api/system-prompt/config', { sections })
      if (timerRef.current) clearTimeout(timerRef.current)
      setMsg('系统提示词（身份提示词）配置已保存')
      await reload()
    } catch (e) {
      setMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      if (mountedRef.current) setSaving(false)
    }
  }, [sections, saving, reload])

  const onReset = useCallback(async () => {
    try {
      await hubPost('/api/system-prompt/config/reset')
      setMsg('身份提示词已重置为默认')
      await reload()
    } catch (e) {
      setError(`重置失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }, [reload])

  return {
    rows: buildRows(sections, registry, stats),
    rawSections: sections,
    summary,
    template,
    templateLoading,
    loading,
    saving,
    dirty,
    error,
    msg,
    onToggle,
    onEditContent,
    onSave,
    onReset,
    onReload: () => void reload(),
  }
}
