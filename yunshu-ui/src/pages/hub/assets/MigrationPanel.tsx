/**
 * 资产迁移 —— 导出 / 恢复 / 删除备份（3 条）
 * ------------------------------------------------------------------
 *   GET    /api/assets/export               导出全部资产为 JSON（浏览器下载）
 *   POST   /api/assets/restore              从指定备份恢复
 *   DELETE /api/assets/backup/<backup_id>   删除指定备份
 *
 * 【为什么恢复要输 backup_id 并勾确认】恢复是**整体覆盖**（按类别写回 assets/*.json）。
 * 一旦选错备份，当前资产就被换掉了 —— 这类动作不能做成"点一下试试"。
 * 备份列表在页面上半部分（后端的备份列表接口），这里只做"拿 id 执行"。
 */
import { useState } from 'react'
import { Download, Loader2, RotateCcw, Trash2 } from 'lucide-react'
import { hubDelete, hubPost } from '../components/ui'
import { ASSETS_EXPORT, ASSETS_RESTORE, assetBackupById } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'
const PRE = 'max-h-40 overflow-auto whitespace-pre-wrap wrap-break-word rounded-sm border border-slate-800 bg-slate-950 p-2 font-mono text-[10px] text-slate-400'

export default function AssetsMigration() {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [backupId, setBackupId] = useState('')
  const [confirmRestore, setConfirmRestore] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const restore = async () => {
    setBusy('restore'); setErr(''); setMsg(''); setDetail(null)
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string; result?: unknown }>(ASSETS_RESTORE, { backup_id: backupId.trim() })
      if (r?.ok === false) { setErr('恢复失败：' + (r.error || 'ok=false')); return }
      setMsg(r?.message ?? '已从备份恢复')
      setDetail((r?.result as Record<string, unknown>) ?? null)
      setConfirmRestore(false)
    } catch (e) { fail('恢复失败', e) } finally { setBusy('') }
  }

  const removeBackup = async () => {
    setBusy('delete'); setErr(''); setMsg(''); setDetail(null)
    try {
      const r = await hubDelete<{ ok?: boolean; error?: string; message?: string }>(assetBackupById(backupId.trim()))
      if (r?.ok === false) { setErr('删除备份失败：' + (r.error || 'ok=false')); return }
      setMsg(r?.message ?? ('备份 ' + backupId + ' 已删除'))
      setConfirmDelete(false)
    } catch (e) { fail('删除备份失败（不存在时后端 404）', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Download size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">备份与迁移</h3>
        <span className="text-[10px] text-slate-500">导出全部资产 / 从备份恢复 / 删除指定备份</span>
        <div className="ml-auto">
          <a href={ASSETS_EXPORT} download className={BTN} aria-label="导出全部资产">
            <Download size={11} /> 导出 JSON
          </a>
        </div>
      </div>

      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {msg && <div className="mb-2 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input
          className={INPUT + ' w-64'}
          value={backupId}
          onChange={(e) => setBackupId(e.target.value)}
          placeholder="backup_id（见上方备份列表）"
          aria-label="备份 id"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-[10px] text-slate-400">
          <input type="checkbox" checked={confirmRestore} onChange={(e) => setConfirmRestore(e.target.checked)} aria-label="确认恢复资产" />
          我确认用该备份覆盖当前资产
        </label>
        <button type="button" className={BTN_EM} onClick={() => void restore()} disabled={loading || !confirmRestore || backupId.trim() === ''} aria-label="恢复资产">
          {busy === 'restore' ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />} 恢复
        </button>
        <label className="ml-3 flex items-center gap-1 text-[10px] text-slate-400">
          <input type="checkbox" checked={confirmDelete} onChange={(e) => setConfirmDelete(e.target.checked)} aria-label="确认删除备份" />
          我确认删除该备份
        </label>
        <button type="button" className={BTN_RED} onClick={() => void removeBackup()} disabled={loading || !confirmDelete || backupId.trim() === ''} aria-label="删除备份">
          {busy === 'delete' ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />} 删除备份
        </button>
      </div>

      {detail && <pre className={PRE + ' mt-2'}>{JSON.stringify(detail, null, 2)}</pre>}
    </div>
  )
}
