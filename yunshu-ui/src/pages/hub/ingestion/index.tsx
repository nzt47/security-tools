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
  AlertTriangle, ArrowRight, BookOpen, CheckCircle2, FileText, FolderOpen, GitBranch,
  Layers, Loader2, PackagePlus, RefreshCw, Search, ShieldCheck,
} from 'lucide-react'
import { Badge, Button, Card, Checkbox, ErrorBox, Input, PageHeader, Select, Tabs, Textarea } from '../components/ui'
import GovernancePanel from './governance'
import ReviewPanel from './ReviewPanel'
import MemoryDraftPanel from './MemoryDraftPanel'
import TransformQueueDialog from '../../../components/TransformQueueDialog'
import IngestionHelp from '../../../components/IngestionHelp'
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
  directIngestion,
  type DirectResult,
} from './api'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
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

type IngestionTab = 'acquire' | 'review' | 'queue' | 'governance'

/** 批量获取的单条结果（逐条预览 + 逐条生成草稿；不安装/不注册/不启用）*/
type BatchRow = { path: string; state: string; id?: string; note?: string }

const BATCH_MAX = 20

/** 候选列表每页条数（惰加载） */
const SOURCE_PAGE = 50

function IngestionTabs({ tab, onChange, onHelp }: {
  tab: IngestionTab
  onChange: (t: IngestionTab) => void
  onHelp: () => void
}) {
  return (
    <div className="mb-3 flex items-center gap-1.5">
      {/* 2026-11-05 IA：转化队列从「技能中心」弹窗搬到这里 —— 摄取 → 生成草稿 → 评审 →
          登记 → 启用是同一条流水线，队列必须和入口同页（原先在技能中心，等于把第 2 段拆走）。 */}
      <Tabs
        variant="pill"
        size="sm"
        aria-label="能力获取子模块"
        value={tab}
        onChange={(v) => onChange(v as IngestionTab)}
        items={[
          { value: 'acquire', label: <><PackagePlus size={12} /> 获取外来能力</> },
          { value: 'review', label: <><ShieldCheck size={12} /> 草稿评审</> },
          { value: 'queue', label: <><GitBranch size={12} /> 转化队列</> },
          { value: 'governance', label: <><Layers size={12} /> 存量治理</> },
        ]}
      />
      {/* 联机帮助：四个 Tab 共用一份手册（流程是跨 Tab 的，只在获取页给会缺一站） */}
      <Button type="button" variant="default" size="sm" className="ml-auto" onClick={onHelp}
        aria-label="使用手册">
        <BookOpen size={12} /> 使用手册
      </Button>
    </div>
  )
}

export default function IngestionPage() {
  // 深链：?panel=ingestion&tab=queue|review|governance（其它页的「转化队列」跳转都落到这里）
  const [tab, setTab] = useState<IngestionTab>(() => {
    const t = new URLSearchParams(window.location.search).get('tab')
    return (t === 'review' || t === 'queue' || t === 'governance') ? t : 'acquire'
  })
  const [mode, setMode] = useState<'path' | 'text'>('path')
  const [pathInput, setPathInput] = useState('')
  const [textInput, setTextInput] = useState('')
  const [sources, setSources] = useState<SourcesView | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [plan, setPlan] = useState<IngestPlan | null>(null)
  const [previewing, setPreviewing] = useState('')
  const [form, setForm] = useState<IngestForm>('skill')
  const [confirmed, setConfirmed] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [error, setError] = useState('')
  // ── 批量获取（2026-11-05 恢复）──
  // 旧「扫描 claude → 全量导入」被 S5 收敛成单条，结果是几十条候选只能一条条点。
  // 批量只做「逐条预览 + 逐条生成草稿」：不安装、不注册、不启用，仍按单条去重结论记账。
  // ── 直通（2026-11-05）：文本 → 草稿一步到位 ──
  // 文本来源恒定判 L2（docs 适配器），所以直通必须显式带 allow_rewrite=true 才调 LLM；
  // 失败（重写未过校验/超时）时**不落草稿**，前端如实显示原因。
  const [directBusy, setDirectBusy] = useState(false)
  const [directResult, setDirectResult] = useState<DirectResult | null>(null)
  const [batchSel, setBatchSel] = useState<string[]>([])
  // 候选惰加载：首屏只渲染一页，点「加载更多」按页追加（**不再硬截断 60 条**）
  const [sourceShown, setSourceShown] = useState(SOURCE_PAGE)
  const [batchBusy, setBatchBusy] = useState('')
  const [batchRows, setBatchRows] = useState<BatchRow[]>([])
  // ── 联机帮助（使用手册）──
  // 首次进入给一条可关闭的提示（不自动弹窗：弹窗会挡住「预览计划」第一跳）。
  const HELP_SEEN_KEY = 'cp.ingestion.helpSeen.v1'
  const [helpOpen, setHelpOpen] = useState(false)
  const [helpSeen, setHelpSeen] = useState(true)
  useEffect(() => {
    try { setHelpSeen(window.localStorage.getItem(HELP_SEEN_KEY) === '1') } catch { setHelpSeen(true) }
  }, [])
  const openHelp = () => {
    setHelpOpen(true)
    setHelpSeen(true)
    try { window.localStorage.setItem(HELP_SEEN_KEY, '1') } catch { /* 隐私模式：不记也不影响使用 */ }
  }

  const currentSource = (): IngestSourceInput => (
    mode === 'text'
      ? { text: textInput, label: '粘贴内容' }
      : { path: pathInput.trim() }
  )

  const resetPlan = () => {
    setPlan(null)
    setConfirmed(false)
    setResult(null)
  }

  const discover = useCallback(async () => {
    setDiscovering(true); setError('')
    try {
      setSources(await fetchIngestionSources())
      setSourceShown(SOURCE_PAGE)  // 重新探测 ⇒ 回到第一页
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
    } catch (e) {
      setError((rewrite ? 'LLM 重写预览失败：' : '预览失败：') + (e instanceof Error ? e.message : String(e)))
    } finally {
      setPreviewing('')
    }
  }

  /** 点候选即预览：省掉「先填路径、再点预览」这两步（用户只做一次选择）。 */
  const previewPath = async (path: string) => {
    setPreviewing('preview'); setError(''); setResult(null); setConfirmed(false)
    try {
      const p = await previewIngestion({ path }, false)
      setPlan(p)
      setForm('skill')
    } catch (e) {
      setError('预览失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setPreviewing('')
    }
  }

  const doCommit = async () => {
    if (!plan || !confirmed) return
    setCommitting(true); setError('')
    try {
      // 【S5 2026-11-04】能力获取页只产出草稿：draft_only=true 走 drafts 隔离区，
      // 不安装、不注册、不启用；登记/启用统一在「转化队列」。
      const next = await commitIngestion({
        source: currentSource(),
        plan_id: plan.plan_id,
        confirm: true,
        form,
        draft_only: true,
        ...(plan.archive_id ? { archive_id: plan.archive_id } : {}),
      })
      setResult(next)
      setConfirmed(false)
    } catch (e) {
      setError('提交失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setCommitting(false)
    }
  }

  const doDirect = async () => {
    if (!textInput.trim()) { setError('请先粘贴文本'); return }
    setDirectBusy(true); setError(''); setDirectResult(null)
    try {
      const r = await directIngestion({
        text: textInput, form, label: '直通文本', allow_rewrite: true, confirm: true,
      })
      setDirectResult(r)
    } catch (e) {
      setError('直通生成失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setDirectBusy(false)
    }
  }

  const runBatch = async () => {
    const paths = batchSel.slice(0, BATCH_MAX)
    if (paths.length === 0) return
    if (!window.confirm('批量生成 ' + paths.length + ' 条草稿？逐条预览后只写草稿（不安装/不注册/不启用）；遇到 L2 会自动做一次 LLM 重写（较慢），重写不过的才跳过。')) return
    setBatchRows([]); setError('')
    const rows: BatchRow[] = []
    for (let i = 0; i < paths.length; i += 1) {
      const path = paths[i]
      setBatchBusy((i + 1) + '/' + paths.length)
      try {
        // 【2026-11-06 自动化】L2 不再让用户回头单条处理：批量里直接代跑一次重写，
        // 重写不过才跳过（并如实写明原因）—— 把「逐条补跑」这类繁复操作消掉。
        let p = await previewIngestion({ path })
        let rewritten = false
        if (p.requires_rewrite) {
          p = await previewIngestion({ path }, true)
          rewritten = true
          const blocked = p.requires_rewrite || (p.rewrite_errors || []).length > 0
          if (blocked) {
            const why = (p.rewrite_errors || []).join('；') || '重写后仍需人工处理'
            rows.push({ path, state: '跳过', note: 'L2 自动重写未通过：' + why.slice(0, 140) })
            setBatchRows([...rows])
            continue
          }
        }
        const res = await commitIngestion({
          source: { path },
          plan_id: p.plan_id,
          confirm: true,
          form: 'skill',
          draft_only: true,
          ...(p.archive_id ? { archive_id: p.archive_id } : {}),
        })
        rows.push({
          path,
          state: res.draft ? (rewritten ? '草稿（自动重写）' : '草稿') : (res.queued ? '入队' : '未落库'),
          id: res.skill_id,
        })
      } catch (e) {
        rows.push({ path, state: '失败', note: (e instanceof Error ? e.message : String(e)).slice(0, 160) })
      }
      setBatchRows([...rows])
    }
    setBatchBusy('')
  }

  const commitLabel = form === 'skill'
    ? '生成技能草稿'
    : (form === 'tool' ? '生成工具草稿' : '生成工作流草稿')

  const tabs = <IngestionTabs tab={tab} onChange={setTab} onHelp={openHelp} />
  const helpNode = <IngestionHelp open={helpOpen} onClose={() => setHelpOpen(false)} onGo={setTab} />

  if (tab === 'review') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-auto px-5 py-4">
        {tabs}
        <ReviewPanel />
        {helpNode}
      </div>
    )
  }

  if (tab === 'queue') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-auto px-5 py-4">
        {tabs}
        <TransformQueueDialog embedded />
        {helpNode}
      </div>
    )
  }

  if (tab === 'governance') {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-auto px-5 py-4">
        {tabs}
        <GovernancePanel />
        {helpNode}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-auto px-5 py-4">
      {tabs}
      <PageHeader
        title="能力获取"
        description="外来技能 / 文档 / 提示词 / 插件包 → 摄取、预览、生成草稿（技能卡 / 工具 / 工作流）。这里**不安装、不注册、不启用**：只产出草稿；评审 → 登记 → 启用在「草稿评审 / 转化队列」两个 Tab（点右上「使用手册」看全流程）。"
      />

      {helpSeen ? null : (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-cyan-900/50 bg-cyan-950/20 px-3 py-2 text-caption text-cyan-200"
          data-cp-help-banner="true">
          <BookOpen size={12} />
          第一次用？先看 3 分钟手册：7 站链路、哪几步自动、你要点的步骤，以及怎么少点几下。
          <Button type="button" variant="default" size="sm" className="ml-auto" onClick={openHelp}>打开使用手册</Button>
          <Button type="button" variant="default" size="xs"
            onClick={() => {
              setHelpSeen(true)
              try { window.localStorage.setItem(HELP_SEEN_KEY, '1') } catch { /* 隐私模式忽略 */ }
            }}>不再提示</Button>
        </div>
      )}

      {error && <div className="mb-3"><ErrorBox message={error} /></div>}

      <div className="grid min-h-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="1. 选择来源">
            <div className="mb-2">
              <Tabs
                variant="pill"
                size="sm"
                aria-label="来源方式"
                value={mode}
                onChange={(v) => { setMode(v as 'path' | 'text'); resetPlan() }}
                items={[
                  { value: 'path', label: <><FolderOpen size={12} /> 本机路径</> },
                  { value: 'text', label: <><FileText size={12} /> 粘贴文本</> },
                ]}
              />
            </div>

            {mode === 'path' ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Input size="sm" className="flex-1" value={pathInput}
                    aria-label="本机路径"
                    placeholder="目录 / SKILL.md / zip / 文档路径"
                    onChange={(e) => { setPathInput(e.target.value); resetPlan() }} />
                  <Button type="button" variant="accent" size="xs"
                    onClick={() => void doPreview(false)}
                    disabled={previewing !== '' || !pathInput.trim()}>
                    {previewing === 'preview' ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                    预览计划
                  </Button>
                </div>
                <p className="mt-1 text-micro text-slate-500">
                  只读取来源，不执行来源脚本；路径可指向 claude / openclaw / trae 技能目录或任意文档。
                </p>
              </>
            ) : (
              <>
                <Textarea size="sm" className="min-h-[150px] font-mono" value={textInput}
                  aria-label="粘贴文档或提示词"
                  placeholder="粘贴 markdown / 普通文档 / 提示词…"
                  onChange={(e) => { setTextInput(e.target.value); resetPlan() }} />
                <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
                  <span className="mr-auto text-micro text-slate-500">
                    直通 = 一次调用直接出草稿（含 1 次 LLM 重写与引用校验）；失败不落草稿。
                  </span>
                  <Button type="button" variant="accent" size="xs"
                    onClick={() => void doPreview(false)}
                    disabled={previewing !== '' || !textInput.trim()}>
                    {previewing === 'preview' ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
                    预览计划
                  </Button>
                  <Button type="button" variant="accent" size="xs"
                    onClick={() => void doDirect()}
                    disabled={directBusy || !textInput.trim()}
                    aria-label="直通生成草稿"
                    title="跳过分级/确认两道闸：直接调用一次 LLM 重写 + 校验并落草稿（仍不安装/不注册/不启用）">
                    {directBusy ? <Loader2 size={12} className="animate-spin" /> : <PackagePlus size={12} />}
                    直接生成草稿
                  </Button>
                </div>
                {directResult && (
                  <div className="mt-2 rounded-sm border border-emerald-900/50 bg-emerald-950/20 p-2 text-micro leading-relaxed text-emerald-200">
                    <div>
                      已直通生成草稿：<span className="font-mono">{directResult.result.skill_id}</span>
                      {' · ' + directResult.plan.level + ' · 去重提示 ' + directResult.plan.dedupe.length + ' 条'}
                    </div>
                    <div className="break-all text-emerald-300/80">{directResult.result.draft_dir}</div>
                    <div className="text-emerald-300/60">这里只落草稿；登记与启用在「草稿评审」的草稿行上。</div>
                  </div>
                )}
              </>
            )}
          </Card>

          <Card title="本机可吞噬源" actions={(
            <div className="flex items-center gap-2">
              <span className="text-micro text-slate-500">
                {sources ? sources.stats.candidates + ' 个候选 / ' + sources.stats.previewable + ' 个可预览' : '未探测'}
              </span>
              {/* 探测入口收在本卡片右侧：它只服务「本机可吞噬源」，放页头会让人以为是全局动作 */}
              <Button type="button" variant="default" size="xs" onClick={() => void discover()} disabled={discovering}
                aria-label="探测本机源">
                {discovering ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                探测本机源
              </Button>
              <Button type="button" variant="default" size="xs" disabled={batchBusy !== '' || batchSel.length === 0}
                onClick={() => void runBatch()}
                aria-label="批量生成草稿"
                title="逐条预览后各生成一份草稿（不安装/不注册/不启用；L2 自动重写）">
                {batchBusy !== '' ? <Loader2 size={12} className="animate-spin" /> : <PackagePlus size={12} />}
                批量生成草稿{batchSel.length > 0 ? '（' + Math.min(batchSel.length, BATCH_MAX) + '）' : ''}{batchBusy ? ' ' + batchBusy : ''}
              </Button>
            </div>
          )}>
            {!sources ? (
              <p className="text-xs text-slate-500">点「探测本机源」列出配置根下的候选；首屏会自动探测一次。</p>
            ) : (
              <>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {sources.roots.map((r) => (
                    <span key={r.ecosystem + r.path}
                      className={'rounded-sm border px-2 py-1 text-micro ' + (r.exists
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
                  <div className="max-h-[300px] overflow-auto rounded-sm border border-slate-800">
                    {sources.candidates.slice(0, sourceShown).map((c) => (
                      <div key={c.path}
                        className="flex w-full items-center gap-2 border-b border-slate-800/60 px-2 py-1.5 text-left text-caption text-slate-300 last:border-b-0 hover:bg-slate-800/50">
                        <Checkbox className="shrink-0" aria-label={'批量选择 ' + c.name} disabled={!c.previewable} checked={batchSel.includes(c.path)} onChange={(e) => setBatchSel((prev) => e.target.checked ? (prev.includes(c.path) ? prev : [...prev, c.path].slice(0, BATCH_MAX)) : prev.filter((x) => x !== c.path))} />
                        <Button variant="plain" size="bare" type="button"
                          className="flex min-w-0 flex-1 items-center gap-2 text-left"
                          onClick={() => {
                            setMode('path'); setPathInput(c.path); resetPlan()
                            // 可预览的候选：选中即自动预览（少点一次）；仅展示的候选不动
                            if (c.previewable) void previewPath(c.path)
                          }}>
                          <Badge color={c.previewable ? 'cyan' : 'amber'}>{c.kind}</Badge>
                          <span className="font-mono text-micro text-cyan-300">{c.ecosystem}</span>
                          <span className="truncate">{c.name}</span>
                          {/* 2026-11-07：这条用过没有 —— 100 个候选名字都差不多，
                              没有标记只能靠记性（实测同一批里 brainstorming / byted-* 早已用过）。 */}
                          {c.usage === 'registered' && (
                            <span className="shrink-0 rounded-sm border border-emerald-800/70 bg-emerald-500/10 px-1 text-micro text-emerald-300"
                              title="已入轨：data/skills_repo 下已有同名技能">已入轨</span>
                          )}
                          {c.usage === 'draft' && (
                            <span className="shrink-0 rounded-sm border border-amber-800/70 bg-amber-500/10 px-1 text-micro text-amber-300"
                              title={'生成过草稿' + (c.usage_draft_id ? '：' + c.usage_draft_id : '') + (c.usage_at ? '（' + c.usage_at + '）' : '')}>已生成草稿</span>
                          )}
                        </Button>
                        <span className="ml-auto shrink-0 text-micro text-slate-600">{c.previewable ? '可预览' : '仅展示'}</span>
                      </div>
                    ))}
                  </div>
                )}
                {sources.candidates.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-micro text-slate-500">
                    <span>
                      已显示 {Math.min(sourceShown, sources.candidates.length)} / {sources.candidates.length} 条
                    </span>
                    {sources.candidates.length > sourceShown ? (
                      <>
                        <Button type="button" variant="default" size="xs" onClick={() => setSourceShown((n) => n + SOURCE_PAGE)}>
                          加载更多（再 {Math.min(SOURCE_PAGE, sources.candidates.length - sourceShown)} 条）
                        </Button>
                        <Button type="button" variant="default" size="xs" onClick={() => setSourceShown(sources.candidates.length)}>
                          全部展开
                        </Button>
                      </>
                    ) : (
                      <span>已全部显示</span>
                    )}
                    <span className="text-slate-600">· 也可直接粘贴完整路径</span>
                  </div>
                )}
                {batchRows.length > 0 && (
                  <div className="mt-2 rounded-sm border border-slate-800 bg-slate-950/60 p-2 text-micro">
                    <div className="text-slate-500">批量结果（{batchRows.length}）：草稿只落隔离区，登记/启用到「草稿评审」逐条点</div>
                    <ul className="mt-1 space-y-0.5">
                      {batchRows.map((row) => (
                        <li key={row.path} className="flex items-center gap-2">
                          <span className={row.state === '失败' ? 'text-rose-300' : (row.state === '跳过' ? 'text-amber-300' : 'text-emerald-300')}>{row.state}</span>
                          <code className="truncate text-slate-400">{row.id || row.path}</code>
                          {row.note && <span className="truncate text-slate-500">{row.note}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </Card>
          <MemoryDraftPanel />
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
                  <span className="font-mono text-micro text-slate-500">{plan.adapter}</span>
                  <span className="font-mono text-micro text-slate-600">{plan.plan_id}</span>
                  {plan.rewritten && <Badge color="cyan">LLM 重写</Badge>}
                  {plan.requires_rewrite && <Badge color="red">需重写</Badge>}
                </div>

                {plan.skill && (
                  <div className="rounded-sm border border-slate-800 bg-slate-950/60 p-2">
                    <div className="text-xs font-medium text-slate-200">{plan.skill.name}</div>
                    <div className="mt-0.5 text-micro text-slate-500">
                      <span className="font-mono text-cyan-300">{plan.skill.id}</span>
                      {' · v' + plan.skill.version}
                      {' · scripts ' + Object.keys(plan.skill.scripts || {}).length}
                    </div>
                    <p className="mt-1 text-caption leading-relaxed text-slate-400">{plan.skill.description || '（无描述）'}</p>
                  </div>
                )}

                <div className="rounded-sm border border-cyan-900/50 bg-cyan-950/20 p-2">
                  <div className="flex flex-wrap items-center gap-2 text-caption text-cyan-200">
                    <Layers size={12} /> 推荐形态：{plan.recommendation.form}
                    <span className="text-micro text-slate-500">置信度 {plan.recommendation.confidence}</span>
                    {plan.recommendation.form !== 'skill' && (
                      <div className="ml-auto flex flex-wrap items-center gap-1.5">
                        <Button type="button" variant="default" size="xs"
                          onClick={() => { setForm(plan.recommendation.form as IngestForm); setConfirmed(false) }}
                          title="采用后只生成骨架草稿：不注册、不启用，步骤需人工绑定工具">
                          采用（生成骨架草稿）
                        </Button>
                        <Button type="button" variant="success-outline" size="sm"
                          onClick={() => { setForm('skill'); setConfirmed(false) }}
                          title="按技能形态生成草稿（仍不注册、不启用）">
                          改为按技能草稿
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="mt-1 text-micro leading-relaxed text-slate-400">{plan.recommendation.reason}</p>
                  {plan.recommendation.form !== 'skill' && (
                    <p className="mt-1.5 rounded-sm border border-amber-900/50 bg-amber-950/20 p-2 text-micro leading-relaxed text-amber-200">
                      推荐的工作流/工具形态只产出<b>骨架草稿</b>：写到隔离目录、不注册、不启用，步骤没绑定工具就不能执行。
                      若你只是想把这份内容变成可检索、可注入的能力，点「改为按技能草稿」更省事（草稿仍不进模型，登记后才算能力）。
                    </p>
                  )}
                </div>

                {plan.requires_rewrite && (
                  <div className="rounded-sm border border-amber-900/60 bg-amber-950/20 p-2">
                    <div className="flex flex-wrap items-center gap-2 text-caption text-amber-200">
                      <AlertTriangle size={12} /> L2 来源不能直接生成草稿，需要 LLM 重写并人工预览。
                      <Button type="button" variant="accent" size="sm" className="ml-auto"
                        onClick={() => void doPreview(true)}
                        disabled={previewing !== ''}>
                        {previewing === 'rewrite' ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
                        LLM 重写并预览
                      </Button>
                    </div>
                  </div>
                )}

                {plan.archive_id && (
                  <div className="text-micro text-slate-500">
                    重写存档：<span className="font-mono text-cyan-300">{plan.archive_id}</span>
                    {' '}（生成草稿时原样使用这份存档，不再次调用 LLM）
                  </div>
                )}
                {plan.rewrite_errors.length > 0 && (
                  <div className="rounded-sm border border-red-900/60 bg-red-950/20 p-2 text-micro text-red-300">
                    重写未通过：{plan.rewrite_errors.join('；')}
                  </div>
                )}

                {plan.warnings.length > 0 && (
                  <div className="rounded-sm border border-amber-900/50 bg-amber-950/10 p-2 text-micro text-amber-300">
                    {plan.warnings.map((w, i) => <div key={i}>· {w}</div>)}
                  </div>
                )}

                <div>
                  <div className="mb-1 flex items-center gap-1.5 text-caption font-medium text-slate-300">
                    <ShieldCheck size={12} /> 去重 / 吸收建议
                  </div>
                  {plan.dedupe.length === 0 ? (
                    <p className="text-micro text-slate-500">未发现同名、同功能或原生能力重叠。</p>
                  ) : (
                    <div className="flex flex-col gap-1">
                      {plan.dedupe.map((d) => (
                        <div key={d.kind + ':' + d.id}
                          className="flex flex-wrap items-center gap-1.5 rounded-sm border border-slate-800 bg-slate-950/50 px-2 py-1 text-micro">
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
                    <div className="mt-1 flex flex-wrap items-center gap-2 rounded-sm border border-emerald-900/50 bg-emerald-950/20 px-2 py-1 text-micro text-emerald-200">
                      <ArrowRight size={12} /> 建议合并到
                      <span className="font-mono">{plan.merge_suggestion.target_id}</span>
                      <span className="text-slate-500">{plan.merge_suggestion.reason}</span>
                      <span className="ml-auto text-slate-400">
                        草稿生成后在「转化队列」里合并
                      </span>
                    </div>
                  )}
                </div>

                {plan.actions.length > 0 && (
                  <details className="text-micro text-slate-500">
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
                <div className="flex flex-wrap items-center gap-2 text-caption text-slate-400">
                  <span>形态</span>
                  <Select
                    size="sm"
                    className="w-56"
                    value={form}
                    aria-label="落库形态"
                    hidePlaceholder
                    onChange={(v) => {
                      const next = v as IngestForm
                      setForm(next); setConfirmed(false)
                    }}
                    options={(Object.keys(FORM_LABEL) as IngestForm[]).map((k) => ({ label: FORM_LABEL[k], value: k }))}
                  />
                  <span className="text-micro text-slate-500">
                    三种形态都<b>只产出草稿</b>（不注册、不启用）；确认后在「草稿评审」同一行登记/启用
                  </span>
                </div>

                <label className="flex items-start gap-2 text-caption text-slate-300">
                  <Checkbox className="mt-0.5" checked={confirmed} aria-label="我已核对预览计划" onChange={(e) => setConfirmed(e.target.checked)} />
                  <span>我已核对预览计划（来源、形态、去重/合并目标与警告）；确认后才落库。</span>
                </label>

                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="accent" size="xs" onClick={() => void doCommit()}
                    disabled={!confirmed || committing || !!previewing || plan.requires_rewrite}
                    aria-label={commitLabel}>
                    {committing ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                    {commitLabel}
                  </Button>
                  {plan.requires_rewrite && (
                    <span className="text-micro text-amber-400">
                      L2 来源被卡在这里：先在「2. 预览计划」点「LLM 重写并预览」，回来才能生成
                    </span>
                  )}
                  {!confirmed && !plan.requires_rewrite && (
                    <span className="text-micro text-amber-400">先勾选上方「我已核对预览计划」，按钮才会启用</span>
                  )}
                </div>
              </div>
            )}
          </Card>

          {result && (
            <Card title="4. 结果" actions={(
              <Badge color={result.draft ? 'cyan' : result.merged ? 'green' : 'green'}>
                {/* 本页只会产出草稿：不再显示「已安装/未安装」这类过时状态 */}
                {result.draft ? '草稿' : result.queued ? '已入队' : '未落草稿'}
              </Badge>
            )}>
              <div className="flex flex-col gap-1.5 text-caption text-slate-300">
                <div>
                  <span className="font-mono text-cyan-300">{result.skill_id || '—'}</span>
                  {result.name ? ' · ' + result.name : ''}
                  {result.version ? ' · v' + result.version : ''}
                </div>
                <div className="flex flex-wrap gap-2 text-micro text-slate-500">
                  <span>form={result.form}</span>
                  <span>installed={String(result.installed)}</span>
                  <span>draft={String(result.draft)}</span>
                  <span>queued={String(result.queued)}</span>
                  <span>merged={String(result.merged)}</span>
                  {result.merged_into && <span>→ {result.merged_into}</span>}
                  {result.stage && <span>stage={result.stage} ({result.stage_verdict})</span>}
                </div>
                {result.draft_dir && (
                  <div className="break-all rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-micro text-slate-400">
                    草稿目录：{result.draft_dir}
                  </div>
                )}
                {result.draft && (
                  <div className="text-micro leading-relaxed text-slate-500">
                    它<b>只是草稿</b>：不注册、不启用。到「草稿评审」页底部的「吞噬生成的骨架草稿」可以查看与删除；
                    要登记/启用就在「草稿评审」里点那一行。路径就是上面这个目录。
                  </div>
                )}
                {result.warnings.length > 0 && (
                  <div className="rounded-sm border border-amber-900/50 p-2 text-micro text-amber-300">
                    {result.warnings.map((w, i) => <div key={i}>· {w}</div>)}
                  </div>
                )}
                {/* 下一步直达：草稿 → 评审 → 登记，省掉「自己回想在哪一页」 */}
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-micro text-slate-500">下一步：</span>
                  <Button type="button" variant="default" size="xs" onClick={() => setTab('review')}>
                    去草稿评审
                  </Button>
                  <Button type="button" variant="default" size="xs" onClick={() => setTab('queue')}>
                    去转化队列登记
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
      {helpNode}
    </div>
  )
}
