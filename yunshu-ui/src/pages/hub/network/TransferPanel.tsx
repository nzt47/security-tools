/**
 * 网络配置迁移 —— 导出（脱敏）/ 导入（2 条）
 * ------------------------------------------------------------------
 *   GET  /api/network-config/export   导出配置 JSON（api_key 已脱敏为 ***xxxx）
 *   POST /api/network-config/import   导入配置
 *
 * 【两个必须写清楚的事实】
 *   1. 导出是**脱敏**的：api_key 只剩尾号。所以"导出→导入"不是完整备份，
 *      导入后密钥仍需重新填 —— 页面上直说，比事后让人以为配好了强。
 *   2. 导入是整体替换：会覆盖 llm / mcp / search 等分区。
 */
import { useCallback, useState } from 'react'
import { Download, Loader2, Upload } from 'lucide-react'
import { hubGet, hubPost } from '../components/ui'
import { NETWORK_CONFIG_EXPORT, NETWORK_CONFIG_IMPORT } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-40 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

export default function NetworkTransfer() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [text, setText] = useState('')
  const [confirmImport, setConfirmImport] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  /** 先拉一次导出内容填进编辑框：让人看清要导入/导出的是什么，而不是盲贴。 */
  const pullExport = useCallback(async () => {
    setBusy('export'); setErr(''); setMsg('')
    try {
      const r = await hubGet<{ config_json?: string; error?: string }>(NETWORK_CONFIG_EXPORT)
      if ((r as { error?: string })?.error) { setErr('导出失败：' + r.error); return }
      setText(r?.config_json ?? '')
      setMsg('已载入当前配置（api_key 已脱敏）')
    } catch (e) { fail('导出失败', e) } finally { setBusy('') }
  }, [])

  const doImport = async () => {
    setBusy('import'); setErr(''); setMsg('')
    let payload: unknown
    try {
      payload = JSON.parse(text || '{}')
    } catch {
      setErr('配置不是合法 JSON —— 没有提交')
      return
    }
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(NETWORK_CONFIG_IMPORT, { config: payload })
      if (r?.ok === false) { setErr('导入失败：' + (r.error || 'ok=false')); return }
      setMsg(r?.message ?? '已导入网络配置')
      setConfirmImport(false)
    } catch (e) { fail('导入失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Upload size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">配置迁移</h3>
        <span className="text-[10px] text-amber-400">导出是脱敏的（api_key 只剩尾号）；导入是整体替换</span>
        <div className="ml-auto flex items-center gap-1.5">
          <a href={NETWORK_CONFIG_EXPORT} download className={BTN} aria-label="下载网络配置">
            <Download size={11} /> 下载
          </a>
          <button type="button" className={BTN} onClick={() => void pullExport()} disabled={loading} aria-label="载入当前配置">
            {busy === 'export' ? <Loader2 size={11} className="animate-spin" /> : null} 载入当前配置
          </button>
        </div>
      </div>

      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <textarea
        className={INPUT + ' h-40 w-full font-mono'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder='{"llm": {...}, "mcp": {...}, "search": {...}}'
        aria-label="网络配置 JSON"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-[10px] text-slate-400">
          <input type="checkbox" checked={confirmImport} onChange={(e) => setConfirmImport(e.target.checked)} aria-label="确认导入配置" />
          我确认用该配置整体替换现有网络配置
        </label>
        <button type="button" className={BTN_EM} onClick={() => void doImport()} disabled={loading || !confirmImport || text.trim() === ''} aria-label="导入网络配置">
          {busy === 'import' ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />} 导入
        </button>
      </div>
      {text && <pre className={PRE + ' mt-2'}>{text.slice(0, 2000)}</pre>}
    </div>
  )
}
