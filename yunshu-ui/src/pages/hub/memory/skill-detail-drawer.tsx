/**
 * 技能详情抽屉 —— 每个技能行上「运行 / 反馈 / 重复」这一侧的管理面
 * ------------------------------------------------------------------
 * 这 7 个端点此前一条都没接线（扫描器 2026-10-17 修完箭头函数常量后仍然如此）：
 *   instruction(GET)       skill.md 正文（Layer 2 按需加载）
 *   scripts(GET)           脚本清单（Layer 3 元信息，不加载代码）
 *   execute(POST)          沙箱执行某个脚本（结果才进上下文，代码不进）
 *   param-stats(GET)       参数级执行统计 + 需要回避的参数
 *   feedback(GET/POST)     反馈聚合统计 / 提交一条反馈
 *   optimize-with-feedback(POST)  按反馈一键调参
 *   duplicates(GET)        与指定技能重复的其他技能
 *
 * 【为什么不塞进「评审-评估」那张表】
 *   评审面回答的是「这个技能能不能用」，这里回答的是「它用得怎么样、跑起来什么结果」。
 *   两者的数据源、刷新节奏（评审=事件驱动，运行=点开即拉）都不同，
 *   混在一张表里会让「刚点了哪个按钮」这类上下文丢失。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, Copy, FileCode, Gauge, Loader2, MessageSquare,
  Play, RefreshCw, ScrollText, X,
} from 'lucide-react'
import { ErrorBox, hubGet, hubPost } from '../components/ui'
import {
  SKILL_CONTENT,
  SKILLS_MGMT_ASSESS_MERGE_SAFE,
  SKILLS_MGMT_ASSESS_MERGE_UNDO,
  skillDuplicates, skillExecute, skillFeedback, skillInstruction,
  skillOptimizeWithFeedback, skillParamStats, skillScripts,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-72 overflow-auto whitespace-pre-wrap wrap-break-word rounded-md border border-slate-800 bg-slate-950 p-2.5 font-mono text-[11px] leading-relaxed text-slate-300'

type Tab = 'instruction' | 'scripts' | 'params' | 'feedback' | 'duplicates'

const TABS: { key: Tab; label: string; icon: typeof FileCode; hint: string }[] = [
  { key: 'instruction', label: '使用说明', icon: ScrollText, hint: 'skill.md 正文（Layer 2 按需加载）' },
  { key: 'scripts', label: '脚本 / 执行', icon: FileCode, hint: '脚本清单 + 沙箱执行（代码不进上下文，只有结果进）' },
  { key: 'params', label: '参数统计', icon: Gauge, hint: '参数级执行统计 · 需要回避的参数 · 按反馈一键调参' },
  { key: 'feedback', label: '反馈', icon: MessageSquare, hint: '满意度聚合 + 提交一条反馈（需 trace_id）' },
  { key: 'duplicates', label: '重复', icon: Copy, hint: '与其它技能的 Jaccard 相似度（只报告；合并/归档到「吞噬 · 存量治理」）' },
]

export interface DrawerSkill {
  id: string
  name?: string
  description_zh?: string
  description?: string
}

interface InstructionResp {
  ok?: boolean
  error?: string
  instruction?: string
  estimated_tokens?: number
  layer?: number
  /** file = 来自 skill.md；main_track = 该技能没有文件轨，回退主轨正文 */
  source?: string
  hint?: string
}

interface ScriptRow { name?: string; path?: string; size?: number; size_bytes?: number; modified?: string }

interface ScriptsResp {
  ok?: boolean
  error?: string
  scripts?: ScriptRow[]
  total?: number
}

interface ExecuteResp {
  ok?: boolean
  error?: string
  success?: boolean
  result?: unknown
  exit_code?: number
  duration_ms?: number
  timed_out?: boolean
  stderr?: string
}

interface ParamStatsResp {
  ok?: boolean
  error?: string
  param_stats?: Record<string, unknown>
  avoid_params?: unknown
  current_default_params?: Record<string, unknown>
  current_default_hash?: string | null
}

interface OptimizeResp {
  ok?: boolean
  error?: string
  recommendations?: unknown
  actions_taken?: unknown
  metrics_snapshot?: unknown
  feedback_summary?: unknown
}

interface FeedbackSummary {
  total_feedback?: number
  like_count?: number
  dislike_count?: number
  satisfaction_rate_percent?: number
  avg_rating?: number
  recommended_action?: string
  by_category?: Record<string, unknown>
}

interface FeedbackResp { ok?: boolean; error?: string; summary?: FeedbackSummary }

interface DupRow { skill_id?: string; id?: string; name?: string; jaccard?: number; content_type?: string }

interface DupsResp { ok?: boolean; error?: string; duplicates?: DupRow[]; total?: number }

interface MergeResp { ok?: boolean; error?: string; merge_id?: string; src_id?: string; dst_id?: string }

/** JSON 兜底序列化：面板只负责「如实地显示」，不猜后端字段。 */
function asJson(v: unknown): string {
  if (v === undefined || v === null) return ''
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

function Section({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-[11px] font-semibold text-slate-300">{title}</h4>
        {extra}
      </div>
      {children}
    </div>
  )
}

export default function SkillDetailDrawer({ skill, onClose }: { skill: DrawerSkill; onClose: () => void }) {
  const id = skill.id
  const [tab, setTab] = useState<Tab>('instruction')

  const [instruction, setInstruction] = useState<InstructionResp | null>(null)
  const [scripts, setScripts] = useState<ScriptsResp | null>(null)
  const [params, setParams] = useState<ParamStatsResp | null>(null)
  const [feedback, setFeedback] = useState<FeedbackSummary | null>(null)
  const [dups, setDups] = useState<DupsResp | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  // ── 合并（带备份/可撤销）：此前该动作只在已退役的「维护」Tab，重复列表只提示不给出口 ──
  const [mergePending, setMergePending] = useState('')
  const [mergeMsg, setMergeMsg] = useState('')
  const [mergeId, setMergeId] = useState('')

  // ── 执行表单 ──
  const [scriptName, setScriptName] = useState('main.py')
  const [paramsText, setParamsText] = useState('')
  const [timeoutSec, setTimeoutSec] = useState('')
  const [execResult, setExecResult] = useState<ExecuteResp | null>(null)
  // ── 反馈表单 ──
  const [fbTrace, setFbTrace] = useState('')
  const [fbType, setFbType] = useState('like')
  const [fbRating, setFbRating] = useState('5')
  const [fbComment, setFbComment] = useState('')
  const [fbDays, setFbDays] = useState('30')
  // ── 重复阈值 ──
  const [minJaccard, setMinJaccard] = useState('0.7')
  // ── 调参结果 ──
  const [optimize, setOptimize] = useState<OptimizeResp | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string }) => setErr(label + '：' + (r?.error || '后端返回 ok=false'))

  const loadInstruction = useCallback(async () => {
    setBusy('instruction'); setErr('')
    try {
      const r = await hubGet<InstructionResp>(skillInstruction(id))
      if (r?.ok === false) { bad('读取使用说明失败', r); setInstruction(null) } else setInstruction(r)
    } catch (e) {
      // 回退：老 /api/skills/content（2026-10-30「查看内容」并入详情后，这条 GET 由这里消费：
      // 运行时技能可能没有 skill.md/资产记录，instruction 端点会 404，而运行期元数据+正文只有它能给）
      try {
        const legacy = await hubGet<{ ok?: boolean; skill?: { content?: string } }>(
          SKILL_CONTENT + '?id=' + encodeURIComponent(id))
        const content = String(legacy?.skill?.content || '')
        if (legacy?.ok && content) {
          setInstruction({ ok: true, instruction: content, layer: 2, source: 'legacy_content',
            estimated_tokens: Math.ceil(content.length / 4) })
        } else { fail('读取使用说明失败', e) }
      } catch { fail('读取使用说明失败', e) }
    } finally { setBusy('') }
  }, [id])

  const loadScripts = useCallback(async () => {
    setBusy('scripts'); setErr('')
    try {
      const r = await hubGet<ScriptsResp>(skillScripts(id))
      if (r?.ok === false) { bad('读取脚本清单失败', r); setScripts(null) } else {
        setScripts(r)
        const first = r?.scripts?.[0]?.name
        if (first) setScriptName((cur) => (cur === 'main.py' ? first : cur))
      }
    } catch (e) { fail('读取脚本清单失败', e) } finally { setBusy('') }
  }, [id])

  const loadParams = useCallback(async () => {
    setBusy('params'); setErr('')
    try {
      const r = await hubGet<ParamStatsResp>(skillParamStats(id))
      if (r?.ok === false) { bad('读取参数统计失败', r); setParams(null) } else setParams(r)
    } catch (e) { fail('读取参数统计失败', e) } finally { setBusy('') }
  }, [id])

  const loadFeedback = useCallback(async (days = fbDays) => {
    setBusy('feedback'); setErr('')
    try {
      const r = await hubGet<FeedbackResp>(skillFeedback(id) + '?days=' + encodeURIComponent(days))
      if (r?.ok === false) { bad('读取反馈统计失败', r); setFeedback(null) } else setFeedback(r?.summary ?? null)
    } catch (e) { fail('读取反馈统计失败', e) } finally { setBusy('') }
  }, [id, fbDays])

  /** 合并到本技能（dst=当前技能）：后端先写两侧快照，返回 merge_id 可撤销 */
  const mergeInto = async (srcId: string) => {
    setBusy('merge'); setErr(''); setMergeMsg('')
    try {
      const r = await hubPost<MergeResp>(SKILLS_MGMT_ASSESS_MERGE_SAFE,
        { src_id: srcId, dst_id: id, strategy: 'auto' })
      if (r?.ok === false) { setErr('合并失败：' + (r.error || '后端返回 ok=false')); return }
      setMergeId(r?.merge_id ?? '')
      setMergePending('')
      setMergeMsg('已把「' + srcId + '」合并进本技能（已写备份，可用「撤销合并」回滚）。')
      await loadDups()
    } catch (e) { setErr('合并失败：' + (e instanceof Error ? e.message : String(e))) }
    finally { setBusy('') }
  }
  const undoMerge = async () => {
    if (!mergeId) return
    setBusy('merge'); setErr(''); setMergeMsg('')
    try {
      const r = await hubPost<MergeResp>(SKILLS_MGMT_ASSESS_MERGE_UNDO, { merge_id: mergeId })
      if (r?.ok === false) { setErr('撤销失败：' + (r.error || '后端返回 ok=false')); return }
      setMergeMsg('已撤销合并 ' + mergeId + '（被合并的技能已恢复）。')
      setMergeId('')
      await loadDups()
    } catch (e) { setErr('撤销失败：' + (e instanceof Error ? e.message : String(e))) }
    finally { setBusy('') }
  }

  const loadDups = useCallback(async (mj = minJaccard) => {
    setBusy('duplicates'); setErr('')
    try {
      const r = await hubGet<DupsResp>(skillDuplicates(id) + '?min_jaccard=' + encodeURIComponent(mj))
      if (r?.ok === false) { bad('读取重复技能失败', r); setDups(null) } else setDups(r)
    } catch (e) { fail('读取重复技能失败', e) } finally { setBusy('') }
  }, [id, minJaccard])

  // 打开即拉当前 Tab：只读、无副作用
  useEffect(() => {
    if (tab === 'instruction' && !instruction) void loadInstruction()
    if (tab === 'scripts' && !scripts) void loadScripts()
    if (tab === 'params' && !params) void loadParams()
    if (tab === 'feedback' && !feedback) void loadFeedback()
    if (tab === 'duplicates' && !dups) void loadDups()
  }, [tab, instruction, scripts, params, feedback, dups, loadInstruction, loadScripts, loadParams, loadFeedback, loadDups])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const runScript = async () => {
    setBusy('execute'); setErr(''); setExecResult(null)
    let parsed: unknown = undefined
    const txt = paramsText.trim()
    if (txt) {
      try {
        parsed = JSON.parse(txt)
      } catch {
        setErr('参数不是合法 JSON —— 没有发出请求（避免把一段坏参数送进沙箱）')
        setBusy('')
        return
      }
    }
    try {
      const body: Record<string, unknown> = { script_name: scriptName || 'main.py' }
      if (parsed !== undefined) body.params = parsed
      const t = timeoutSec.trim()
      if (t) body.timeout = Number(t)
      const r = await hubPost<ExecuteResp>(skillExecute(id), body)
      setExecResult(r)
      if (r?.ok === false && r?.error) setErr('执行失败：' + r.error)
    } catch (e) { fail('执行失败', e) } finally { setBusy('') }
  }

  const submitFeedback = async () => {
    setBusy('submit-feedback'); setErr('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(skillFeedback(id), {
        trace_id: fbTrace.trim(),
        feedback_type: fbType,
        rating: Number(fbRating || 0),
        comment: fbComment.trim(),
        category: 'other',
      })
      if (r?.ok === false) { bad('提交反馈失败', r); return }
      setFbComment('')
      await loadFeedback()
    } catch (e) { fail('提交反馈失败', e) } finally { setBusy('') }
  }

  const optimizeWithFeedback = async () => {
    setBusy('optimize'); setErr(''); setOptimize(null)
    try {
      const r = await hubPost<OptimizeResp>(skillOptimizeWithFeedback(id) + '?days=' + encodeURIComponent(fbDays), {})
      if (r?.ok === false) { bad('按反馈调参失败', r); return }
      setOptimize(r)
      await loadParams()
    } catch (e) { fail('按反馈调参失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const title = skill.name || id
  const desc = skill.description_zh || skill.description || ''

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-2xl flex-col border-l border-slate-700 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={'技能详情 · ' + title}
      >
        {/* 头部 */}
        <div className="border-b border-slate-800 px-4 py-3">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-semibold text-slate-100">{title}</h3>
              <p className="mt-0.5 truncate font-mono text-[10px] text-slate-500">{id}</p>
              {desc && <p className="mt-1 line-clamp-2 text-[11px] text-slate-400">{desc}</p>}
            </div>
            <button type="button" className={BTN} onClick={onClose} aria-label="关闭技能详情">
              <X size={12} />
            </button>
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-1">
            {TABS.map((t) => {
              const Icon = t.icon
              return (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setTab(t.key)}
                  aria-selected={tab === t.key}
                  title={t.hint}
                  className={'flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
                    tab === t.key
                      ? 'bg-cyan-500/15 font-medium text-cyan-300'
                      : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
                  )}
                >
                  <Icon size={11} />{t.label}
                </button>
              )
            })}
            <span className="ml-auto hidden font-mono text-[10px] text-slate-600 lg:inline">
              {TABS.find((t) => t.key === tab)?.hint}
            </span>
          </div>
        </div>

        {/* 内容 */}
        <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-4 py-3">
          {err && <ErrorBox message={err} />}

          {tab === 'instruction' && (
            <Section
              title={instruction?.source === 'main_track' ? '使用说明（主轨正文 · 该技能没有 skill.md）' : '使用说明（skill.md 正文）'}
              extra={
                <button type="button" className={BTN} onClick={() => void loadInstruction()} disabled={loading} aria-label="刷新使用说明">
                  {busy === 'instruction' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
                </button>
              }
            >
              {busy === 'instruction' && !instruction && <div className="text-[11px] text-slate-500">加载中…</div>}
              {instruction && (
                <>
                  <div className="mb-1.5 flex flex-wrap gap-2 font-mono text-[10px] text-slate-500">
                    <span>{'Layer ' + (instruction.layer ?? 2)}</span>
                    <span>约 {instruction.estimated_tokens ?? 0} tokens</span>
                    {instruction.source === 'main_track' && <span className="text-amber-400">来源：主轨回退</span>}
                  </div>
                  {instruction.hint && (
                    <p className="mb-2 rounded-md border border-amber-800/60 bg-amber-950/20 px-2 py-1 text-[11px] text-amber-300">{instruction.hint}</p>
                  )}
                  <pre className={PRE + ' max-h-104'}>{instruction.instruction || '（空）'}</pre>
                </>
              )}
              {!instruction && !loading && <div className="text-[11px] text-slate-500">没有取到说明正文。</div>}
            </Section>
          )}

          {tab === 'scripts' && (
            <>
              <Section
                title={'脚本清单（' + (scripts?.total ?? scripts?.scripts?.length ?? 0) + '）'}
                extra={
                  <button type="button" className={BTN} onClick={() => void loadScripts()} disabled={loading} aria-label="刷新脚本清单">
                    {busy === 'scripts' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
                  </button>
                }
              >
                {busy === 'scripts' && !scripts && <div className="text-[11px] text-slate-500">加载中…</div>}
                {scripts && (scripts.scripts?.length ?? 0) === 0 && (
                  <div className="text-[11px] text-slate-500">这个技能没有脚本（指令型技能只注入提示，不执行代码）。</div>
                )}
                {(scripts?.scripts?.length ?? 0) > 0 && (
                  <div className="flex flex-col gap-1">
                    {(scripts?.scripts ?? []).map((s, i) => {
                      const name = s.name || s.path || ('script-' + i)
                      return (
                        <button
                          key={name + i}
                          type="button"
                          onClick={() => setScriptName(name)}
                          title="选它作为「要执行的脚本」"
                          className={'flex items-center gap-2 rounded-sm border px-2 py-1 text-left text-[11px] transition-colors ' + (
                            scriptName === name
                              ? 'border-cyan-700/70 bg-cyan-500/10 text-cyan-200'
                              : 'border-slate-800 bg-slate-950/60 text-slate-300 hover:bg-slate-800/60'
                          )}
                        >
                          <FileCode size={11} />
                          <span className="font-mono">{name}</span>
                          <span className="ml-auto font-mono text-[10px] text-slate-500">
                            {s.size_bytes ?? s.size ?? ''} {s.modified ? '· ' + s.modified : ''}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                )}
              </Section>

              <Section title="沙箱执行（Layer 3）">
                <label className="mb-2 block">
                  <span className="mb-1 block text-[11px] text-slate-400">脚本名</span>
                  <input className={INPUT} value={scriptName} onChange={(e) => setScriptName(e.target.value)} aria-label="脚本名" />
                </label>
                <label className="mb-2 block">
                  <span className="mb-1 block text-[11px] text-slate-400">参数（JSON，可留空）</span>
                  <textarea
                    className={INPUT + ' h-20 font-mono'}
                    value={paramsText}
                    onChange={(e) => setParamsText(e.target.value)}
                    placeholder='{"query": "..."}'
                    aria-label="执行参数 JSON"
                  />
                </label>
                <label className="mb-2 block">
                  <span className="mb-1 block text-[11px] text-slate-400">超时（秒，可留空 = 后端默认）</span>
                  <input className={INPUT} value={timeoutSec} onChange={(e) => setTimeoutSec(e.target.value)} aria-label="执行超时秒" />
                </label>
                <button type="button" className={BTN_EM} onClick={() => void runScript()} disabled={loading} aria-label="执行脚本">
                  {busy === 'execute' ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} 在沙箱里执行
                </button>
                {execResult && (
                  <div className="mt-2 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      {execResult.success
                        ? <span className="flex items-center gap-1 text-emerald-400"><CheckCircle2 size={11} /> 成功</span>
                        : <span className="flex items-center gap-1 text-red-400"><AlertTriangle size={11} /> 失败</span>}
                      <span className="font-mono text-[10px] text-slate-500">
                        exit={execResult.exit_code ?? '-'} · {execResult.duration_ms ?? 0}ms
                        {execResult.timed_out ? ' · 超时' : ''}
                      </span>
                    </div>
                    {execResult.error && <div className="text-[11px] text-red-300">{execResult.error}</div>}
                    {execResult.result !== undefined && execResult.result !== null && (
                      <pre className={PRE}>{asJson(execResult.result)}</pre>
                    )}
                    {execResult.stderr && (
                      <pre className={PRE + ' text-amber-300'}>{String(execResult.stderr).slice(0, 4000)}</pre>
                    )}
                  </div>
                )}
              </Section>
            </>
          )}

          {tab === 'params' && (
            <>
              <Section
                title="参数级执行统计"
                extra={
                  <button type="button" className={BTN} onClick={() => void loadParams()} disabled={loading} aria-label="刷新参数统计">
                    {busy === 'params' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
                  </button>
                }
              >
                {busy === 'params' && !params && <div className="text-[11px] text-slate-500">加载中…</div>}
                {params && Object.keys(params.param_stats ?? {}).length === 0 && (
                  <div className="text-[11px] text-slate-500">还没有参数级样本（执行过带参数的脚本后才会出现）。</div>
                )}
                {params && Object.keys(params.param_stats ?? {}).length > 0 && (
                  <div className="flex flex-col gap-1">
                    {Object.entries(params.param_stats ?? {}).map(([name, stat]) => (
                      <div key={name} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1">
                        <div className="font-mono text-[11px] text-cyan-300">{name}</div>
                        <div className="font-mono text-[10px] text-slate-400">{asJson(stat)}</div>
                      </div>
                    ))}
                  </div>
                )}
                {params?.avoid_params !== undefined && params?.avoid_params !== null && (
                  <div className="mt-2">
                    <div className="mb-1 text-[11px] text-amber-300">建议回避的参数</div>
                    <pre className={PRE}>{asJson(params.avoid_params)}</pre>
                  </div>
                )}
                {params?.current_default_params && (
                  <div className="mt-2">
                    <div className="mb-1 text-[11px] text-slate-400">
                      当前默认参数
                      {params.current_default_hash ? <span className="ml-1 font-mono text-[10px] text-slate-600">#{params.current_default_hash}</span> : null}
                    </div>
                    <pre className={PRE}>{asJson(params.current_default_params)}</pre>
                  </div>
                )}
              </Section>

              <Section title="按反馈一键调参">
                <p className="mb-2 text-[11px] text-slate-500">
                  拉取最近一段时间的反馈统计，再按它给出参数调整建议（有实际改动才返回 actions_taken）。
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-1 text-[11px] text-slate-400">
                    统计窗口（天）
                    <input className={INPUT + ' w-16'} value={fbDays} onChange={(e) => setFbDays(e.target.value)} aria-label="调参统计窗口天数" />
                  </label>
                  <button type="button" className={BTN_EM} onClick={() => void optimizeWithFeedback()} disabled={loading} aria-label="按反馈调参">
                    {busy === 'optimize' ? <Loader2 size={11} className="animate-spin" /> : <Gauge size={11} />} 拉反馈 + 调参
                  </button>
                </div>
                {optimize && (
                  <div className="mt-2 space-y-1.5">
                    {optimize.recommendations !== undefined && (
                      <>
                        <div className="text-[11px] text-slate-400">建议</div>
                        <pre className={PRE}>{asJson(optimize.recommendations)}</pre>
                      </>
                    )}
                    {optimize.actions_taken !== undefined && (
                      <>
                        <div className="text-[11px] text-slate-400">已执行</div>
                        <pre className={PRE}>{asJson(optimize.actions_taken)}</pre>
                      </>
                    )}
                  </div>
                )}
              </Section>
            </>
          )}

          {tab === 'feedback' && (
            <>
              <Section
                title="满意度聚合"
                extra={
                  <button type="button" className={BTN} onClick={() => void loadFeedback()} disabled={loading} aria-label="刷新反馈统计">
                    {busy === 'feedback' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
                  </button>
                }
              >
                {busy === 'feedback' && !feedback && <div className="text-[11px] text-slate-500">加载中…</div>}
                {feedback && (
                  <div className="grid grid-cols-2 gap-1.5 text-[11px] sm:grid-cols-4">
                    {[
                      ['总反馈', feedback.total_feedback],
                      ['好评', feedback.like_count],
                      ['差评', feedback.dislike_count],
                      ['满意度', feedback.satisfaction_rate_percent],
                      ['均分', feedback.avg_rating],
                    ].map(([k, v]) => (
                      <div key={String(k)} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1">
                        <div className="text-[10px] text-slate-500">{String(k)}</div>
                        <div className="font-mono text-cyan-300">{v ?? '-'}</div>
                      </div>
                    ))}
                    {feedback.recommended_action && (
                      <div className="col-span-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 sm:col-span-4">
                        <div className="text-[10px] text-slate-500">建议动作</div>
                        <div className="text-slate-300">{feedback.recommended_action}</div>
                      </div>
                    )}
                  </div>
                )}
                {feedback?.by_category && (
                  <div className="mt-2">
                    <div className="mb-1 text-[11px] text-slate-400">按分类</div>
                    <pre className={PRE}>{asJson(feedback.by_category)}</pre>
                  </div>
                )}
              </Section>

              <Section title="提交一条反馈">
                <p className="mb-2 text-[11px] text-slate-500">
                  后端要求 trace_id 非空（反馈要能对回一次具体调用）—— 留空会被 400 拒掉，故此处先校验。
                </p>
                <label className="mb-2 block">
                  <span className="mb-1 block text-[11px] text-slate-400">trace_id</span>
                  <input className={INPUT} value={fbTrace} onChange={(e) => setFbTrace(e.target.value)} aria-label="反馈 trace_id" />
                </label>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-1 text-[11px] text-slate-400">
                    类型
                    <select className={INPUT + ' w-24'} value={fbType} onChange={(e) => setFbType(e.target.value)} aria-label="反馈类型">
                      <option value="like">like</option>
                      <option value="dislike">dislike</option>
                      <option value="report">report</option>
                      <option value="suggestion">suggestion</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1 text-[11px] text-slate-400">
                    评分(0-5)
                    <input className={INPUT + ' w-16'} value={fbRating} onChange={(e) => setFbRating(e.target.value)} aria-label="反馈评分" />
                  </label>
                </div>
                <label className="mb-2 block">
                  <span className="mb-1 block text-[11px] text-slate-400">备注</span>
                  <textarea className={INPUT + ' h-16'} value={fbComment} onChange={(e) => setFbComment(e.target.value)} aria-label="反馈备注" />
                </label>
                <button
                  type="button"
                  className={BTN_EM}
                  onClick={() => void submitFeedback()}
                  disabled={loading || fbTrace.trim() === ''}
                  title={fbTrace.trim() === '' ? '先填 trace_id' : '提交'}
                  aria-label="提交反馈"
                >
                  {busy === 'submit-feedback' ? <Loader2 size={11} className="animate-spin" /> : <MessageSquare size={11} />} 提交反馈
                </button>
              </Section>
            </>
          )}

          {tab === 'duplicates' && (
            <Section
              title={'重复技能（' + (dups?.total ?? dups?.duplicates?.length ?? 0) + '）'}
              extra={
                <div className="flex items-center gap-1.5">
                  <label className="flex items-center gap-1 text-[10px] text-slate-500">
                    Jaccard ≥
                    <input className={INPUT + ' w-16'} value={minJaccard} onChange={(e) => setMinJaccard(e.target.value)} aria-label="重复阈值" />
                  </label>
                  <button type="button" className={BTN} onClick={() => void loadDups()} disabled={loading} aria-label="刷新重复技能">
                    {busy === 'duplicates' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 查重
                  </button>
                </div>
              }
            >
              {busy === 'duplicates' && !dups && <div className="text-[11px] text-slate-500">加载中…</div>}
              {dups && (dups.duplicates?.length ?? 0) === 0 && (
                <div className="text-[11px] text-slate-500">没有超过阈值的重复技能。</div>
              )}
              {(dups?.duplicates?.length ?? 0) > 0 && (
                <div className="flex flex-col gap-1">
                  {(dups?.duplicates ?? []).map((d, i) => {
                    const key = d.skill_id || d.id || ('dup-' + i)
                    return (
                      <div key={key} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                        <span className="font-mono text-cyan-300">{key}</span>
                        <span className="truncate text-slate-300">{d.name ?? ''}</span>
                        <span className="ml-auto font-mono text-[10px] text-amber-300">
                          {d.jaccard !== undefined ? Number(d.jaccard).toFixed(3) : ''}
                        </span>
                        {mergePending === key ? (
                          <span className="flex flex-wrap items-center gap-1">
                            <span className="text-[10px] text-amber-300">确认合并？会删除它并写备份</span>
                            <button type="button" className={BTN} onClick={() => void mergeInto(key)} disabled={busy === 'merge'}>确认合并</button>
                            <button type="button" className={BTN} onClick={() => setMergePending('')}>取消</button>
                          </span>
                        ) : (
                          <button type="button" className={BTN} onClick={() => setMergePending(key)}
                            disabled={busy !== ''} title="把该技能合并进本技能：先写两侧快照（可撤销），再删源技能">
                            合并到本技能
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              {mergeMsg && (
                <p className="mt-2 rounded-md border border-emerald-800/60 bg-emerald-950/20 px-2 py-1 text-[11px] text-emerald-300">{mergeMsg}</p>
              )}
              {mergeId && (
                <button type="button" className={BTN + ' mt-2'} onClick={() => void undoMerge()} disabled={busy === 'merge'}>
                  撤销合并（{mergeId}）
                </button>
              )}
              <p className="mt-2 text-[10px] text-slate-600">
                合并 = 保留本技能、把对方内容作为一条版本并入并删除对方（**先写两侧快照**，可在这里撤销）。
                这里按 Jaccard 阈值筛候选；「操作面重叠 / 共享触发面」的提示在报告面板里，两套口径互相独立。
              </p>
            </Section>
          )}
        </div>
      </div>
    </div>
  )
}
