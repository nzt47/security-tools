/**
 * 分身行操作 —— 详情 / 热更新 / 离线包（3 条）
 * ------------------------------------------------------------------
 *   GET  /api/subagent/<name>             分身详情
 *   POST /api/subagent/<name>/reload      热更新配置
 *   GET  /api/subagent/<name>/offline_pack 下载预构建离线包（tar.gz）
 *
 * 【为什么不放"执行"按钮】后端 api_subagent_execute 的 docstring 明写：
 * 「本端点走 SubagentContainer.execute() —— 设计上保持"占位骨架"（不调 LLM、
 * 不做委派协议），只回一段占位文案，供集成测试验证容器链路。需要真执行请用
 * /api/subagent/<name>/delegate」。给占位骨架做按钮等于让用户以为分身真的干了活。
 * 真执行入口是「委派」（delegate），已在组装台里接线。
 */
import { useState } from 'react'
import { Download, Info, Loader2, RefreshCw } from 'lucide-react'
import { Button, CodeBlock, hubGet, hubPost, Notice } from '../components/ui'
import { subagentByName, subagentOfflinePack, subagentReload } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-micro text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'

export default function SubagentRowActions({ name }: { name: string }) {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [confirmReload, setConfirmReload] = useState(false)

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))

  const openDetail = async () => {
    setBusy('detail'); setErr(''); setMsg(''); setDetail(null)
    try {
      const r = await hubGet<Record<string, unknown>>(subagentByName(name))
      setDetail(r)
    } catch (e) { fail('读取分身详情失败', e) } finally { setBusy('') }
  }

  const reload = async () => {
    setBusy('reload'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; message?: string }>(subagentReload(name), {})
      if (r?.ok === false) { setErr('热更新失败：' + (r.error || 'ok=false')); return }
      setMsg('已热更新：' + name + (r?.message ? '（' + r.message + '）' : ''))
      setConfirmReload(false)
    } catch (e) { fail('热更新失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center justify-end gap-1">
        <Button type="button" variant="default" size="xs" onClick={() => void openDetail()} disabled={loading} aria-label={'分身详情 ' + name}>
          {busy === 'detail' ? <Loader2 size={10} className="animate-spin" /> : <Info size={10} />} 详情
        </Button>
        <Button
          type="button"
          variant="default" size="xs"
          onClick={() => setConfirmReload((v) => !v)}
          disabled={loading}
          aria-label={'热更新 ' + name}
        >
          <RefreshCw size={10} /> 热更新
        </Button>
        {/* 离线包是文件下载：直连端点，不经 hubGet 的 JSON 解析 */}
        <a href={subagentOfflinePack(name)} download className={BTN} aria-label={'下载离线包 ' + name}>
          <Download size={10} /> 离线包
        </a>
      </div>

      {confirmReload && (
        <div className="flex flex-wrap items-center justify-end gap-1 rounded-sm border border-amber-900/50 bg-amber-500/5 px-1.5 py-1">
          <span className="text-micro text-amber-300">热更新会重载该分身的配置（进行中的任务不受影响）。确定？</span>
          <Button type="button" variant="default" size="xs" onClick={() => void reload()} disabled={loading} aria-label={'确认热更新 ' + name}>
            {busy === 'reload' ? <Loader2 size={10} className="animate-spin" /> : null} 确认
          </Button>
          <Button type="button" variant="default" size="xs" onClick={() => setConfirmReload(false)}>取消</Button>
        </div>
      )}

      {err && <Notice tone="danger" size="sm" className="text-micro">{err}</Notice>}
      {msg && <Notice tone="success" size="sm" className="text-micro">{msg}</Notice>}
      {detail && (
        <div>
          <div className="mb-1 flex items-center justify-end">
            <Button type="button" variant="default" size="xs" onClick={() => setDetail(null)}>关闭详情</Button>
          </div>
          <CodeBlock maxH={224}>{JSON.stringify(detail, null, 2)}</CodeBlock>
        </div>
      )}
    </div>
  )
}
