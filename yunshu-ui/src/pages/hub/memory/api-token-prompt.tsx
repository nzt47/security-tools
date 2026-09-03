/**
 * ApiTokenPrompt —— 受保护写接口 401 时的一站式令牌引导
 * 输入后端 FLASK_API_TOKEN → 经 lib/apiToken 持久化（yunshu_api_token），
 * 之后 hubGet/hubPost(…, getApiToken()) 会自动携带 Authorization: Bearer。
 */
import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { getApiToken, setApiToken } from '../../../lib/apiToken'

const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

export default function ApiTokenPrompt({ onSaved }: { onSaved?: () => void }) {
  const [value, setValue] = useState(getApiToken())
  const save = () => {
    setApiToken(value.trim())
    onSaved?.()
  }
  return (
    <div className="mb-3 rounded-lg border border-amber-800/60 bg-amber-950/20 px-3 py-2.5">
      <div className="flex items-start gap-2 text-[11px] leading-relaxed text-amber-200">
        <KeyRound size={13} className="mt-0.5 shrink-0" />
        <div>
          <div className="font-medium text-amber-100">受保护写接口需要 API 令牌（FLASK_API_TOKEN）</div>
          <p className="text-amber-200/80">
            在此输入后端 .env 的 <code className="text-cyan-300">FLASK_API_TOKEN</code>（仅存本机浏览器）后重试；
            技能启停 / 补说明 / 插件刷新等写操作即会携带令牌。
          </p>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input className={INPUT} type="password" value={value} onChange={(e) => setValue(e.target.value)}
          placeholder="FLASK_API_TOKEN（留空保存=清除）" autoComplete="off" />
        <button type="button"
          className="shrink-0 rounded-md border border-cyan-700/70 px-3 py-1.5 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
          onClick={save}>保存并重试</button>
      </div>
    </div>
  )
}
