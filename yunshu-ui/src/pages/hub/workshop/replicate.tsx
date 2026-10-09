/**
 * 装配车间 —— 系统自我复制 / 可带走 bundle（S5）
 * ------------------------------------------------------------------
 * 【它解决什么】本页此前是纯说明页（零 API 调用），而"可带走"这件事**只有后端知道**
 * （bundle 契约 + 密钥闸 + 导出端点）。本页改为**真实导出面**：填分身名 → 调
 * GET /api/subagent/<name>/bundle → 展示可复制的 JSON；失败如实显示错误。
 *
 * 【为什么不在前端拼 bundle/校验】契约的唯一权威是 agent/subagent/bundle.py；前端
 * 自己拼一份或自己判"合法"就是第二份口径（那正是本仓反复记录的漂移形态）。本页只做
 * "发起 + 展示"：bundle 合法性一律由后端 validate_bundle 裁定（非法 400 不建容器）。
 *
 * 【导入面（2026-10-09 补齐）】选文件或粘贴 JSON → POST /api/subagent/import。
 * 前端只发**原样文本/对象**，不做本地结构判定（那会与后端两份口径）；唯一的本地
 * 处理是"是不是合法 JSON"，因为它决定要不要发这次请求。
 *
 * 【如实标注】离线运行**部分接线**：local（本地推理）与 container（隔离运行）已进
 * runtime.backend 词表；wheelhouse 打包**未做** ⇒ environment.offline_ready 恒 false。
 * 页面只显示后端给的 environment/offline_ready 与导入 problems，不复算（判定权威在后端）。
 */
import { useState } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import { ClipboardCopy, Loader2, Package, RefreshCw, Upload } from 'lucide-react'
import { Card, ErrorBox, PageHeader } from '../components/ui'
import { ApiError, request } from '@/lib/apiClient'
import { SUBAGENT_BUNDLE_BY_NAME, SUBAGENT_IMPORT } from '@/api/endpoints'

/** 导出响应（后端 jsonify({"ok": True, "bundle": ...})；兼容统一信封的 data 包法） */
interface BundleEnvelope {
  bundle?: unknown
  data?: { bundle?: unknown }
}

/** bundle.environment 离线依赖清单（后端 agent/subagent/dependencies.py 产；前端只读展示） */
interface BundleEnvironment {
  source?: string
  source_status?: string
  offline_ready?: boolean
  satisfied_locally?: boolean
  reason?: string
  counts?: Record<string, number>
  python?: { required?: string; running?: string; status?: string }
  artifacts?: { mode?: string; count?: number }
  items?: { name?: string; required?: string; installed?: string | null; status?: string }[]
}

/** 导入端对拍（后端 check_against_bundle；offline_ready 恒 false，导入成功 ≠ 可离线跑） */
interface ImportCheck {
  satisfied?: boolean
  missing?: string[]
  version_mismatch?: string[]
  python_ok?: boolean
  offline_ready?: boolean
  reason?: string
}

/** 导入响应（后端 {ok, subagent, imported, environment, environment_check}；兼容 data 包法） */
interface ImportEnvelope {
  ok?: boolean
  subagent?: { name?: string }
  imported?: { bundle_id?: string; backend?: string }
  error?: string
  error_code?: string
  problems?: string[]
  environment?: BundleEnvironment
  environment_check?: ImportCheck
  data?: ImportEnvelope
}

/** 从导出的 bundle 里取 environment 段（只读；判定口径仍在后端） */
function bundleEnvironment(value: unknown): BundleEnvironment | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const env = (value as { environment?: unknown }).environment
  return env && typeof env === 'object' ? (env as BundleEnvironment) : undefined
}

/** 离线就绪徽标：恒 false 是有意为之（未打包 wheel），用中性色如实展示 */
function EnvFlag({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span
      className={ok
        ? 'rounded bg-emerald-950/40 px-2 py-0.5 text-[11px] text-emerald-300'
        : 'rounded bg-amber-950/40 px-2 py-0.5 text-[11px] text-amber-300'}
    >{text}</span>
  )
}

export default function WorkshopReplicate() {
  const [name, setName] = useState('')
  const [bundle, setBundle] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  // ── 导入面 ──
  const [importText, setImportText] = useState('')
  const [importError, setImportError] = useState('')
  const [importMessage, setImportMessage] = useState('')
  const [importing, setImporting] = useState(false)
  const [importProblems, setImportProblems] = useState<string[]>([])
  const [importCheck, setImportCheck] = useState<ImportCheck | null>(null)

  const text = bundle === null ? '' : JSON.stringify(bundle, null, 2)
  const env = bundleEnvironment(bundle)

  const exportBundle = async () => {
    const target = name.trim()
    if (!target) { setError('请先填写要导出的分身名称'); return }
    setBusy(true); setError(''); setMessage(''); setBundle(null)
    try {
      const res = await request<unknown>(SUBAGENT_BUNDLE_BY_NAME(target))
      const envelope = (res || {}) as BundleEnvelope
      const payload = envelope.data ?? envelope
      if (!payload || payload.bundle === undefined) {
        throw new Error('导出响应缺少 bundle 字段（后端契约未对齐）')
      }
      setBundle(payload.bundle)
      setMessage('已导出「' + target + '」的 bundle（不含任何密钥值）')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setMessage('bundle JSON 已复制到剪贴板')
    } catch {
      setMessage('复制失败（浏览器未授权剪贴板）')
    }
  }

  const onEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') void exportBundle()
  }

  /** 选文件只做"读进文本框"：真正的合法性判定交给后端（单一权威） */
  const onPickFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    setImportError(''); setImportMessage('')
    try {
      const content = await file.text()
      setImportText(content)
      setImportMessage('已读取 ' + file.name + '（点「导入」提交）')
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err))
    }
  }

  const importBundle = async () => {
    const raw = importText.trim()
    if (!raw) { setImportError('请先选择或粘贴 bundle JSON'); return }
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      // 只有"能不能解析成 JSON"在本地判：它决定要不要发请求；结构合法性仍归后端
      setImportError('不是合法 JSON：请确认贴入的是导出的 bundle 文本')
      return
    }
    setImporting(true); setImportError(''); setImportMessage('')
    setImportProblems([]); setImportCheck(null)
    try {
      const res = await request<ImportEnvelope>(SUBAGENT_IMPORT, {
        method: 'POST',
        body: { bundle: parsed },
      })
      const payload = (res && (res.data ?? res)) as ImportEnvelope
      const imported = payload.imported || {}
      const who = (payload.subagent && payload.subagent.name) || '新分身'
      setImportMessage(
        '已导入 ' + who + '（bundle_id=' + (imported.bundle_id || '?')
        + '，backend=' + (imported.backend || '?') + '）',
      )
      setImportCheck(payload.environment_check || null)
    } catch (e) {
      // 非法 bundle 是 400：后端把逐条问题放在顶层 problems（ApiError.details）。
      // 前端只如实列出，不复算合法性（判定权威仍是后端 validate_bundle）。
      if (e instanceof ApiError) {
        const detail = (e.details || {}) as { problems?: unknown }
        if (Array.isArray(detail.problems)) {
          setImportProblems(detail.problems.map((x) => String(x)))
        }
      }
      setImportError(e instanceof Error ? e.message : String(e))
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader
        title="系统自我复制"
        description="装配车间 —— 可带走 bundle：导出与导入都是真实端点，离线运行如实标注"
      />

      <Card title="导出可带走 bundle">
        <p className="text-sm leading-6 text-slate-400">
          把一个分身的身份、装配、引用式密钥与启动协议导出为一个 JSON 包
          （<code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">GET /api/subagent/&lt;name&gt;/bundle</code>）。
          导出前整包过一次密钥闸：任何长期密钥形态都会被**拒绝导出**（只存引用，不存值）。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={onEnter}
            data-testid="replicate-name"
            placeholder="分身名称（如 sa-1）"
            className="w-64 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none"
          />
          <button
            type="button"
            onClick={exportBundle}
            disabled={busy}
            data-testid="replicate-export"
            className="flex items-center gap-1.5 rounded-lg bg-cyan-600 px-4 py-2 text-sm text-white hover:bg-cyan-500 disabled:opacity-40"
          >
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />} 导出 bundle
          </button>
          {text && (
            <button
              type="button"
              onClick={copy}
              data-testid="replicate-copy"
              className="flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
            ><ClipboardCopy size={12} /> 复制 JSON</button>
          )}
        </div>
        {error && (
          <div className="mt-3" data-testid="replicate-error"><ErrorBox message={error} /></div>
        )}
        {message && (
          <div
            className="mt-3 rounded-lg border border-emerald-800/60 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300"
            data-testid="replicate-message"
          >{message}</div>
        )}
        {text && (
          <pre
            data-testid="replicate-json"
            className="mt-3 max-h-[420px] overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-[11px] leading-5 text-slate-300"
          >{text}</pre>
        )}
      </Card>

      {env && (
        <Card className="mt-4" title="离线依赖清单（bundle.environment）">
          <div className="flex flex-wrap items-center gap-2">
            <EnvFlag ok={env.offline_ready === true}
              text={'offline_ready=' + String(env.offline_ready === true)} />
            <span className="text-[11px] text-slate-500">
              satisfied_locally={String(env.satisfied_locally === true)}（只说明导出机装了）
            </span>
            <span className="text-[11px] text-slate-500">source_status={env.source_status || '?'}</span>
            <span className="text-[11px] text-slate-500">artifacts.mode={env.artifacts?.mode || '?'}</span>
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">{env.reason || ''}</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
            <span>declared={env.counts?.declared ?? 0}</span>
            <span>ok={env.counts?.ok ?? 0}</span>
            <span>missing={env.counts?.missing ?? 0}</span>
            <span>version_mismatch={env.counts?.version_mismatch ?? 0}</span>
            <span>unknown={env.counts?.unknown ?? 0}</span>
            <span>python={env.python?.running || '?'}（需 {env.python?.required || '未声明'}）</span>
          </div>
        </Card>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="导入可带走 bundle">
          <p className="text-sm leading-6 text-slate-400">
            选择或粘贴一个 bundle JSON → 调
            <code className="mx-1 rounded bg-slate-800 px-1 text-xs text-cyan-400">POST {SUBAGENT_IMPORT}</code>
            。bundle 的合法性（结构 / 版本 / 引用式密钥 / 后端词表）**一律由后端裁定**：
            非法即 400 且不建容器，前端只如实展示错误与 problems。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              type="file"
              accept="application/json,.json"
              onChange={onPickFile}
              data-testid="replicate-import-file"
              className="text-xs text-slate-400 file:mr-2 file:rounded-lg file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-xs file:text-slate-100"
            />
            <button
              type="button"
              onClick={importBundle}
              disabled={importing}
              data-testid="replicate-import"
              className="flex items-center gap-1.5 rounded-lg bg-cyan-600 px-4 py-2 text-sm text-white hover:bg-cyan-500 disabled:opacity-40"
            >
              {importing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} 导入
            </button>
          </div>
          <textarea
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            data-testid="replicate-import-text"
            placeholder="或直接粘贴 bundle JSON 文本"
            className="mt-3 h-28 w-full rounded-lg border border-slate-700 bg-slate-900 p-2 font-mono text-[11px] leading-5 text-slate-200 placeholder-slate-600 outline-none"
          />
          {importError && (
            <div className="mt-3" data-testid="replicate-import-error"><ErrorBox message={importError} /></div>
          )}
          {importMessage && (
            <div
              className="mt-3 rounded-lg border border-emerald-800/60 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300"
              data-testid="replicate-import-message"
            >{importMessage}</div>
          )}
          {importProblems.length > 0 && (
            <div
              className="mt-3 rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-xs text-rose-300"
              data-testid="replicate-import-problems"
            >
              <div className="font-medium">后端逐条问题（problems）</div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {importProblems.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          )}
          {importCheck && (
            <div
              className="mt-3 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-[11px] leading-5 text-slate-400"
              data-testid="replicate-import-envcheck"
            >
              到达端对拍：satisfied={String(importCheck.satisfied === true)}
              ／python_ok={String(importCheck.python_ok === true)}
              ／offline_ready=false
              {(importCheck.missing?.length ?? 0) > 0
                ? '；缺失：' + (importCheck.missing || []).join('、') : ''}
              {(importCheck.version_mismatch?.length ?? 0) > 0
                ? '；版本不符：' + (importCheck.version_mismatch || []).join('、') : ''}
            </div>
          )}
        </Card>

        <Card title="离线运行（部分接线）">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-500/15 text-slate-400"><RefreshCw size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              bundle 的 <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">runtime.backend</code>
              现映射四档：<code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">inproc</code> /
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">subprocess</code> /
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">local</code>（断网可跑，需本机 Ollama）/ 
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">container</code>（隔离运行，镜像内对端见
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">docker/subagent-peer</code>），
              共用同一份 task_file-jsonl 协议。**wheelhouse 打包未做** ⇒
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">environment.offline_ready</code>
              恒 false（换机仍不能离线安装依赖）。
            </p>
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="能力对照">
        <ul className="space-y-2 text-sm text-slate-400">
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 导出 bundle：真实端点，过密钥闸（不含任何密钥值）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 导入 bundle：真实端点 + 本页导入 UI（非法 bundle 400 不建容器）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 通信反向通道：可选 HTTP 投递 + require_token 入站（默认关闭，仅开启才外呼）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 离线运行：local（本地推理）与 container（镜像内对端）已接线；wheelhouse 打包未做（offline_ready 恒 false）</li>
        </ul>
      </Card>
    </div>
  )
}
