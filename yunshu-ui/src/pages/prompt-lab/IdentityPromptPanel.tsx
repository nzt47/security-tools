/**
 * IdentityPromptPanel —— 并入提示词实验室的「身份提示词」编辑区
 * ------------------------------------------------------------------
 * 原工作台「人格与提示词 → 身份提示词」页面功能收拢于此：
 *   - 逐节启停 / 可编辑节自定义内容（custom_content）
 *   - 服务端 Token 统计摘要（总计/启用/停用）与逐节估算
 *   - 保存（写入线上配置）/ 恢复默认 / 重新载入
 * 数据与状态全部由 useIdentityPrompt（identityPrompt.ts）管理，本组件仅渲染。
 * 卡片样式沿用实验室 .pl- 风格。
 *
 * 启用状态 ↔ 右侧预览联动（2026-11-16 Owner 改）：
 *   - 卡片内**不再**重复预览「发出内容」：右侧「系统提示词（身份提示词 · 注入）」的实时预览
 *     已经是同一份文本，卡片里再放一份既占版面又容易和它不一致。
 *     卡片只保留可编辑节的自定义内容编辑框（那是**输入**，不是预览）。
 *   - 鼠标悬停某面板项 → 右侧「系统提示词」按该节发出内容高亮定位
 *     （onFocusRow 上抛 key，由页面把 emitText 传给 PreviewPanel.highlightSnippet）。
 * 排序：行序由 identityPrompt.buildRows 按「发出顺序」（模板中的注入位置）给出。
 */
import { useState } from 'react'
import { Check, Copy, Loader2, RefreshCw, RotateCcw, Save, TriangleAlert, Zap } from 'lucide-react'
import { SectionTitle, Switch, Textarea, Button} from '@/components/ui'
import type {
  IdentityRow, IdentitySummary, RolePromptSet, RolePromptSetGroup, UseIdentityPromptResult,
} from './identityPrompt'

export interface IdentityPromptPanelProps {
  rows: IdentityRow[]
  summary: IdentitySummary | null
  loading: boolean
  saving: boolean
  applying: boolean
  dirty: boolean
  error: string
  msg: string
  onToggle: (key: string) => void
  onEditContent: (key: string, content: string) => void
  onSave: () => void
  /** 保存并写入运行时模板（data/system_prompt.txt），立即对后续 LLM 调用生效 */
  onApply: () => void
  onReset: () => void
  onReload: () => void
  /** 悬停/聚焦某面板项（null = 取消）；页面据此在提示词区域高亮该节的发出内容 */
  onFocusRow?: (key: string | null) => void
  /** 当前聚焦的面板项 key */
  focusedKey?: string | null
  /**
   * 分身角色方案（按角色类型，每类多套身份提示词）——来自 GET /api/subagent/list 的 role.prompt_sets。
   * 空数组/未传 = 不渲染这一块（取不到只是少一块参考模板，不在身份提示词面板里报错）。
   */
  roleSets?: RolePromptSetGroup[]
}

/** 由 Hook 结果构造面板 props（组件不直接依赖 Hook 类型，便于测试/复用） */
export function toIdentityPromptPanelProps(r: UseIdentityPromptResult): Omit<IdentityPromptPanelProps, 'onFocusRow' | 'focusedKey'> {
  return {
    rows: r.rows,
    summary: r.summary,
    loading: r.loading,
    saving: r.saving,
    applying: r.applying,
    dirty: r.dirty,
    error: r.error,
    msg: r.msg,
    onToggle: r.onToggle,
    onEditContent: r.onEditContent,
    onSave: r.onSave,
    onApply: r.onApply,
    onReset: r.onReset,
    onReload: r.onReload,
  }
}

export default function IdentityPromptPanel(props: IdentityPromptPanelProps) {
  const {
    rows, summary, loading, saving, applying, dirty, error, msg,
    onToggle, onEditContent, onSave, onApply, onReset, onReload,
    onFocusRow, focusedKey, roleSets = [],
  } = props
  const enabledCount = rows.filter((r) => r.enabled).length
  const busy = loading || saving || applying
  // 发出序号：仅统计真正发出的节（面板项按发出顺序排列，序号即发出次序）
  const emittedOrder = new Map<string, number>()
  rows.filter((r) => r.emitted).forEach((r, i) => emittedOrder.set(r.key, i + 1))

  return (
    <section className="pl-category pl-identity">
      <SectionTitle
        level={2}
        actions={
          <span className="pl-category-count">
            {loading ? '载入中…' : `${rows.length} 节 · 启用 ${enabledCount} 节 · 已发出 ${emittedOrder.size} 节`}
          </span>
        }
      >
        身份提示词（系统提示词 · 线上配置）
      </SectionTitle>
      <p className="pl-category-desc">
        原工作台「人格与提示词 → 身份提示词」并入。<b>面板顺序 = 发出顺序</b>（该节被拼进
        system message 的先后）＝ DeepSeek 前缀缓存命中顺序（稳定节前置、易变节后置）。
        某节<b>启用</b>后，其「发出内容」只在右侧「系统提示词」区域显示；悬停卡片即可在那段高亮定位
        （卡片内不再重复预览 —— 右侧实时预览已是同一份文本）。
        <b>保存</b> 仅写配置库；<b>应用</b> 再按 registry 顺序组装并写入运行时
        <code>data/system_prompt.txt</code>，后续每次 LLM 调用立即生效。
      </p>

      {!loading && summary && (
        <div className="pl-id-summary">
          <div className="pl-id-stat">
            <span>总计 Token（含 tools/基线）</span>
            <b>{summary.grand_total ?? 0}</b>
          </div>
          <div className="pl-id-stat">
            <span>启用 Token</span>
            <b className="ok">{summary.total_enabled_tokens ?? 0}</b>
          </div>
          <div className="pl-id-stat">
            <span>停用节数</span>
            <b>{summary.total_disabled_count ?? 0}</b>
          </div>
        </div>
      )}

      <div className="pl-id-toolbar">
        <Button type="button" variant="default" size="sm" onClick={onReload} disabled={busy} title="从后端重新载入身份提示词配置">
          <RefreshCw size={12} /> 刷新
        </Button>
        <Button type="button" variant="default" size="sm" onClick={onReset} disabled={busy} title="恢复默认身份提示词配置">
          <RotateCcw size={12} /> 恢复默认
        </Button>
        <Button type="button" variant="default" size="sm" onClick={onSave} disabled={busy} title="仅写入配置库（system_prompt_config.json），不更新运行时模板">
          <Save size={12} /> {saving ? '保存中…' : '保存配置'}
        </Button>
        <Button
          type="button"
          variant="primary" size="sm"
          onClick={onApply}
          disabled={busy}
          title="保存并按 registry 顺序组装模板，写入运行时 data/system_prompt.txt，后续 LLM 调用立即生效"
        >
          {applying ? <Loader2 size={12} className="spin" /> : <Zap size={12} />}
          {applying ? '应用中…' : '应用到运行时'}
        </Button>
        {/* 【2026-11-10】身份提示词是 system message 的稳定前缀段：一改就会让缓存前缀整段失效一次，
            这里是唯一会「赔一次全价」的动作，必须在按钮旁说清楚（§11.81 新条目 lab-apply-cache-notice）。 */}
        <span className="pl-id-cache-note"
          style={{ marginLeft: 8, fontSize: 11, color: '#94a3b8' }}
          title="身份提示词位于 system message 的稳定前缀：一旦改变，下一次 LLM 调用无法复用旧前缀，整段按未命中计价（只赔一次，之后重新稳定）。">
          ⓘ 改身份 = 下一次调用整段前缀失效一次（按未命中计价）
        </span>
        {dirty && <span className="pl-id-dirty">● 有未保存修改</span>}
      </div>

      {error && <p className="pl-error">{error}</p>}
      {msg && <p className="pl-id-msg">{msg}</p>}

      {loading ? (
        <div className="pl-id-loading">
          <Loader2 size={14} className="spin" /> 正在载入线上身份提示词配置…
        </div>
      ) : rows.length === 0 ? (
        <p className="pl-error">未获取到任何提示词节（后端无配置或接口不可达）。</p>
      ) : (
        <div className="pl-card-grid">
          {rows.map((row) => {
            const seq = emittedOrder.get(row.key)
            return (
              <div
                key={row.key}
                className={`pl-factor-card pl-syspart ${row.emitted ? 'emitted' : ''} ${focusedKey === row.key ? 'focused' : ''}`}
                onMouseEnter={() => onFocusRow?.(row.key)}
                onMouseLeave={() => onFocusRow?.(null)}
                onFocus={() => onFocusRow?.(row.key)}
                onBlur={() => onFocusRow?.(null)}
              >
                <div className="pl-factor-head">
                  <span className="pl-factor-name">
                    {row.emitted && seq != null && <em className="pl-emit-seq" title={`发出顺序 #${seq}`}>#{seq}</em>}
                    {row.label}
                    {row.editable && <em className="pl-editable-tag">可编辑</em>}
                  </span>
                  <div className="flex items-center gap-2">
                    {row.estimate > 0 && (
                      <span className="pl-token-chip" title={row.range ? `估算范围 ${row.range} tok` : `估算 ~${row.estimate} tok`}>
                        ~{row.estimate} tok{row.range ? ` · ${row.range}` : ''}
                      </span>
                    )}
                    <Switch
                      checked={row.enabled}
                      onCheckedChange={() => onToggle(row.key)}
                      size="sm"
                      disabled={row.moduleBadge && !row.moduleAvailable}
                      label={
                        row.moduleBadge && !row.moduleAvailable
                          ? `${row.label}：对应 V2 模块未安装，无法启用`
                          : `${row.label}：${row.enabled ? '已开启' : '已关闭'}`
                      }
                    />
                  </div>
                </div>
                {row.description && <p className="pl-factor-desc">{row.description}</p>}

                {row.editable ? (
                  <Textarea
                    className={`mt-2 ${row.enabled ? '' : 'opacity-45'}`}
                    rows={3}
                    value={row.customContent}
                    spellCheck={false}
                    placeholder="留空使用该节默认注入内容；填写后以自定义内容替代。"
                    onChange={(e) => onEditContent(row.key, e.target.value)}
                  />
                ) : (
                  <>
                    {/* 【2026-11-16】不再写「已启用（运行时自动注入）/ 已停用（不参与组装）」：
                        开关本身就是状态（开=亮、关=灰），那行等于第二列状态。只留 token 上限与备注。 */}
                    {(row.tokenLimit > 0 || row.note) && (
                      <div className="pl-syspart-status">
                        {row.tokenLimit > 0 && (
                          <span className="pl-syspart-limit">token 上限 {row.tokenLimit.toLocaleString()}</span>
                        )}
                        {row.note && <span className="pl-syspart-note">{row.note}</span>}
                      </div>
                    )}
                  </>
                )}

                {/* 【2026-11-16 Owner】卡片内不再预览「发出内容」：右侧实时预览里有同一份文本，
                    这里只保留悬停→右侧高亮（onFocusRow 链路未变）。 */}

                {row.moduleBadge && !row.moduleAvailable && (
                  <p className="pl-id-warn">
                    <TriangleAlert size={11} /> 对应 V2 模块未安装，服务端将保持停用
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
      <div className="pl-syspart-actions">
        <span className="pl-id-hint">
          改动后需「保存配置」；要让线上真正生效请点「应用到运行时」（后端按 registry 顺序组装并写入
          data/system_prompt.txt）。注入模板段序 = DeepSeek 前缀缓存命中顺序（稳定前置/易变后置）。
        </span>
      </div>

      {roleSets.length > 0 && <RolePromptSetSection roleSets={roleSets} />}
    </section>
  )
}

/**
 * 分身角色方案：按角色类型列出多套**可整段复制**的身份提示词。
 *
 * 【为什么放在身份提示词面板里】分身的 role_text 就是「身份提示词」的另一种宿主：
 * 主智能体那套是逐节配置（线上 system prompt），分身那套是整段粘贴（role_text，≤2000 字）。
 * 两者放同一页，用户找"给分身写身份"时不会跑去别处。
 *
 * 【为什么不直接写入分身】创建/编辑分身有独立的权限与审计链路（role_mode 档位、
 * red 档需审计），从提示词页越权改分身会绕开那条链路。这里只提供成稿 + 复制。
 */
function RolePromptSetSection({ roleSets }: { roleSets: RolePromptSetGroup[] }) {
  const [roleId, setRoleId] = useState('')
  const [copied, setCopied] = useState('')
  const [copyErr, setCopyErr] = useState('')
  const active = roleSets.find((g) => g.role_id === roleId) ?? roleSets[0]
  const total = roleSets.reduce((n, g) => n + g.sets.length, 0)

  const copy = async (s: RolePromptSet) => {
    setCopyErr('')
    try {
      await navigator.clipboard.writeText(s.body)
      setCopied(s.id)
      window.setTimeout(() => setCopied((cur) => (cur === s.id ? '' : cur)), 1800)
    } catch {
      // 复制失败不假装成功：让用户手选文本框里的内容
      setCopyErr('浏览器不允许写剪贴板：请点开下方文本框全选复制。')
    }
  }

  return (
    <div className="pl-role-sets mt-6">
      <SectionTitle level={2} actions={<span className="pl-category-count">{roleSets.length} 类角色 · {total} 套</span>}>
        分身角色方案（按角色类型）
      </SectionTitle>
      <p className="pl-category-desc">
        分身创建时可从这里挑一套<b>角色说明</b>。先选角色类型，再选方案；每套都是自足的通用提示词，
        <b>复制</b>后粘到「分身 → 角色说明」，档位选「模板+说明」（正文不能超过 2000 字符，后端会拒）。
        这里只提供成稿，不会改动任何分身。
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-1.5" role="group" aria-label="分身角色类型">
        {roleSets.map((g) => (
          <Button
            key={g.role_id}
            type="button"
            size="xs"
            variant={g.role_id === active?.role_id ? 'primary' : 'default'}
            aria-pressed={g.role_id === active?.role_id}
            onClick={() => { setRoleId(g.role_id); setCopied(''); setCopyErr('') }}
          >
            {g.role_title}
          </Button>
        ))}
      </div>

      {active?.role_note && <p className="pl-factor-desc mt-2">{active.role_note}</p>}
      {copyErr && <p className="pl-error">{copyErr}</p>}

      <div className="pl-card-grid mt-2">
        {active?.sets.map((s) => (
          <div key={s.id} className="pl-factor-card" data-cp-role-set={s.id}>
            <div className="pl-factor-head">
              <span className="pl-factor-name">{s.title}</span>
              <span className="pl-token-chip" title="正文字符数（role_text 上限 2000）">
                {s.chars ?? s.body.length} 字
              </span>
            </div>
            <p className="pl-factor-desc">{s.scene}</p>
            <Textarea
              className="mt-2"
              rows={7}
              value={s.body}
              readOnly
              spellCheck={false}
              aria-label={s.title + ' 角色提示词正文'}
            />
            <div className="mt-2 flex items-center gap-2">
              <Button type="button" size="sm" variant="default" onClick={() => void copy(s)}>
                {copied === s.id ? <Check size={12} /> : <Copy size={12} />}
                {copied === s.id ? '已复制' : '复制'}
              </Button>
              <span className="pl-id-hint">粘到「分身 → 角色说明」</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
