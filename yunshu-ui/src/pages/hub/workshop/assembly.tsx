/**
 * 装配车间 —— 组装台（②）：把一个主权分身"装"出来
 * ------------------------------------------------------------------
 * 【它解决什么】原「创建分身」是一张四格表单，把"主权二十面"压成四个输入框，
 * 其中记忆/工具源还没接线。本视图把装配拆成六步，并**常驻一张主权清单**：
 * 每一面是"拥有 / 声明未接线 / 未做"三态，数据来自后端 `capabilities.py` 投影
 * （`GET /api/subagent/capabilities`）——前端不自己判"是否生效"。
 *
 * 【自解释原则（起源设计 P5 §5.5）】界面显示"实际会怎样"，不是"配置里写了什么"：
 *   · 未接线的字段标黄并给出缺口与补齐阶段（不假装已生效）；
 *   · 装配预览 = 真实创建请求体（同一个 `buildCreatePayload`，可复制、可审计）；
 *   · 点任一面展开"这一项凭什么"（evidence + 证据文件 + gap + 阶段）。
 *
 * 【为什么工具集不在本页选】分身的工具集由**主线档案 + SubAgentToolset** 在委派时装配
 * （`agent/subagent/assembly.py`），`SubagentConfig` 里没有 line 字段（见 docs/分身装配单.md §6）。
 * 在本页给一个"选主线"的下拉会暗示它能决定工具集，那是假的 ⇒ 只作只读说明。
 */
import { useEffect, useMemo, useState } from 'react'
import { ClipboardCopy, Hammer, Loader2, Rocket, ShieldCheck, Users } from 'lucide-react'
import { Badge, Card, ErrorBox, Loading, PageHeader, hubPost } from '../components/ui'
import { SUBAGENT_CREATE, SUBAGENT_LIST } from '@/api/endpoints'
import { getEnvelope } from '@/api/envelope'
import {
  DEFAULT_ROLE_TIERS,
  EMPTY_ASSEMBLE_FORM,
  buildCreatePayload,
  fetchSovereignty,
  type AssembleForm,
  type SovereigntyFace,
  type SovereigntyReport,
} from '@/lib/subagentApi'

interface RoleCatalogLite {
  templates?: { id?: string; title?: string; note?: string }[]
  tiers?: { value?: string; label?: string; red?: boolean }[]
}
interface LlmCatalogLite {
  model?: string
  options?: { model?: string; source?: string }[]
}
interface ListLite { role?: RoleCatalogLite; llm?: LlmCatalogLite }

const PERMISSIONS = ['read', 'write', 'execute', 'network', 'system']

/** 起点模板：只填**已接线**的字段（出现未接线字段即视为假绿，由用例拦） */
const TEMPLATES: { key: string; label: string; hint: string; form: Partial<AssembleForm> }[] = [
  { key: 'blank', label: '空白', hint: '最小权限：只读 + 默认档', form: {} },
  { key: 'code_review', label: '代码审查', hint: '受控角色 + 只读', form: { roleTemplate: 'code_review' } },
  { key: 'research', label: '资料调研', hint: '受控角色 + 只读', form: { roleTemplate: 'research' } },
  { key: 'sandboxed', label: '隔离执行', hint: '短 TTL（执行后端 S5 才可切换，当前如实标注）', form: { ttlSeconds: '60' } },
]

const STATE_COLOR: Record<string, 'green' | 'amber' | 'slate'> = {
  owned: 'green',
  partial: 'amber',
  missing: 'slate',
}

const STEPS = [
  { id: 'step-identity', label: '① 身份与角色' },
  { id: 'step-brain', label: '② 大脑' },
  { id: 'step-memory', label: '③ 记忆' },
  { id: 'step-hands', label: '④ 工具与技能' },
  { id: 'step-boundary', label: '⑤ 权限与边界' },
  { id: 'step-generate', label: '⑥ 生成与带走' },
]

export interface AssemblyConsoleProps {
  /** 生成成功后"去分身列表"的去处（由容器切视图） */
  onShowAgents?: () => void
}

export default function AssemblyConsole({ onShowAgents }: AssemblyConsoleProps) {
  const [form, setForm] = useState<AssembleForm>({ ...EMPTY_ASSEMBLE_FORM, permissions: ['read'] })
  const [roleCatalog, setRoleCatalog] = useState<RoleCatalogLite | null>(null)
  const [llmCatalog, setLlmCatalog] = useState<LlmCatalogLite | null>(null)
  const [sovereignty, setSovereignty] = useState<SovereigntyReport | null>(null)
  const [expanded, setExpanded] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const loadCatalogs = () => {
    getEnvelope<ListLite>(SUBAGENT_LIST).then((d) => {
      setRoleCatalog(d?.role ?? null)
      setLlmCatalog(d?.llm ?? null)
    }).catch((e) => setError(String(e)))
  }

  const loadSovereignty = () => {
    fetchSovereignty()
      .then(setSovereignty)
      .catch((e) => setError(String(e)))
  }

  useEffect(() => {
    setLoading(true)
    Promise.allSettled([
      getEnvelope<ListLite>(SUBAGENT_LIST).then((d) => {
        setRoleCatalog(d?.role ?? null); setLlmCatalog(d?.llm ?? null)
      }),
      fetchSovereignty().then(setSovereignty),
    ]).then((results) => {
      const failed = results.find((r) => r.status === 'rejected')
      if (failed && failed.status === 'rejected') setError(String(failed.reason))
      setLoading(false)
    })
  }, [])

  const payload = useMemo(() => buildCreatePayload(form), [form])
  const previewText = useMemo(() => JSON.stringify(payload, null, 2), [payload])
  const roleTiers = roleCatalog?.tiers?.length ? roleCatalog.tiers : DEFAULT_ROLE_TIERS

  const set = <K extends keyof AssembleForm>(key: K, value: AssembleForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const togglePermission = (p: string) =>
    setForm((f) => ({
      ...f,
      permissions: f.permissions.includes(p)
        ? f.permissions.filter((x) => x !== p)
        : [...f.permissions, p],
    }))

  const applyTemplate = (t: (typeof TEMPLATES)[number]) => {
    setForm({ ...EMPTY_ASSEMBLE_FORM, permissions: ['read'], ...t.form })
    setMessage('')
  }

  const create = async () => {
    setMessage(''); setError(''); setBusy(true)
    try {
      await hubPost(SUBAGENT_CREATE, buildCreatePayload(form))
      setMessage('已生成：' + (form.name.trim() || '(未命名)') + '（可在「分身」视图看到）')
      setForm((f) => ({ ...f, name: '' }))
      loadCatalogs(); loadSovereignty()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }

  const copyPreview = async () => {
    try {
      await navigator.clipboard.writeText(previewText)
      setMessage('装配预览已复制')
    } catch { setMessage('复制失败（浏览器未授权剪贴板）') }
  }

  const jump = (id: string) => {
    document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  if (loading) return <div className="p-6"><Loading text="正在装配…" /></div>

  const templateIds = (roleCatalog?.templates ?? []).map((t) => String(t.id || ''))
  const extraTemplate = form.roleTemplate && !templateIds.includes(form.roleTemplate)

  return (
    <div className="p-6">
      <PageHeader title="组装台" description="按主权二十面拼装一个分身：装什么、缺什么，一目了然" />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Hammer size={14} className="text-cyan-400" />
        <span className="text-xs text-slate-500">起点模板：</span>
        {TEMPLATES.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => applyTemplate(t)}
            title={t.hint}
            data-testid={'assembly-template-' + t.key}
            className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800"
          >{t.label}</button>
        ))}
        <button
          type="button"
          onClick={onShowAgents}
          data-testid="assembly-go-agents"
          className="ml-auto flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800"
        ><Users size={11} /> 去分身列表</button>
      </div>

      {error && <div className="mb-3"><ErrorBox message={error} /></div>}
      {message && (
        <div className="mb-3 rounded-lg border border-emerald-800/60 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300" data-testid="assembly-message">{message}</div>
      )}

      <div className="grid gap-4 lg:grid-cols-[150px_minmax(0,1fr)_300px]">
        {/* 左：步骤导航 */}
        <nav className="flex flex-col gap-1" aria-label="组装步骤">
          {STEPS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => jump(s.id)}
              className="rounded-md px-2 py-1.5 text-left text-[12.5px] text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
            >{s.label}</button>
          ))}
        </nav>

        {/* 中：配置区 */}
        <div className="flex flex-col gap-4">
          <Card title="① 身份与角色">
            <div id="step-identity" className="grid gap-3 md:grid-cols-2">
              <input
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                data-testid="assembly-name"
                placeholder="分身名称 *（唯一）"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
              <select
                value={form.roleTemplate}
                onChange={(e) => set('roleTemplate', e.target.value)}
                data-testid="assembly-role-template"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none"
              >
                <option value="">角色：未装（旧行为）</option>
                {extraTemplate && <option value={form.roleTemplate}>{form.roleTemplate}（目录外）</option>}
                {(roleCatalog?.templates ?? []).map((t) => (
                  <option key={String(t.id)} value={String(t.id)}>{String(t.title || t.id)}</option>
                ))}
              </select>
              <select
                value={form.roleMode}
                onChange={(e) => set('roleMode', e.target.value)}
                data-testid="assembly-role-mode"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none"
              >
                {roleTiers.map((t) => (
                  <option key={String(t.value)} value={String(t.value)}>{t.red ? '⚠ ' : ''}
                    {String(t.label || t.value)}</option>
                ))}
              </select>
              <input
                value={form.roleText}
                onChange={(e) => set('roleText', e.target.value)}
                data-testid="assembly-role-text"
                placeholder="角色自由文本（仅显式选 template+text / full-system 档生效）"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
            </div>
            {/* 自由文本 + 默认档 = 会被后端 400；提前说清，不让使用者白提交（不静默吞掉） */}
            {form.roleText.trim() && form.roleMode === 'template' && (
              <p className="mt-2 text-[11px] text-amber-400/80" data-testid="assembly-role-text-hint">
                自由文本只在 template+text / full-system 档生效；默认档提交会被后端 400（不静默忽略）。
              </p>
            )}
          </Card>

          <Card title="② 大脑">
            <div id="step-brain" className="grid gap-3 md:grid-cols-2">
              <input
                value={form.modelId}
                onChange={(e) => set('modelId', e.target.value)}
                list="assembly-model-options"
                data-testid="assembly-model"
                placeholder={llmCatalog?.model ? '留空 = 跟随母体（' + llmCatalog.model + '）' : '留空 = 跟随母体'}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
              <datalist id="assembly-model-options">
                {(llmCatalog?.options ?? []).map((o) => (
                  <option key={String(o.model)} value={String(o.model ?? '')}>
                    {o.source === 'deployment' ? '部署默认' : '已声明'}</option>
                ))}
              </datalist>
              <input
                value={form.temperature}
                onChange={(e) => set('temperature', e.target.value)}
                inputMode="decimal"
                data-testid="assembly-temperature"
                placeholder="生成温度 0.0–2.0（留空 = 默认，不是 0）"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
            </div>
            <p className="mt-2 text-[11px] text-slate-500">
              执行后端（inproc / subprocess / container）由部署配置决定，不在本页选；
              当前可用性与缺口见右侧「执行后端」面。
            </p>
          </Card>

          <Card title="③ 记忆">
            <div id="step-memory" className="grid gap-3 md:grid-cols-2">
              <input
                value={form.memoryProvider}
                onChange={(e) => set('memoryProvider', e.target.value)}
                data-testid="assembly-memory"
                placeholder="记忆提供商（声明字段）"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
            </div>
            <p className="mt-2 text-[11px] text-amber-400/80" data-testid="assembly-memory-note">
              记忆提供商目前是**声明字段**（无消费者）；记忆档（none/brokered/scoped）由 S3 接线。
            </p>
          </Card>

          <Card title="④ 工具与技能（只读说明）">
            <p id="step-hands" className="text-[12px] text-slate-400">
              分身的工具集由「主线档案 + SubAgentToolset」在**委派时**装配（去 govern、去 §5.7 机制 3 硬禁），
              SubagentConfig 里没有 line 字段，本页不提供"选主线"——那会暗示它能决定工具集。
              如需调整，请到「主线档案」视图。
            </p>
            <input
              value={form.toolSources}
              onChange={(e) => set('toolSources', e.target.value)}
              data-testid="assembly-tools"
              placeholder="工具源（逗号分隔；声明字段，尚未接线）"
              className="mt-3 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
            />
          </Card>

          <Card title="⑤ 权限与边界">
            <div id="step-boundary" className="flex flex-col gap-3">
              <div className="flex flex-wrap gap-1.5">
                {PERMISSIONS.map((p) => {
                  const on = form.permissions.includes(p)
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() => togglePermission(p)}
                      data-testid={'assembly-permission-' + p}
                      aria-pressed={on}
                      className={on
                        ? 'rounded-full border border-cyan-700 bg-cyan-500/15 px-2.5 py-1 text-xs text-cyan-300'
                        : 'rounded-full border border-slate-700 px-2.5 py-1 text-xs text-slate-400'}
                    >{p}</button>
                  )
                })}
              </div>
              <input
                value={form.ttlSeconds}
                onChange={(e) => set('ttlSeconds', e.target.value)}
                inputMode="numeric"
                data-testid="assembly-ttl"
                placeholder="TTL 秒（留空 = 永久；委派时后端按契约⑦收敛）"
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
              />
              <p className="text-[11px] text-slate-500">
                权限交给容器沙箱（Sandbox.allowed_permissions）；自主权 L1–L5 与审批在派发侧生效，
                尚未进 SubagentConfig（见右侧「自主权分级」面）。
              </p>
            </div>
          </Card>

          <Card title="⑥ 生成与带走">
            <div id="step-generate" className="flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <pre
                  data-testid="assembly-preview"
                  className="max-h-56 flex-1 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-[11px] leading-5 text-slate-300"
                >{previewText}</pre>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={copyPreview}
                  data-testid="assembly-copy"
                  className="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                ><ClipboardCopy size={12} /> 复制预览</button>
                <button
                  type="button"
                  onClick={create}
                  disabled={busy || !form.name.trim()}
                  data-testid="assembly-create"
                  className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500 disabled:opacity-40"
                >{busy ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />} 生成分身</button>
                <span className="text-[11px] text-slate-600">预览即真实请求体（同一序列化函数）</span>
              </div>
              <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-[11px] text-slate-500">
                可带走（bundle 导出/导入 + 离线运行）尚未实现 —— 见右侧「可带走 bundle」面，S5 交付。
              </div>
            </div>
          </Card>
        </div>

        {/* 右：主权清单 */}
        <aside className="flex flex-col gap-3" data-testid="sovereignty-panel">
          <Card title="主权清单">
            {sovereignty ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-xs text-slate-400" data-testid="sovereignty-summary">
                  <ShieldCheck size={13} className="text-emerald-400" />
                  拥有 {sovereignty.summary.owned} · 声明未接线 {sovereignty.summary.partial} · 未做 {sovereignty.summary.missing}
                  <Badge color="cyan">{sovereignty.maturity.current}</Badge>
                </div>
                <p className="text-[11px] text-slate-500">点任一面看"凭什么"（证据 / 缺口 / 补齐阶段）</p>
                {sovereignty.layers.map((layer) => (
                  <div key={layer.key} className="flex flex-col gap-1">
                    <div className="mt-1 text-[11px] font-medium text-slate-500">{layer.label} · {layer.question}</div>
                    {layer.faces.map((key) => {
                      const face = sovereignty.faces.find((f) => f.key === key)
                      if (!face) return null
                      return <FaceRow key={key} face={face} expanded={expanded === key} onToggle={() => setExpanded(expanded === key ? '' : key)} />
                    })}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-slate-500">主权清单不可用（后端投影未就绪）</p>
            )}
          </Card>
        </aside>
      </div>
    </div>
  )
}

function FaceRow({ face, expanded, onToggle }: { face: SovereigntyFace; expanded: boolean; onToggle: () => void }) {
  return (
    <div data-testid={'sovereignty-face-' + face.key}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-[12px] text-slate-300 hover:bg-slate-800/60"
      >
        <span>{face.label}</span>
        <Badge color={STATE_COLOR[face.state] ?? 'slate'}>{face.state_label}</Badge>
      </button>
      {expanded && (
        <div className="mb-1 rounded-md border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px] leading-5 text-slate-400">
          <div>{face.evidence}</div>
          {face.evidence_files.length > 0 && (
            <div className="mt-1 font-mono text-[10px] text-slate-500">{face.evidence_files.join('  ')}</div>
          )}
          {face.gap && <div className="mt-1 text-amber-400/80">缺口：{face.gap}</div>}
          {face.next_stage && <div className="text-slate-500">计划：{face.next_stage}</div>}
        </div>
      )}
    </div>
  )
}