/**
 * 装配车间 —— 系统自我复制 / 可带走 bundle（S5）
 * ------------------------------------------------------------------
 * 【它解决什么】本页此前是纯说明页（零 API 调用），而"可带走"这件事**只有后端知道**
 * （bundle 契约 + 密钥闸 + 导出端点）。本页改为**真实导出面**：填分身名 → 调
 * GET /api/subagent/<name>/bundle → 展示可复制的 JSON；失败如实显示错误。
 *
 * 【为什么不在前端拼 bundle】契约的唯一权威是 agent/subagent/bundle.py；前端自己
 * 拼一份就是第二份口径（那正是本仓反复记录的漂移形态）。本页只做"发起 + 展示"。
 *
 * 【如实标注】导入端点（POST /api/subagent/import）已实现但**本页没有导入 UI**；
 * 离线运行（本地推理 / 依赖打包 / container 后端）**未做** —— 都写在页面上，
 * 不假装已接线。
 */
import { useState } from 'react'
import type { KeyboardEvent } from 'react'
import { ClipboardCopy, Loader2, Package, RefreshCw, ShieldAlert } from 'lucide-react'
import { Card, ErrorBox, PageHeader } from '../components/ui'
import { request } from '@/lib/apiClient'
import { SUBAGENT_BUNDLE_BY_NAME, SUBAGENT_IMPORT } from '@/api/endpoints'

/** 导出响应（后端 jsonify({"ok": True, "bundle": ...})；兼容统一信封的 data 包法） */
interface BundleEnvelope {
  bundle?: unknown
  data?: { bundle?: unknown }
}

export default function WorkshopReplicate() {
  const [name, setName] = useState('')
  const [bundle, setBundle] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const text = bundle === null ? '' : JSON.stringify(bundle, null, 2)

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

  return (
    <div className="p-6">
      <PageHeader
        title="系统自我复制"
        description="装配车间 —— 可带走 bundle：导出为真实端点，导入与离线运行如实标注"
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

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="导入（接口已通，界面未做）">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400"><ShieldAlert size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              导入端点 <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">POST {SUBAGENT_IMPORT}</code>
              已实现（非法 bundle 400 且不建容器），但本页**没有**导入 UI —— 如实标注，不假装已接线。
              导入后按同一份配置可再导出（幂等）。
            </p>
          </div>
        </Card>

        <Card title="离线运行（未做）">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-500/15 text-slate-400"><RefreshCw size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              bundle 的 <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">runtime.backend</code>
              目前只映射到既有 <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">inproc</code> /
              <code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">subprocess</code> 两档，
              共用同一份 task_file-jsonl 协议；本地推理 / 离线依赖打包与 container 后端**未做**。
            </p>
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="能力对照">
        <ul className="space-y-2 text-sm text-slate-400">
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 导出 bundle：真实端点，过密钥闸（不含任何密钥值）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 导入 bundle：<code className="text-cyan-400">POST /api/subagent/import</code>（界面未做）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-slate-600" /> 通信反向通道：单发 delegate 为同步响应，分身→母体的异步反向通道未做</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-slate-600" /> 离线运行：本地推理 / 依赖打包未做</li>
        </ul>
      </Card>
    </div>
  )
}
