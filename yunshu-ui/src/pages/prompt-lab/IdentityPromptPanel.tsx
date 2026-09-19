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
 * 启用状态 ↔ 内容显示联动（按需实现）：
 *   - 面板项**启用**时，其「发出内容」就地显示（例：技能指令启用 → {skill_instructions}）；
 *     停用则不显示发出内容（该节不参与组装）。
 *   - 鼠标悬停某面板项 → 右侧/预览区「系统提示词」按同一份发出内容高亮定位
 *     （onFocusRow 上抛 key，由页面把 emitText 传给 PreviewPanel.highlightSnippet）。
 * 排序：行序由 identityPrompt.buildRows 按「发出顺序」（模板中的注入位置）给出。
 */
import { Loader2, RefreshCw, RotateCcw, Save, TriangleAlert, Zap } from 'lucide-react'
import type { IdentityRow, IdentitySummary, UseIdentityPromptResult } from './identityPrompt'

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
    onFocusRow, focusedKey,
  } = props
  const enabledCount = rows.filter((r) => r.enabled).length
  const busy = loading || saving || applying
  // 发出序号：仅统计真正发出的节（面板项按发出顺序排列，序号即发出次序）
  const emittedOrder = new Map<string, number>()
  rows.filter((r) => r.emitted).forEach((r, i) => emittedOrder.set(r.key, i + 1))

  return (
    <section className="pl-category pl-identity">
      <h2 className="pl-category-title" style={{ color: '#22d3ee' }}>
        <span className="pl-category-dot" style={{ background: '#22d3ee' }} />
        身份提示词（系统提示词 · 线上配置）
        <span className="pl-category-count">
          {loading ? '载入中…' : `${rows.length} 节 · 启用 ${enabledCount} 节 · 已发出 ${emittedOrder.size} 节`}
        </span>
      </h2>
      <p className="pl-category-desc">
        原工作台「人格与提示词 → 身份提示词」并入。<b>面板顺序 = 发出顺序</b>（该节被拼进
        system message 的先后）＝ DeepSeek 前缀缓存命中顺序（稳定节前置、易变节后置）。
        某节<b>启用</b>后其「发出内容」会就地显示在卡片内，并在右侧「系统提示词」区域高亮定位。
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
        <button type="button" className="pl-btn" onClick={onReload} disabled={busy} title="从后端重新载入身份提示词配置">
          <RefreshCw size={12} /> 刷新
        </button>
        <button type="button" className="pl-btn" onClick={onReset} disabled={busy} title="恢复默认身份提示词配置">
          <RotateCcw size={12} /> 恢复默认
        </button>
        <button type="button" className="pl-btn" onClick={onSave} disabled={busy} title="仅写入配置库（system_prompt_config.json），不更新运行时模板">
          <Save size={12} /> {saving ? '保存中…' : '保存配置'}
        </button>
        <button
          type="button"
          className="pl-btn apply"
          onClick={onApply}
          disabled={busy}
          title="保存并按 registry 顺序组装模板，写入运行时 data/system_prompt.txt，后续 LLM 调用立即生效"
        >
          {applying ? <Loader2 size={12} className="spin" /> : <Zap size={12} />}
          {applying ? '应用中…' : '应用到运行时'}
        </button>
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
                    <button
                      type="button"
                      className={`pl-toggle ${row.enabled ? 'on' : ''}`}
                      aria-pressed={row.enabled}
                      disabled={row.moduleBadge && !row.moduleAvailable}
                      title={
                        row.moduleBadge && !row.moduleAvailable
                          ? '对应 V2 模块未安装，无法启用'
                          : row.enabled
                            ? '停用该节（不再注入）'
                            : '启用该节'
                      }
                      onClick={() => onToggle(row.key)}
                    >
                      <span className="pl-toggle-dot" />
                      {row.enabled ? '启用' : '停用'}
                    </button>
                  </div>
                </div>
                {row.description && <p className="pl-factor-desc">{row.description}</p>}

                {row.editable ? (
                  <textarea
                    className={`pl-textarea pl-syspart-text ${row.enabled ? '' : 'disabled'}`}
                    rows={3}
                    value={row.customContent}
                    spellCheck={false}
                    placeholder="留空使用该节默认注入内容；填写后以自定义内容替代。"
                    onChange={(e) => onEditContent(row.key, e.target.value)}
                  />
                ) : (
                  <div className="pl-syspart-status">
                    {row.enabled ? (
                      <span>已启用（运行时自动注入）</span>
                    ) : (
                      <span>已停用（不参与组装）</span>
                    )}
                    {row.tokenLimit > 0 && <span className="pl-syspart-limit">token 上限 {row.tokenLimit.toLocaleString()}</span>}
                    {row.note && <span className="pl-syspart-note">{row.note}</span>}
                  </div>
                )}

                {/* 启用状态 ↔ 内容显示联动：启用后就地显示该节真正发出的内容 */}
                {row.enabled && row.emitText && (
                  <div className="pl-emit" title="该节启用后拼进请求的内容（与右侧提示词区域一致）">
                    <span className="pl-emit-label">
                      发出内容{row.emitStageLabel ? ` · ${row.emitStageLabel}` : ''}
                    </span>
                    <pre className="pl-emit-text">{row.emitText}</pre>
                  </div>
                )}
                {!row.enabled && (row.emitText || row.editable) && (
                  <div className="pl-emit muted" title="该节当前未启用，不参与请求组装">
                    <span className="pl-emit-label">未发出</span>
                    <pre className="pl-emit-text">{row.emitText || '（启用后在此显示该节发出的内容）'}</pre>
                  </div>
                )}

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
    </section>
  )
}
