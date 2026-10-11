/**
 * CognitiveLoopPanel —— 全景感知页的「认知闭环（元认知）」区
 * ------------------------------------------------------------------
 * 数据源：
 *   GET /api/cognitive/status  可能是 JSON {summary, full, mode, mode_description}
 *                              或纯文本（两处定义并存，故两种形态都要能显示）
 *   GET /api/cognitive/reject  JSON {rejected, reason}
 *
 * 【分批接入】方案 docs/proposals/ui_information_architecture_review_20261011.md §7：
 * cognitive/status 与 cognitive/reject 接进 UI；cognitive/prompt 与 cognitive/translate
 * 属开发/调试面，已在 config/route_ui_intent.json 标注为「不该进」。
 */
import { useCallback, useEffect, useState } from 'react'
import { Brain, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { Badge, Card, ErrorBox, Loading, hubGet } from '../components/ui'
import { COGNITIVE_REJECT, COGNITIVE_STATUS } from '@/api/endpoints'

interface RejectVerdict {
  rejected?: boolean
  reason?: string
}

/** 状态接口可能返回 JSON 或纯文本，统一折成一段可显示文本 */
function asStatusText(d: unknown): string {
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') {
    const o = d as Record<string, unknown>
    if (typeof o.summary === 'string' && o.summary.trim()) return o.summary
    const mode = typeof o.mode === 'string' ? o.mode : ''
    return mode ? `当前行为档：${mode}` : ''
  }
  return ''
}

export default function CognitiveLoopPanel() {
  const [statusText, setStatusText] = useState('')
  const [verdict, setVerdict] = useState<RejectVerdict | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setError('')
    Promise.allSettled([
      hubGet<unknown>(COGNITIVE_STATUS),
      hubGet<RejectVerdict>(COGNITIVE_REJECT),
    ]).then(([s, r]) => {
      if (s.status === 'fulfilled') setStatusText(asStatusText(s.value))
      if (r.status === 'fulfilled') {
        setVerdict(r.value && typeof r.value === 'object' ? r.value : null)
      }
      if (s.status === 'rejected' && r.status === 'rejected') {
        setError('认知闭环接口均不可用')
      }
      setLoading(false)
    })
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <Card title="认知闭环（元认知）">
      <div className="mb-3 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs text-slate-500">
          <Brain size={13} /> /api/cognitive/status · /api/cognitive/reject
        </span>
        <button onClick={load}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {error && <div className="mb-3"><ErrorBox message={error} /></div>}
      {loading ? <Loading /> : (
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400">状态摘要</div>
            {statusText ? (
              <pre data-testid="cognitive-status"
                className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300">
                {statusText}
              </pre>
            ) : (
              <div className="text-xs text-slate-500">（后端未返回状态摘要）</div>
            )}
          </div>
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400">任务接受度裁决</div>
            {verdict ? (
              <div data-testid="cognitive-reject" className="flex items-center gap-2 text-sm">
                {verdict.rejected
                  ? <Badge color="red"><ShieldAlert size={12} /> 拒绝</Badge>
                  : <Badge color="green"><ShieldCheck size={12} /> 接受</Badge>}
                <span className="text-slate-400">{verdict.reason || '（无理由）'}</span>
              </div>
            ) : (
              <div className="text-xs text-slate-500">（后端未返回裁决）</div>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
