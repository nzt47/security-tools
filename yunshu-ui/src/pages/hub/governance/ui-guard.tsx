/**
 * 治理与运营 · 规范守卫（2026-11-19）
 *
 * 把「UI 封装规范」从文档变成可执行检查的操作面：
 *  - 概览：错误/警告、量具基线（只降不升）、规则分布；
 *  - 违规明细：file:line + 修复建议，可按规则过滤；
 *  - 自动修复：只有 button.alignment / heading.scale 两类（字面量 className），
 *    先 dry_run 看 diff，再 confirm 落盘（后端默认 dry_run）。
 *
 * 数据源：GET /api/ui-guard/check|rules、POST /api/ui-guard/fix（需令牌）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, ShieldCheck, Wrench } from 'lucide-react'

import {
  Badge, Button, Card, DataTable, ErrorBox, Loading, Notice, PageTabsHeader,
  SectionTitle, Select, Tabs, hubGet, hubPost,
} from '../components/ui'

type Tab = 'overview' | 'findings'

interface Finding {
  rule: string
  severity: 'error' | 'warning' | 'info'
  file: string
  line: number
  message: string
  fix_hint: string
  fixable: boolean
  snippet?: string
}

interface Baseline {
  path: string
  current: Record<string, number>
  regressions: Array<{ metric: string; baseline: number; current: number; delta: number }>
  passed: boolean
}

interface Report {
  schema_version: number
  mode: string
  generated_at: string
  commit: string
  counts: { total: number; error: number; warning: number; info: number }
  by_rule: Record<string, number>
  scanned_files: number
  baseline: Baseline
  errors: string[]
  duration_ms: number
  findings: Finding[]
  findings_total: number
}

interface RuleDef { id: string; label: string; fixable: boolean; why: string }
interface FixChange { rule: string; file: string; line: number; before: string; after: string; token?: string }
interface FixResult {
  applied: boolean
  dry_run: boolean
  change_count: number
  changes: FixChange[]
  files: string[]
  errors: string[]
}

const RULE_LABEL: Record<string, string> = {
  'button.alignment': 'Button 对齐',
  'heading.scale': '标题字号 token',
  'tabs.order': '标题在 Tab 上面',
  'audit.raw_control': '原生控件',
  'audit.arbitrary_text': '任意值字号',
  'audit.raw_heading': '原生标题标签',
  'audit.css_hardcoded_color': 'CSS 硬编码颜色',
}

const TONE: Record<string, 'red' | 'amber' | 'slate'> = {
  error: 'red', warning: 'amber', info: 'slate',
}

export default function UiGuardPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [report, setReport] = useState<Report | null>(null)
  const [rules, setRules] = useState<RuleDef[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [ruleFilter, setRuleFilter] = useState('')
  const [fixBusy, setFixBusy] = useState(false)
  const [preview, setPreview] = useState<FixResult | null>(null)
  const [fixMsg, setFixMsg] = useState('')
  const [fixErr, setFixErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const body = await hubGet<{ ok: boolean; report: Report; error?: string }>('/api/ui-guard/check?limit=500')
      if (!body?.ok) throw new Error(body?.error || '检查失败')
      setReport(body.report)
    } catch (e) {
      setErr('检查失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    hubGet<{ ok: boolean; rules: RuleDef[] }>('/api/ui-guard/rules')
      .then((b) => { if (b?.ok) setRules(b.rules) })
      .catch(() => { /* 规则清单只是分组辅助，失败不挡主流程 */ })
  }, [])

  const findings = useMemo(() => {
    const list = report?.findings ?? []
    return ruleFilter ? list.filter((f) => f.rule === ruleFilter) : list
  }, [report, ruleFilter])

  const baselineRows = useMemo(() => {
    const b = report?.baseline
    if (!b) return []
    return Object.entries(b.current).map(([metric, current]) => ({
      metric,
      current,
      baseline: Object.prototype.hasOwnProperty.call(b, metric) ? b.current[metric] : null,
      delta: 0,
    }))
  }, [report])

  const runFix = async (apply: boolean) => {
    setFixBusy(true); setFixErr(''); setFixMsg('')
    try {
      const body = await hubPost<{ ok: boolean; result: FixResult; error?: string }>('/api/ui-guard/fix', {
        dry_run: !apply,
        confirm: apply,
        rules: ['button.alignment', 'heading.scale'],
        max_files: 20,
      })
      if (!body?.ok) throw new Error(body?.error || '修复失败')
      setPreview(body.result)
      setFixMsg(apply
        ? '已落盘 ' + body.result.change_count + ' 处（' + body.result.files.length + ' 个文件）'
        : '预览 ' + body.result.change_count + ' 处（未写盘）')
      if (apply) await load()
    } catch (e) {
      setFixErr('修复失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setFixBusy(false)
    }
  }

  const tabs = (
    <Tabs
      variant="pill"
      size="sm"
      aria-label="规范守卫视图"
      value={tab}
      onChange={(v) => setTab(v as Tab)}
      items={[
        { value: 'overview', label: <>概览</> },
        { value: 'findings', label: <>违规明细 {report ? '(' + report.findings_total + ')' : ''}</> },
      ]}
    />
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageTabsHeader
        icon={<ShieldCheck size={14} className="text-primary" />}
        title="规范守卫"
        hint={tab === 'overview' ? 'UI 封装规范 · 量具基线只降不升' : '按 file:line 修；两类可用自动修复'}
        actions={(
          <Button type="button" variant="default" size="sm" onClick={() => void load()} disabled={loading} aria-label="重新检查">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> 重新检查
          </Button>
        )}
        tabs={tabs}
      />

      <div className="min-h-0 flex-1 overflow-auto bg-slate-950 px-5 py-4">
        {err && <div className="mb-3"><ErrorBox message={err} /></div>}
        {loading && !report ? <Loading /> : null}
        {report ? (
          <>
            {!report.baseline.passed && (
              <div className="mb-3">
                <ErrorBox message={'量具升高（基线只降不升）：' + report.baseline.regressions
                  .map((r) => r.metric + ' ' + r.baseline + '→' + r.current).join('；')} />
              </div>
            )}
            {report.errors.length > 0 && (
              <div className="mb-3">
                <Notice tone="warning" size="sm">{report.errors.join('；')}</Notice>
              </div>
            )}

            {tab === 'overview' ? (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
                  <StatBox label="错误" value={report.counts.error} tone="text-red-400" />
                  <StatBox label="警告" value={report.counts.warning} tone="text-amber-300" />
                  <StatBox label="扫描文件" value={report.scanned_files} tone="text-cyan-300" />
                  <StatBox label="基线回归" value={report.baseline.regressions.length} tone={report.baseline.passed ? 'text-emerald-400' : 'text-red-400'} />
                </div>
                <Card title="量具基线（只降不升）">
                  <DataTable
                    data={baselineRows}
                    keyField="metric"
                    columns={[
                      { key: 'metric', title: '指标' },
                      { key: 'current', title: '当前', render: (r) => <span className="font-mono">{r.current}</span> },
                      { key: 'baseline', title: '基线', render: (r) => <span className="font-mono">{String(r.baseline ?? '—')}</span> },
                    ]}
                  />
                  <p className="mt-2 text-caption text-muted-foreground">
                    基线文件 {report.baseline.path}；升高即失败（CI 的「代码质量检查」同口径）。检查耗时 {report.duration_ms} ms · commit {report.commit || '—'}。
                  </p>
                </Card>
                <div className="mt-4">
                  <Card title="规则分布">
                    <DataTable
                      data={Object.entries(report.by_rule).map(([rule, n]) => ({ rule, n }))}
                      keyField="rule"
                      columns={[
                        { key: 'rule', title: '规则', render: (r) => RULE_LABEL[r.rule] ?? r.rule },
                        { key: 'n', title: '条数', render: (r) => <span className="font-mono">{r.n}</span> },
                      ]}
                    />
                  </Card>
                </div>
              </>
            ) : (
              <>
                <SectionTitle
                  title="违规明细"
                  description="只显示当前检查结果；自动修复仅覆盖 Button 对齐与标题字号两类（字面量 className）"
                  dot={false}
                  actions={(
                    <>
                      <Select
                        size="sm"
                        className="w-48"
                        value={ruleFilter}
                        onChange={setRuleFilter}
                        options={[{ label: '全部规则', value: '' }, ...rules.map((r) => ({ label: r.label, value: r.id }))]}
                        aria-label="按规则过滤"
                      />
                      <Button type="button" variant="default" size="sm" disabled={fixBusy}
                        onClick={() => void runFix(false)} aria-label="自动修复预览">
                        <Wrench size={12} /> 自动修复预览
                      </Button>
                      <Button type="button" variant="primary" size="sm" disabled={fixBusy || !preview || preview.change_count === 0}
                        onClick={() => void runFix(true)} aria-label="确认落盘">
                        确认落盘
                      </Button>
                    </>
                  )}
                />
                {fixErr && <div className="mb-3"><ErrorBox message={fixErr} /></div>}
                {fixMsg && <div className="mb-3"><Notice tone="success" size="sm">{fixMsg}</Notice></div>}
                {preview && preview.changes.length > 0 && (
                  <Card title={'修复预览（' + preview.change_count + ' 处 · ' + (preview.dry_run ? '未写盘' : '已写盘') + '）'} className="mb-4">
                    <DataTable
                      data={preview.changes.map((c, i) => ({ ...c, key: c.file + ':' + c.line + ':' + i }))}
                      keyField="key"
                      columns={[
                        { key: 'file', title: '文件', render: (c) => <span className="font-mono text-caption">{c.file}:{c.line}</span> },
                        { key: 'rule', title: '规则', render: (c) => RULE_LABEL[c.rule] ?? c.rule },
                        { key: 'before', title: '改前', render: (c) => <code className="text-micro text-slate-400">{c.before}</code> },
                        { key: 'after', title: '改后', render: (c) => <code className="text-micro text-emerald-300">{c.after}</code> },
                      ]}
                    />
                  </Card>
                )}
                <Card title={'明细（' + findings.length + ' / ' + report.findings_total + '）'}>
                  <DataTable
                    data={findings.map((f, i) => ({ ...f, key: f.file + ':' + f.line + ':' + f.rule + ':' + i }))}
                    keyField="key"
                    columns={[
                      { key: 'severity', title: '级别', render: (f) => <Badge color={TONE[f.severity] ?? 'slate'}>{f.severity}</Badge> },
                      { key: 'rule', title: '规则', render: (f) => RULE_LABEL[f.rule] ?? f.rule },
                      { key: 'file', title: '位置', render: (f) => <span className="font-mono text-caption">{f.file}:{f.line}</span> },
                      { key: 'message', title: '说明', render: (f) => <span className="text-caption text-slate-300">{f.message}</span> },
                      { key: 'fix_hint', title: '修复建议', render: (f) => <span className="text-caption text-slate-400">{f.fix_hint}</span> },
                      { key: 'fixable', title: '可自动修', render: (f) => f.fixable ? <Badge color="green">是</Badge> : <Badge color="slate">否</Badge> },
                    ]}
                  />
                  {findings.length === 0 && <p className="mt-2 text-caption text-muted-foreground">当前过滤下没有违规。</p>}
                </Card>
              </>
            )}
            <p className="mt-3 text-caption text-slate-500">
              口径：docs/ui/封装规范.md · 规则与 vitest 守卫同源（Button.alignment / pageHeaderOrder / headingScale）。
            </p>
          </>
        ) : null}
      </div>
    </div>
  )
}

function StatBox({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-card border border-border bg-card p-4">
      <div className="text-micro text-muted-foreground">{label}</div>
      <div className={'mt-1 font-mono text-h2 ' + tone}>{value}</div>
    </div>
  )
}
