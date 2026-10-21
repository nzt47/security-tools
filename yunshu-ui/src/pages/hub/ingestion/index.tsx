/**
 * 吞噬（ingestion）—— 全系统唯一的外来能力获取入口。
 *
 * 页面只做三件事：选来源 → 看计划 → 显式确认。
 * 业务判定（适配器/L0-L2/去重/重写/合并/草稿）全部在后端；前端不复制规则。
 *
 * 单一入口纪律：技能中心 / 技能维护 / 工具调用不再提供「外来安装 / 导入 zip /
 * 扫描 claude」控件，统一指向本页；旧端点如仍在，只供旧链接兼容。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, ArrowRight, CheckCircle2, FileText, FolderOpen, GitBranch,
  Layers, Loader2, RefreshCw, Search, ShieldCheck,
} from 'lucide-react'
import { Badge, Card, ErrorBox, PageHeader } from '../components/ui'
import {
  commitIngestion,
  fetchIngestionSources,
  previewIngestion,
  type DedupeItem,
  type IngestForm,
  type IngestPlan,
  type IngestResult,
  type IngestSourceInput,
  type SourcesView,
} from './api'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'
const FORM_LABEL: Record<IngestForm, string> = {
  skill: '技能（默认）',
  tool: '代码工具草稿',
  workflow: '工作流草稿',
}

function levelTone(level: string): 'green' | 'amber' | 'red' | 'slate' {
  if (level === 'L0') return 'green'
  if (level === 'L1') return 'amber'
  if (level === 'L2') return 'red'
  return 'slate'
}

function dedupeLabel(item: DedupeItem): string {
  if (item.kind === 'native') return '原生重叠'
  if (item.match === 'id') return '同 id 覆盖'
  if (item.match === 'name') return '同名'
  return '既有技能'
}

function dedupeTone(item: DedupeItem): 'green' | 'amber' | 'red' | 'cyan' | 'slate' {
  if (item.action === 'absorb') return 'cyan'
  if (item.action === 'overwrite') return 'red'
  if (item.action === 'merge') return 'green'
  if (item.action === 'review') return 'amber'
  return 'slate'
}

export default function IngestionPage() {
  const [mode, setMode] = useState<'path' | 'text'>('path')
  const [pathInput, setPathInput] = useState('')
  const [textInput, setTextInput] = useState('')
  const [sources, setSources] = useState<SourcesView | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [plan, setPlan] = useState<IngestPlan | null>(null)
  const [previewing, setPreviewing] = useState('')
  const [form, setForm] = useState<IngestForm>('skill')
  const [mergeInto, setMergeInto] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [error, setError] = useState('')

  const currentSource = (): IngestSourceInput => (
    mode === 'text'
      ? { text: textInput, label: '粘贴内容' }
      : { path: pathInput.trim() }
  )

  const resetPlan = () => {
    setPlan(null)
    setConfirmed(false)
    setResult(null)
    setMergeInto('')
  }

  const discover = useCallback(async () => {
    setDiscovering(true); setError('')
    try {
      setSources(await fetchIngestionSources())
    } catch (e) {
      setError('探测本机源失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setDiscovering(false)
    }
  }, [])

  // 首屏探测是只读的；失败不挡手输路径
  useEffect(() => { void discover() }, [discover])

  const doPreview = async (rewrite: boolean) => {
    const source = currentSource()
    if (mode === 'path' && !source.path) { setError('请先填写本机路径'); return }
    if (mode === 'text' && !(source.text ?? '').trim()) { setError('请先粘贴文档 / 提示词'); return }
    setPreviewing(rewrite ? 'rewrite' : 'preview'); setError(''); setResult(null); setConfirmed(false)
    try {
      const p = await previewIngestion(source, rewrite)
      setPlan(p)
      setForm('skill')
      setMergeInto('')
    } catch (e) {
      setError((rewrite ? 'LLM 重写预览失败：' : '预览失败：') + (e instanceof Error ? e.message : String(e)))
    } finally {
      setPreviewing('')
    }
  }

  const doCommit = async () => {
    if (!plan || !confirmed) return
    setCommitting(true); setError('')
    try {
      const next = await commitIngestion({
        source: currentSource(),
        plan_id: plan.plan_id,
        confirm: true,
        form,
        ...(plan.archive_id ? { archive_id: plan.archive_id } : {}),
        ...(form === 'skill' && mergeInto.trim() ? { merge_into: mergeInto.trim() } : {}),
      })
      setResult(next)
      setConfirmed(false)
    } catch (e) {
      setError('提交失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setCommitting(false)
    }
  }

  const commitLabel = form === 'skill'
    ? (mergeInto.trim() ? '合并进 ' + mergeInto.trim() : '安装为技能')
    : (form === 'tool' ? '生成工具草稿' : '生成工作流草稿')

  return (
    <div className="flex h-full min-h-0 flex-col overflow-auto px-5 py-4">
      <PageHeader
        title="吞噬"
        description="外来技能 / 文档 / 提示词 / 插件包 → 可预览、可重写、可合并、可生成草稿的云枢能力。只在显式确认后落库。"
        actions={(
          <button type="button" className={BTN} onClick={() => void discover()} disabled={discovering}>
            {discovering ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            探测本机源
          </button>
        )}
      />

      {error && <div className="mb-3"><ErrorBox message={error} /></div>}

      <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="1. 选择来源">
            <div className="mb-2 flex items-center gap-1.5" role="tablist" aria-label="来源方式">
              <button type="button" role="tab" aria-selected={mode === 'path'}
                className={mode === 'path' ? BTN_EM : BTN} onClick={() => { setMode('path'); resetPlan() }}>
                <FolderOpen size={12} /> 本机路径
              </button>
              <button type="button" role="tab" aria-selected={mode === 'text'}
                className={mode === 'text' ? BTN_EM : BTN} onClick={() => { setMode('text'); resetPlan() }}>
                <FileText size={12} /> 粘贴文本
              </button>
            </div>

            {mode === 'path' ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <input className={INPUT + ' flex-1'} value={pathInput}
                    aria-label="本机路径"
                    placeholder="目录 / SKILL.md / zip / 文档路径"
                    onChange={(e) => { setPathInput(e.target.value); resetPlan() }} />
                  <button type="button" className={BTN_EM}
                    onClick={() => void doPreview(false)}
                    disabled={previewing !== '' || !pathInput.trim()}>
                    {previewing === 'preview' ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                    预览计划
                  </button>
                </div>
                <p className="mt-1 text-[10px] text-slate-500">
                  只读取来源，不执行来源脚本；路径可指向 claude / openclaw / trae 技能目录或任意文档。
                </p>
              </>
            ) : (
              <>
                <textarea className={INPUT + ' min-h-[150px] font-mono'} value={textInput}
                  aria-label="粘贴文档或提示词"
                  placeholder="粘贴 markdown / 普通文档 / 提示词…"
                  onChange={(e) => { setTextInput(e.target.value); resetPlan() }} />
                <div className="mt-2 flex justify-end">
                  <button type="button" className={BTN_EM}
                    onClick={() => void doPreview(false)}
                    disabled={previewing !== '' || !textInput.trim()}>
                    {previewing === 'preview' ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                    预览计划
                  </button>
                </div>
              </>
            )}
          </Card>

          <Card title="本机可吞噬源" actions={(
            <span className="text-[10px] text-slate-500">
              {sources ? sources.stats.candidates + ' 个候选 / ' + sources.stats.previewable + ' 个可预览' : '未探测'}
            </span>
          )}>
            {!sources ? (
              <p className="text-xs text-slate-500">点「探测本机源」列出配置根下的候选；首屏会自动探测一次。</p>
            ) : (
              <>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {sources.roots.map((r) => (
                    <span key={r.ecosystem + r.path}
                      className={'rounded border px-2 py-1 text-[10px] ' + (r.exists
                        ? 'border-slate-700 text-slate-400'
                        : 'border-amber-900/60 text-amber-400')}
                      title={r.path}>
                      {r.ecosystem} · {r.exists ? (r.candidates ?? 0) + ' 项' : (r.error || '不存在')}
                    </span>
                  ))}
                </div>
                {sources.candidates.length === 0 ? (
                  <p className="text-xs text-slate-500">配置根下没有可预览候选。</p>
                ) : (
                  <div className="max-h-[300px] overflow-auto rounded border border-slate-800">
                    {sources.candidates.slice(0, 60).map((c) => (
                      <button key={c.path} type="button"
                        className="flex w-full items-center gap-2 border-b border-slate-800/60 px-2 py-1.5 text-left text-[11px] text-slate-300 last:border-b-0 hover:bg-slate-800/50"
                        onClick={() => { setMode('path'); setPathInput(c.path); resetPlan() }}>
                        <Badge color={c.previewable ? 'cyan' : 'amber'}>{c.kind}</Badge>
                        <span className="font-mono text-[10px] text-cyan-300">{c.ecosystem}</span>
                        <span className="truncate">{c.name}</span>
                        <span className="ml-auto shrink-0 text-[10px] text-slate-600">{c.previewable ? '可预览' : '仅展示'}</span>
                      </button>
                    ))}
                  </div>
                )}
                {sources.candidates.length > 60 && (
                  <p className="mt-1 text-[10px] text-slate-500">只显示前 60 个候选；可直接粘贴完整路径。</p>
                )}
              </>
            )}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="2. 预览计划" actions={plan ? (
            <Badge color={levelTone(plan.conformance.level)}>{plan.conformance.level}</Badge>
          ) : undefined}>
            {!plan ? (
              <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
                <Search size={14} /> 先选来源并点「预览计划」；这里会显示 L0/L1/L2、去重、推荐形态与警告。
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge color={levelTone(plan.conformance.level)}>{plan.conformance.level}</Badge>
                  <span className="font-mono text-[10px] text-slate-500">{plan.adapter}</span>
                  <span className="font-mono text-[10px] text-slate-600">{plan.plan_id}</span>
                  {plan.rewritten && <Badge color="cyan">LLM 重写</Badge>}
                  {plan.requires_rewrite && <Badge color="red">需重写</Badge>}
                </div>

                {plan.skill && (
                  <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
                    <div className="text-xs font-medium text-slate-200">{plan.skill.name}</div>
                    <div className="mt-0.5 text-[10px] text-slate-500">
                      <span className="font-mono text-cyan-300">{plan.skill.id}</span>
                      {' · v' + plan.skill.version}
                      {' · scripts ' + Object.keys(plan.skill.scripts || {}).length}
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{plan.skill.description || '（无描述）'}</p>
                  </div>
                )}

                <div className="rounded border border-cyan-900/50 bg-cyan-950/20 p-2">
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-cyan-200">
                    <Layers size={12} /> 推荐形态：{plan.recommendation.form}
                    <span className="text-[10px] text-slate-500">置信度 {plan.recommendation.confidence}</span>
                    {plan.recommendation.form !== 'skill' && (
                      <button type="button" className={BTN + ' ml-auto'}
                        onClick={() => { setForm(plan.recommendation.form as IngestForm); setConfirmed(false) }}>
                        采用推荐
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-[10px] leading-relaxed text-slate-400">{plan.recommendation.reason}</p>
                </div>

                {plan.requires_rewrite && (
                  <div className="rounded border border-amber-900/60 bg-amber-950/20 p-2">
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-amber-200">
                      <AlertTriangle size={12} /> L2 来源不能直接安装，需要 LLM 重写并人工预览。
                      <button type="button" className={BTN_EM + ' ml-auto'}
                        onClick={() => void doPreview(true)}
                        disabled={previewing !== ''}>
                        {previewing === 'rewrite' ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
                        LLM 重写并预览
                      </button>
                    </div>
                  </div>
                )}

                {plan.archive_id && (
                  <div className="text-[10px] text-slate-500">
                    重写存档：<span className="font-mono text-cyan-300">{plan.archive_id}</span>
                    {' '}（commit 会原样安装这份草稿，不再次调用 LLM）
                  </div>
                )}
                {plan.rewrite_errors.length > 0 && (
                  <div className="rounded border border-red-900/60 bg-red-950/20 p-2 text-[10px] text-red-300">
                    重写未通过：{plan.rewrite_errors.join('；')}
                  </div>
                )}

                {plan.warnings.length > 0 && (
                  <div className="rounded border border-amber-900/50 bg-amber-950/10 p-2 text-[10px] text-amber-300">
                    {plan.warnings.map((w, i) => <div key={i}>· {w}</div>)}
                  </div>
                )}

                <div>
                  <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-slate-300">
                    <ShieldCheck size={12} /> 去重 / 吸收建议
                  </div>
                  {plan.dedupe.length === 0 ? (
                    <p className="text-[10px] text-slate-500">未发现同名、同功能或原生能力重叠。</p>
                  ) : (
                    <div className="flex flex-col gap-1">
                      {plan.dedupe.map((d) => (
                        <div key={d.kind + ':' + d.id}
                          className="flex flex-wrap items-center gap-1.5 rounded border border-slate-800 bg-slate-950/50 px-2 py-1 text-[10px]">
                          <Badge color={dedupeTone(d)}>{dedupeLabel(d)}</Badge>
                          <span className="font-mono text-slate-300">{d.id}</span>
                          <span className="text-slate-500">{d.name}</span>
                          <span className="text-slate-600">{d.action}</span>
                          {d.similarity > 0 && <span className="text-slate-500">J={d.similarity}</span>}
                          <span className="ml-auto max-w-[55%] truncate text-slate-500" title={d.reason}>{d.reason}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {plan.merge_suggestion && (
                    <div className="mt-1 flex flex-wrap items-center gap-2 rounded border border-emerald-900/50 bg-emerald-950/20 px-2 py-1 text-[10px] text-emerald-200">
                      <ArrowRight size={12} /> 建议合并到
                      <span className="font-mono">{plan.merge_suggestion.target_id}</span>
                      <span className="text-slate-500">{plan.merge_suggestion.reason}</span>
                      <button type="button" className={BTN + ' ml-auto'}
                        onClick={() => { setForm('skill'); setMergeInto(plan.merge_suggestion!.target_id); setConfirmed(false) }}>
                        填入合并目标
                      </button>
                    </div>
                  )}
                </div>

                {plan.actions.length > 0 && (
                  <details className="text-[10px] text-slate-500">
                    <summary className="cursor-pointer text-slate-400">计划动作（{plan.actions.length}）</summary>
                    <ul className="mt-1 list-disc pl-4 leading-relaxed">
                      {plan.actions.map((a, i) => <li key={i}>{a}</li>)}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </Card>

          <Card title="3. 确认落库">
            {!plan ? (
              <p className="text-xs text-slate-500">预览后才可提交；未确认不会写任何文件。</p>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                  <span>形态</span>
                  <select className={INPUT + ' w-56'} value={form}
                    aria-label="落库形态"
                    onChange={(e) => {
                      const next = e.target.value as IngestForm
                      setForm(next); setConfirmed(false)
                      if (next !== 'skill') setMergeInto('')
                    }}>
                    {(Object.keys(FORM_LABEL) as IngestForm[]).map((k) => (
                      <option key={k} value={k}>{FORM_LABEL[k]}</option>
                    ))}
                  </select>
                  <span className="text-[10px] text-slate-500">
                    tool / workflow 只生成草稿（不注册、不启用）
                  </span>
                </div>

                {form === 'skill' && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[11px] text-slate-400">合并目标（可选）</span>
                    <input className={INPUT + ' w-64'} value={mergeInto}
                      aria-label="合并目标技能 id"
                      placeholder="留空 = 安装为独立技能"
                      onChange={(e) => { setMergeInto(e.target.value); setConfirmed(false) }} />
                  </div>
                )}

                <label className="flex items-start gap-2 text-[11px] text-slate-300">
                  <input type="checkbox" className="mt-0.5" checked={confirmed}
                    aria-label="我已核对预览计划"
                    onChange={(e) => setConfirmed(e.target.checked)} />
                  <span>我已核对预览计划（来源、形态、去重/合并目标与警告）；确认后才落库。</span>
                </label>

                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className={BTN_EM} onClick={() => void doCommit()}
                    disabled={!confirmed || committing || !!previewing || plan.requires_rewrite}
                    aria-label={commitLabel}>
                    {committing ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                    {commitLabel}
                  </button>
                  {plan.requires_rewrite && <span className="text-[10px] text-amber-400">先完成 LLM 重写并重新预览</span>}
                  {!confirmed && <span className="text-[10px] text-slate-500">未勾选确认时按钮不可用</span>}
                </div>
              </div>
            )}
          </Card>

          {result && (
            <Card title="4. 结果" actions={(
              <Badge color={result.draft ? 'cyan' : result.merged ? 'green' : 'green'}>
                {result.draft ? '草稿' : result.merged ? '已合并' : result.installed ? '已安装' : '未安装'}
              </Badge>
            )}>
              <div className="flex flex-col gap-1.5 text-[11px] text-slate-300">
                <div>
                  <span className="font-mono text-cyan-300">{result.skill_id || '—'}</span>
                  {result.name ? ' · ' + result.name : ''}
                  {result.version ? ' · v' + result.version : ''}
                </div>
                <div className="flex flex-wrap gap-2 text-[10px] text-slate-500">
                  <span>form={result.form}</span>
                  <span>installed={String(result.installed)}</span>
                  <span>draft={String(result.draft)}</span>
                  <span>queued={String(result.queued)}</span>
                  <span>merged={String(result.merged)}</span>
                  {result.merged_into && <span>→ {result.merged_into}</span>}
                  {result.stage && <span>stage={result.stage} ({result.stage_verdict})</span>}
                </div>
                {result.draft_dir && (
                  <div className="break-all rounded border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400">
                    草稿目录：{result.draft_dir}
                  </div>
                )}
                {result.warnings.length > 0 && (
                  <div className="rounded border border-amber-900/50 p-2 text-[10px] text-amber-300">
                    {result.warnings.map((w, i) => <div key={i}>· {w}</div>)}
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
