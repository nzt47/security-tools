/**
 * SubagentRoleEditor —— 「编辑已有分身的角色」弹窗（2026-11-22）
 * ------------------------------------------------------------------
 * 【它解决什么】角色三字段（role_template / role_mode / role_text）此前只在**创建**时能填：
 * 建完才发现角色选错，只能销毁重建（还会丢掉上下文与记忆增量）。后端
 * `POST /api/subagent/<name>/reload` 早已支持热更新这三字段（`_role_fields` 复用
 * `resolve_subagent_role` 校验、未传字段沿用存活容器现值、非法组合当场 400），
 * 缺的只是一个入口 —— 本弹窗**只做入口，不新增写路由**（多一条 PUT 就是第二条同义写路径）。
 *
 * 【三条不可省的诚实规则（都有用例）】
 *  1. **正文不回显**：`GET /api/subagent/list` 的 role 段刻意不含 role_text（列表不携带自由文本）。
 *     因此留空 = **不发这个键 = 保留现有正文**；要清空必须显式勾选 —— 靠"留空"猜意图
 *     等于替使用者把一段他看不见的正文删掉。
 *  2. **切回 template 档会自动清空正文**：默认档配非空 role_text 后端必 400；
 *     不显式发空串就永远切不回"纯模板"。
 *  3. **红档（full-system）二次确认**：自由文本进 system prompt 是安全姿态变更，
 *     不点确认不发请求（Cancel 什么都不变）。
 *
 * 【为什么不放进 pages/**】`reports/ui_reachability.json` 按页面文件集派生，弹窗组件放
 * `src/components/`（口径同 TransformQueueDialog）；页面只负责打开它，并把真正的
 * reload 请求以 `onSubmit` 注入（组件不认识 HTTP 层）。
 */
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Checkbox, ModalBase, Notice, Select, Textarea } from '@/components/ui'
import ConfirmDialog from '@/components/ConfirmDialog'
import { DEFAULT_ROLE_TIERS, buildRoleUpdatePayload, type RoleDraft } from '@/lib/subagentApi'

/** 逐分身的角色生效投影（后端 `resolve_subagent_role(...).to_dict()`，**不含正文**） */
export interface RoleEditorRole {
  template?: string
  tier?: string
  source?: string
  fragment_chars?: number
  constraints?: number
  audit_required?: boolean
  red?: boolean
  error?: string
}

/** 一套身份提示词方案（`role_prompt_library.prompt_sets_catalog()` 的一项） */
export interface RolePromptSet {
  id?: string
  title?: string
  scene?: string
  body?: string
  chars?: number
}

/** 部署级角色事实（`GET /api/subagent/list` 的 role 段，后端 `role_catalog()`） */
export interface RoleEditorCatalog {
  templates?: { id?: string; title?: string; body?: string; note?: string }[]
  tiers?: { value?: string; label?: string; semantics?: string; red?: boolean; audit?: boolean }[]
  default_tier?: string
  role_text_max_chars?: number
  /** 按角色类型分组的方案库（2026-11-21 起随 role 段返回） */
  prompt_sets?: { role_id?: string; role_title?: string; sets?: RolePromptSet[] }[]
}

export interface SubagentRoleEditorProps {
  /** 目标分身名（写进标题；请求 URL 由页面注入的 onSubmit 决定） */
  name: string
  open: boolean
  /** 该分身**当前**的角色投影（用来预填模板/档位，并显示"当前生效"） */
  role?: RoleEditorRole
  /** 部署级词表 + 方案库（预填选项；取不到时回落 DEFAULT_ROLE_TIERS） */
  catalog?: RoleEditorCatalog
  onClose: () => void
  /** 提交（页面注入：`hubPost(subagentReload(name), payload)`）；ok=false 视为业务拒绝 */
  onSubmit: (payload: Record<string, unknown>) => Promise<{ ok?: boolean; error?: string } | null>
  /** 保存成功后的回调（页面据此重拉列表，回读生效徽章） */
  onSaved?: () => void
}

export default function SubagentRoleEditor({
  name, open, role, catalog, onClose, onSubmit, onSaved,
}: SubagentRoleEditorProps) {
  const [template, setTemplate] = useState('')
  const [mode, setMode] = useState('template')
  const [text, setText] = useState('')
  const [clearText, setClearText] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [confirmRed, setConfirmRed] = useState(false)

  // 每次打开都从该分身的**当前**投影重新预填（正文刻意不回显 ⇒ 永远是空 + "保留"语义）
  useEffect(() => {
    if (!open) return
    setTemplate(String(role?.template || ''))
    setMode(String(role?.tier || 'template'))
    setText('')
    setClearText(false)
    setErr('')
    setMsg('')
    setSaving(false)
    setConfirmRed(false)
  }, [open, role?.template, role?.tier])

  const maxChars = catalog?.role_text_max_chars ?? 2000
  const tiers = catalog?.tiers?.length ? catalog.tiers : DEFAULT_ROLE_TIERS
  const hasTemplate = template.trim() !== ''
  const textDisabled = !hasTemplate || mode === 'template'
  const trimmed = text.trim()
  const tooLong = trimmed.length > maxChars
  const templateTitle = (catalog?.templates ?? []).find((t) => t.id === template)?.title || template || '未装'
  const currentTier = String(role?.tier || 'template')
  const currentTierLabel = tiers.find((t) => t.value === currentTier)?.label || currentTier

  // 方案库：只显示与当前模板同一角色类型的成稿（与组装台同源，不维护第二份清单）
  const sets = useMemo(
    () => (catalog?.prompt_sets ?? []).find((g) => g.role_id === template)?.sets ?? [],
    [catalog, template],
  )

  const applySet = (s: RolePromptSet) => {
    const body = String(s.body || '')
    setText(body)
    setClearText(false)
    // 默认档下非空正文会被后端 400：点方案 = 明确要用这段正文 ⇒ 抬到 template+text。
    // 已是 full-system 的保持不动（不偷偷降档）。
    if (mode === 'template') setMode('template+text')
    setMsg('已套用方案「' + (s.title || s.id || '') + '」' +
      (mode === 'template' ? '，档位已切到「模板 + 自由文本（进约束）」' : ''))
  }

  const submit = async () => {
    setErr('')
    if (tooLong) {
      setErr('自由文本超长（' + trimmed.length + ' > ' + maxChars + ' 字符）：不静默截断，请精简')
      return
    }
    const draft: RoleDraft = { template, mode, text, clearText }
    setSaving(true)
    try {
      const r = await onSubmit(buildRoleUpdatePayload(draft))
      if (r && r.ok === false) {
        setErr('保存失败：' + (r.error || '后端返回 ok=false'))
        return
      }
      onSaved?.()
      onClose()
    } catch (e) {
      setErr('保存失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setSaving(false)
      setConfirmRed(false)
    }
  }

  // 红档不直接发：先弹确认，确认后才真提交
  const requestSave = () => {
    if (mode === 'full-system' && hasTemplate) { setConfirmRed(true); return }
    void submit()
  }

  const textHint = !hasTemplate
    ? '未装角色：先选一个受控模板，自由文本才有可依附的来源（后端会 400）'
    : mode === 'template'
      ? '受控模板档不使用自由文本：保存会清空该分身现有的自由文本（否则后端 400）'
      : '留空 = 保留现有正文（不回显）；输入即替换'

  return (
    <>
      <ModalBase
        open={open}
        onClose={onClose}
        title={'编辑角色 · ' + name}
        width="max-w-2xl"
        closeButton
        closeOnEsc={!confirmRed}
        closeOnMask={!confirmRed}
        testId="role-editor"
        footer={(
          <>
            <Button variant="default" onClick={onClose} disabled={saving}>取消</Button>
            <Button variant="primary" onClick={requestSave} loading={saving} data-testid="role-editor-save">
              保存角色
            </Button>
          </>
        )}
      >
        <div className="flex flex-col gap-3">
          <p className="text-caption text-slate-400" data-testid="role-editor-current">
            当前：{templateTitle} · {currentTierLabel}
            {' · '}片段 {Number(role?.fragment_chars ?? 0)} 字
            {' · '}约束 {Number(role?.constraints ?? 0)} 行
            {role?.red ? <Badge tone="danger" size="sm" className="ml-1">红档</Badge> : null}
            {role?.audit_required ? <Badge tone="warning" size="sm" className="ml-1">需审计</Badge> : null}
          </p>
          {role?.error ? (
            <Notice tone="danger" size="sm" data-testid="role-editor-current-error">
              当前配置在委派时会被拒：{role.error}
            </Notice>
          ) : null}
          <p className="text-caption text-slate-500">
            自由文本正文不回显（列表载荷刻意不携带正文）：<b>留空 = 保留现有正文</b>；输入即替换。
            只发角色三字段，其余配置沿用该分身现值。
          </p>

          <div className="grid gap-3 md:grid-cols-2">
            <Select
              label="角色模板"
              size="md"
              value={template}
              onChange={(v) => {
                setTemplate(v)
                setMsg('')
                if (!v) { setMode('template'); setText(''); setClearText(false) }
              }}
              placeholder="（不装角色：不注入角色片段）"
              placeholderOptionDisabled={false}
              data-testid="role-editor-template"
              options={(catalog?.templates ?? []).map((t) => ({
                label: String(t.title || t.id), value: String(t.id),
              }))}
            />
            <Select
              label="角色档位"
              size="md"
              value={mode}
              onChange={(v) => {
                setMode(v)
                setMsg('')
                // 切回默认档 ⇒ 正文已不生效；清空标记与输入一并复位（保存时按 template 档发空串）
                if (v === 'template') { setText(''); setClearText(false) }
              }}
              hidePlaceholder
              disabled={!hasTemplate}
              data-testid="role-editor-mode"
              options={tiers.map((t) => ({
                label: (t.red ? '⚠ ' : '') + String(t.label || t.value), value: String(t.value),
              }))}
            />
          </div>

          <div>
            <Textarea
              value={text}
              onChange={(e) => { setText(e.target.value); setClearText(false); setMsg(''); setErr('') }}
              data-testid="role-editor-text"
              aria-label="角色自由文本"
              rows={5}
              spellCheck={false}
              disabled={textDisabled}
              error={tooLong}
              placeholder={textDisabled
                ? textHint
                : '留空 = 保留现有自由文本；输入即替换'}
            />
            <div className="mt-1 flex items-start justify-between gap-3 text-caption text-slate-500">
              <span data-testid="role-editor-text-hint">{textHint}</span>
              <span className="shrink-0" data-testid="role-editor-count">{trimmed.length} / {maxChars}</span>
            </div>
          </div>

          {hasTemplate && mode !== 'template' && (
            <Checkbox
              size="sm"
              checked={clearText}
              disabled={trimmed.length > 0}
              onChange={(e) => setClearText(e.target.checked)}
              data-testid="role-editor-clear"
              label="清空现有自由文本（不回显；勾选即发送空串）"
            />
          )}

          {sets.length > 0 && (
            <div>
              <p className="text-caption text-slate-500">
                身份提示词方案（{templateTitle}）：点一套填入上面的自由文本
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {sets.map((s) => (
                  <Button
                    key={s.id}
                    type="button"
                    size="xs"
                    variant={trimmed !== '' && trimmed === String(s.body || '').trim() ? 'primary' : 'default'}
                    title={s.scene ?? ''}
                    data-testid={'role-editor-set-' + s.id}
                    onClick={() => applySet(s)}
                  >
                    {s.title}（{s.chars ?? String(s.body || '').length} 字）
                  </Button>
                ))}
              </div>
            </div>
          )}

          {msg && <Notice tone="success" size="sm" data-testid="role-editor-msg">{msg}</Notice>}
          {err && <Notice tone="danger" size="sm" data-testid="role-editor-error">{err}</Notice>}
        </div>
      </ModalBase>

      <ConfirmDialog
        open={confirmRed}
        title="红档：自由文本进系统提示词"
        message={'保存后，该分身的自由文本（现有或刚输入的这段）会直接拼进 system prompt —— '
          + '这会打破「外来文本不进 system prompt」的保护；该档每次实跑都会留一条审计。确定保存？'}
        details={['档位：full-system（红档）', '模板：' + templateTitle]}
        confirmText="保存为红档"
        danger
        loading={saving}
        onConfirm={() => void submit()}
        onCancel={() => setConfirmRed(false)}
      />
    </>
  )
}