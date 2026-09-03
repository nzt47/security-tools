/**
 * IdentityPromptPanel —— 并入提示词实验室的「身份提示词」编辑区
 * ------------------------------------------------------------------
 * 原工作台「人格与提示词 → 身份提示词」页面功能收拢于此：
 *   - 逐节启停 / 可编辑节自定义内容（custom_content）
 *   - 服务端 Token 统计摘要（总计/启用/停用）与逐节估算
 *   - 保存（写入线上配置）/ 恢复默认 / 重新载入
 * 数据与状态全部由 useIdentityPrompt（identityPrompt.ts）管理，本组件仅渲染。
 * 卡片样式沿用实验室 .pl- 风格。
 */
import { Loader2, RefreshCw, RotateCcw, Save, Power, TriangleAlert } from 'lucide-react'
import type { IdentityRow, IdentitySummary, UseIdentityPromptResult } from './identityPrompt'

export interface IdentityPromptPanelProps {
  rows: IdentityRow[]
  summary: IdentitySummary | null
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

/** 由 Hook 结果构造面板 props（组件不直接依赖 Hook 类型，便于测试/复用） */
export function toIdentityPromptPanelProps(r: UseIdentityPromptResult): IdentityPromptPanelProps {
  return {
    rows: r.rows,
    summary: r.summary,
    loading: r.loading,
    saving: r.saving,
    dirty: r.dirty,
    error: r.error,
    msg: r.msg,
    onToggle: r.onToggle,
    onEditContent: r.onEditContent,
    onSave: r.onSave,
    onReset: r.onReset,
    onReload: r.onReload,
  }
}

export default function IdentityPromptPanel(props: IdentityPromptPanelProps) {
  const { rows, summary, loading, saving, dirty, error, msg, onToggle, onEditContent, onSave, onReset, onReload } = props
  const enabledCount = rows.filter((r) => r.enabled).length

  return (
    <section className="pl-category pl-identity">
      <h2 className="pl-category-title" style={{ color: '#22d3ee' }}>
        <span className="pl-category-dot" style={{ background: '#22d3ee' }} />
        身份提示词（系统提示词 · 线上配置）
        <span className="pl-category-count">
          {loading ? '载入中…' : `${rows.length} 节 · 启用 ${enabledCount} 节`}
        </span>
      </h2>
      <p className="pl-category-desc">
        原工作台「人格与提示词 → 身份提示词」并入。逐节启停 / 编辑后保存即更新线上 system message，
        预览与「请求真实输出」使用后端模板引擎按当前配置生成的注入内容。
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
        <button type="button" className="pl-btn" onClick={onReload} title="从后端重新载入身份提示词配置">
          <RefreshCw size={12} /> 刷新
        </button>
        <button type="button" className="pl-btn" onClick={onReset} title="恢复默认身份提示词配置">
          <RotateCcw size={12} /> 恢复默认
        </button>
        <button type="button" className="pl-btn primary" onClick={onSave} disabled={saving || loading} title="保存启停与自定义内容到线上配置">
          <Save size={12} /> {saving ? '保存中…' : '保存配置'}
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
          {rows.map((row) => (
            <div key={row.key} className="pl-factor-card pl-syspart">
              <div className="pl-factor-head">
                <span className="pl-factor-name">
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

              {row.moduleBadge && !row.moduleAvailable && (
                <p className="pl-id-warn">
                  <TriangleAlert size={11} /> 对应 V2 模块未安装，服务端将保持停用
                </p>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="pl-syspart-actions">
        <button type="button" className="pl-btn" onClick={onSave} disabled={saving || loading}>
          <Power size={12} /> 保存配置
        </button>
        <span className="pl-id-hint">保存仅写入配置；模板由后端按 registry 顺序组装（tools/记忆等运行时槽位保留占位符）。</span>
      </div>
    </section>
  )
}
